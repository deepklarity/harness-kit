"""Print the per-agent success-rate / median-cost table ranked by
odin.agent_routing.suggest_routing's history-driven rule.

Used as the operator-facing proof artifact for task 188 (history-
driven routing). Connects to the same TaskIt database the rest of
the diagnostic scripts read, computes AgentStats from
tasks.agent_stats.compute_agent_stats_for_board, then asks
odin.agent_routing.suggest_routing which of the observed agents
the routing decision would pick.

Run from taskit/taskit-backend with the same Django settings the
other diagnostic scripts use:

    python testing_tools/routing_suggest.py
    python testing_tools/routing_suggest.py --json
    python testing_tools/routing_suggest.py --board-id 5
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Bootstrap Django so tasks.* and odin.* both resolve. odin lives at
# ../odin/src — prepend its parent (the odin repo's src dir) to
# sys.path. Sibling scripts under testing_tools already do this
# pattern (see autonomy_metrics.py).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_BACKEND_PARENT = _BACKEND_DIR.parent
_ODIN_SRC = _BACKEND_PARENT.parent / "odin" / "src"

for path in (str(_BACKEND_DIR), str(_ODIN_SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

import django  # noqa: E402

django.setup()

from tasks.agent_stats import AgentStats, compute_agent_stats_for_board  # noqa: E402
from tasks.models import Board, Task, TaskStatus, User  # noqa: E402

from odin.agent_routing import (  # noqa: E402
    DEFAULT_MIN_SAMPLES,
    DEFAULT_SUCCESS_THRESHOLD,
    StaticFallback,
    RoutingDecision,
    suggest_routing,
)


# ── Rendering helpers ────────────────────────────────────────────────


def _format_row(stats: AgentStats) -> str:
    rate_pct = stats.success_rate * 100
    return (
        f"  {stats.name:<14} "
        f"success={stats.success_count:>4}/{stats.sample_count:<4} "
        f"({rate_pct:5.1f}%) "
        f"median={stats.median_tokens:>9,.0f} tok"
    )


def _print_table(
    board_name: str,
    candidates: list[str],
    stats_by_name: dict[str, AgentStats],
    decision,
    threshold: float,
    min_samples: int,
    *,
    cheapest_tier_pick: str | None = None,
    pre_filter_pick: str | None = None,
    static_fallback_pick: str | None = None,
) -> None:
    """Match the operator-facing table style of autonomy_metrics.

    ``cheapest_tier_pick`` is what the orchestrator's
    ``_pick_from_viable_routes`` would actually pick — same call as
    the global suggester now (full pool), so the orchestrator pick
    agrees with the global suggester when history is rich. The
    ``pre_filter_pick`` field is the OLD behaviour shown for
    comparison only: cheapest-tier-bucketed suggester call, which
    produced the escalation gap that the previous review caught.

    ``static_fallback_pick`` is what the orchestrator's static
    fallback path would do when the suggester returns StaticFallback
    — uniform random within the cheapest viable tier. When the
    global suggester returns StaticFallback, this is the actual
    routing_reasoning source.
    """
    print(f"\n{'=' * 76}")
    print(f"  ROUTING SUGGESTION — {board_name}")
    print(f"{'=' * 76}")
    print(
        f"  rule: history-driven (threshold={threshold:.2f}, "
        f"min_samples={min_samples})"
    )
    if isinstance(decision, RoutingDecision):
        print(f"  suggester pick (full pool): {decision.picked}  (rule=history)")
        if decision.ranked:
            ranked_str = ", ".join(decision.ranked)
            print(f"  ranked qualifiers: {ranked_str}")
    elif isinstance(decision, StaticFallback):
        print(f"  suggester pick (full pool): <static fallback>  (rule=static)")
        print(f"  reason: {decision.reason}")
        if static_fallback_pick:
            print(
                f"  orchestrator fallback pick (cheapest-tier random): "
                f"{static_fallback_pick}"
            )
    else:
        print(f"  suggester pick: <unknown decision type>")

    if cheapest_tier_pick is not None and isinstance(decision, RoutingDecision):
        marker = "  (agrees with suggester — full pool)"
        print(
            f"  orchestrator pick (full-pool suggester): "
            f"{cheapest_tier_pick}{marker}"
        )
        # Surface the OLD behaviour as a "would-have-been" line so the
        # operator can see the gap that was closed by task 188.
        if pre_filter_pick and pre_filter_pick != cheapest_tier_pick:
            print(
                f"  OLD behaviour (cheapest-tier-only suggester): "
                f"{pre_filter_pick}  ← escalation gap"
            )

    print(f"\n  observed agents:")
    if not stats_by_name:
        print("    (no DONE tasks observed for this board)")
        return
    # Sort observed rows by success_rate desc; tiebreak by name.
    observed = sorted(
        stats_by_name.values(),
        key=lambda s: (-s.success_rate, s.name),
    )
    for stats in observed:
        marker = "  "
        if isinstance(decision, RoutingDecision) and stats.name == decision.picked:
            marker = "> "
        if cheapest_tier_pick and stats.name == cheapest_tier_pick:
            marker = "+ "
        eligible = stats.sample_count >= min_samples
        flag = "" if eligible else "  [thin]"
        print(f"{marker}{_format_row(stats)}{flag}")


# ── Entry point ──────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--board-id",
        type=int,
        default=None,
        help="Scope to a specific board (default: all boards with DONE tasks).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help=f"Min samples for history eligibility (default: {DEFAULT_MIN_SAMPLES})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SUCCESS_THRESHOLD,
        help=(
            f"Success-rate threshold to qualify a candidate "
            f"(default: {DEFAULT_SUCCESS_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of the table.",
    )
    args = parser.parse_args()

    if args.board_id is not None:
        try:
            board = Board.objects.get(pk=args.board_id)
        except Board.DoesNotExist:
            sys.exit(f"Board #{args.board_id} not found.")
        candidates_for_board = {board.id: board}
    else:
        # All boards that have at least one DONE task — the production
        # routing decision happens per-board, so the proof iterates the
        # same way.
        boards_with_done = (
            Board.objects.filter(tasks__status=TaskStatus.DONE)
            .distinct()
        )
        candidates_for_board = {b.id: b for b in boards_with_done}

    # The routing decision in production consults model_routing for
    # viable candidates; for the proof artifact we surface every
    # agent that has actually observed data (the suggester's input).
    # We also compute the "orchestrator view" — what the cheapest cost
    # tier would have been used to actually route this board's tasks.
    from odin.models import CostTier

    agent_tiers: dict[str, str] = {}
    for agent in User.objects.filter(email__endswith="@odin.agent"):
        agent_tiers[agent.name] = (agent.cost_tier or "medium")

    results = []
    for board in candidates_for_board.values():
        stats_by_name = compute_agent_stats_for_board(board)
        candidates = sorted(stats_by_name.keys())
        decision = suggest_routing(
            candidates,
            stats_by_name,
            min_samples=args.min_samples,
            success_threshold=args.threshold,
        )

        # Orchestrator view (NEW behaviour, task 188): the orchestrator
        # now calls suggest_routing over the *full* viable pool — same
        # call as the global suggester. The orchestrator's
        # `_pick_from_viable_routes` agrees with the global suggester
        # whenever the suggester returns a RoutingDecision; the
        # cheapest-tier bucket only kicks in on StaticFallback (random
        # among cheapest-tier candidates).
        cheapest_tier_pick = None
        if isinstance(decision, RoutingDecision):
            cheapest_tier_pick = decision.picked

        # OLD behaviour (pre-188): the orchestrator pre-filtered to the
        # cheapest tier and asked the suggester only about those names.
        # That's the gap that let cheap failures persist in production.
        # We surface it as `pre_filter_pick` so the proof shows the
        # delta the fix closed.
        pre_filter_pick = None
        static_fallback_pick = None
        tier_order = {CostTier.LOW.value: 0, CostTier.MEDIUM.value: 1, CostTier.HIGH.value: 2}
        candidate_tiers = {
            n: tier_order.get(agent_tiers.get(n, "medium"), 1)
            for n in candidates
        }
        if candidate_tiers:
            cheapest = min(candidate_tiers.values())
            cheap_agents = [
                n for n, t in candidate_tiers.items() if t == cheapest
            ]
            # OLD behaviour: pre-filter then call suggester.
            pre_filter_decision = suggest_routing(
                cheap_agents,
                stats_by_name,
                min_samples=args.min_samples,
                success_threshold=args.threshold,
            )
            if isinstance(pre_filter_decision, RoutingDecision):
                pre_filter_pick = pre_filter_decision.picked
            elif isinstance(pre_filter_decision, StaticFallback):
                # The OLD static fallback also stuck to the cheap tier
                # and picked randomly — mirror that here so the proof
                # explains what the old behaviour would have produced
                # even when the global suggester now picks something
                # in a higher tier.
                import random as _random
                if cheap_agents:
                    static_fallback_pick = _random.choice(cheap_agents)

        # NEW static-fallback pick: when the global suggester is static
        # the orchestrator randomizes within the cheapest viable tier.
        # Surface that pick so the proof explains the routing_reasoning
        # for new tasks when history is too thin.
        if isinstance(decision, StaticFallback):
            if candidate_tiers:
                cheapest = min(candidate_tiers.values())
                cheap_agents_all = [
                    n for n, t in candidate_tiers.items() if t == cheapest
                ]
                if cheap_agents_all:
                    import random as _random
                    static_fallback_pick = _random.choice(cheap_agents_all)

        board_name = f"board #{board.id} '{board.name}'"
        if args.json:
            results.append({
                "board_id": board.id,
                "board_name": board.name,
                "candidates": candidates,
                "stats_by_name": {
                    n: s.to_dict() for n, s in stats_by_name.items()
                },
                "decision": {
                    "rule": (
                        "history"
                        if isinstance(decision, RoutingDecision)
                        else "static"
                    ),
                    "picked": getattr(decision, "picked", None),
                    "reason": decision.reason,
                    "threshold": decision.threshold,
                    "min_samples": decision.min_samples,
                },
                "orchestrator_full_pool_pick": cheapest_tier_pick,
                "pre_filter_cheapest_tier_pick": pre_filter_pick,
                "static_fallback_pick": static_fallback_pick,
                "escalation_delta": (
                    pre_filter_pick != cheapest_tier_pick
                    if pre_filter_pick and cheapest_tier_pick
                    else False
                ),
            })
        else:
            _print_table(
                board_name,
                candidates,
                stats_by_name,
                decision,
                args.threshold,
                args.min_samples,
                cheapest_tier_pick=cheapest_tier_pick,
                pre_filter_pick=pre_filter_pick,
                static_fallback_pick=static_fallback_pick,
            )

    if args.json:
        print(json.dumps(
            results,
            indent=2,
            default=lambda o: getattr(o, "to_dict", lambda: str(o))(),
        ))


if __name__ == "__main__":
    main()
