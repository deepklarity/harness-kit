"""History-driven agent suggester.

Odin picks which agent/model to assign to a task by combining two
inputs:

1. **A static priority list** (``OdinConfig.model_routing``) — the
   deterministic, hand-tuned fallback that knows nothing about
   actual merged-task outcomes. Default First; survives cold start.

2. **Measured history** (per-agent success rate + median token cost
   computed from board data) — the dynamic layer that learns which
   agents deliver DONE tasks without operator takeover. Lives in
   ``tasks.agent_stats`` on the backend side; exposed via
   ``/boards/{id}/agent-stats/`` and consumed here.

The two layers compose: the suggester ranks viable candidates using
the measured history when there is enough of it, and falls back to
the static ordering when the history is thin or empty. Default First
— the static ordering is the override layer, but history is what we
actually want to follow when we have signal.

This module is **pure**: no I/O, no Django, no HTTP. The orchestrator
fetches the stats once before routing and passes them in. Tests
exercise the ranking logic with synthetic history in milliseconds.

Wiring: ``Orchestrator._route_task`` consumes
:func:`suggest_routing` in Phase 2 (tier distribution). Phase 1
(planner-suggested agent) still wins outright — the planner's
per-task judgment overrides the historical aggregate for that task.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .models import AgentConfig, CostTier


# ── Sensible defaults ────────────────────────────────────────────────


#: Minimum merged samples for history to be considered meaningful.
#: 5 matches the small-N floor used elsewhere in the routing
#: pipeline (see wave-2 audit: ~tens of observations per agent, and
#: anything below 5 is dominated by noise).
DEFAULT_MIN_SAMPLES: int = 5

#: A candidate must clear this success-rate threshold to be considered
#: "history-capable" — below it, the recommend is to escalate (or, if
#: no agent qualifies anywhere, fall back to static).
DEFAULT_SUCCESS_THRESHOLD: float = 0.5

#: Cap on sample size we honour from history. Once an agent has more
#: than this many merges the rolling sample is representative enough;
#: reading the whole corpus just makes routing slower without
#: changing the ranking.
MAX_SAMPLES_PER_AGENT: int = 200


# ── Wire format ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentStats:
    """Per-agent rollup over observed merged tasks.

    Mirrors ``tasks.agent_stats.AgentStats`` (backend) but lives here
    in the odin package so the suggester can be exercised without
    Django. Kept field-compatible so JSON from
    ``/boards/{id}/agent-stats/`` deserializes directly via
    ``AgentStats(**row)`` — see :func:`stats_from_dicts`.

    Fields:
      name           short agent name (e.g. "gemini", "claude")
      sample_count   number of merged tasks observed
      success_count  number of merges agent drove to DONE un-assisted
      success_rate   successes / sample_count (0.0–1.0)
      median_tokens  median token spend (0 when capture is missing —
                     caller must surface this as a gap, not a cost
                     reduction)
    """

    name: str
    sample_count: int
    success_count: int
    success_rate: float
    median_tokens: float

    @classmethod
    def from_dict(cls, data: Dict) -> "AgentStats":
        """Build from a REST endpoint row (string-indexed fields)."""
        return cls(
            name=str(data["name"]),
            sample_count=int(data["sample_count"]),
            success_count=int(data["success_count"]),
            success_rate=float(data["success_rate"]),
            median_tokens=float(data["median_tokens"]),
        )


@dataclass(frozen=True)
class SuggestionInput:
    """Inputs the suggester needs to rank candidates.

    Bundled into a single value object so the orchestrator can build
    it once (with cached stats + tier info) and pass it down, instead
    of ballooning the suggester signature as we add signals.
    """

    candidates: List[str]
    stats_by_name: Dict[str, AgentStats]
    min_samples: int = DEFAULT_MIN_SAMPLES
    success_threshold: float = DEFAULT_SUCCESS_THRESHOLD


def stats_from_dicts(rows: Sequence[Dict]) -> Dict[str, AgentStats]:
    """Map a list of REST rows to ``{agent_name: AgentStats}``.

    Used by the orchestrator's ``fetch_agent_stats`` loader — accepts
    the JSON shape from ``/boards/{id}/agent-stats/`` directly.
    Rows whose ``name`` is missing or empty are dropped (caller
    shouldn't have produced them; defensive).
    """
    out: Dict[str, AgentStats] = {}
    for row in rows or ():
        name = (row or {}).get("name") if isinstance(row, dict) else None
        if not name:
            continue
        out[str(name)] = AgentStats.from_dict(row)
    return out


# ── Decision shapes ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _BaseDecision:
    """Shared fields between the two decision types."""

    min_samples: int
    threshold: float
    reason: str  # operator-facing summary; the routing_reasoning

    def __str__(self) -> str:  # pragma: no cover — debug helper
        return f"{type(self).__name__}({self.reason!r})"


@dataclass(frozen=True)
class RoutingDecision(_BaseDecision):
    """A history-driven pick.

    Exposes the picked agent plus the ranked table so the operator
    can audit any single routing decision ("why gemini and not qwen?").
    """

    picked: str
    # All eligible candidates ordered by rank — first entry is the
    # pick, the rest is the audit table rendered into
    # routing_reasoning.
    ranked: List[str] = field(default_factory=list)

    @property
    def rule(self) -> str:
        return "history"


@dataclass(frozen=True)
class StaticFallback(_BaseDecision):
    """No history-driven pick — caller must use the existing static
    tier distribution (random.choice within the cheapest viable tier).

    Carries the candidates forward so the caller doesn't have to keep
    them on the side, and renders a clear ``rule="static"`` so the
    routing_reasoning reflects what actually ran.
    """

    candidates: List[str] = field(default_factory=list)

    @property
    def picked(self) -> Optional[str]:
        """No picked agent — caller chooses (random.choice on tier)."""
        return None

    @property
    def rule(self) -> str:
        return "static"


# ── Suggester core ───────────────────────────────────────────────────


def suggest_routing(
    candidates: Sequence[str],
    stats_by_name: Dict[str, AgentStats],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    success_threshold: float = DEFAULT_SUCCESS_THRESHOLD,
) -> _BaseDecision:
    """Return a history-driven pick, or a StaticFallback signal.

    Algorithm:

    1. Reduce the candidate list to agents with ≥ ``min_samples``
       observed merges. Agents with thin history are excluded from
       ranking (not auto-failed — that would be misreading the gap).
    2. If no candidate clears the sample floor → StaticFallback.
    3. Of those that remain, compute ``success_rate`` per
       ``AgentStats``. Disqualify any below ``success_threshold``.
    4. If everyone is below threshold → StaticFallback (Default
       First — do not pin an obviously-failing cheap agent).
    5. Among qualifiers, rank by ``success_rate`` DESC, then
       ``median_tokens`` ASC (cheaper agents win ties), then name
       ASC (deterministic for testing).
    6. Pick the top of the ranked list. Wrap as a RoutingDecision
       with the full ranked table for audit.

    The function never raises on empty input — it always returns
    a decision. A StaticFallback's ``picked`` is ``None`` so the
    orchestrator knows to fall back to random.choice on the
    supplied ``candidates``.
    """
    # Step 1: filter to agents with enough samples.
    eligible = [
        name for name in candidates
        if _has_enough_samples(stats_by_name.get(name), min_samples)
    ]

    # Step 2: thin-history path.
    if not eligible:
        return StaticFallback(
            min_samples=min_samples,
            threshold=success_threshold,
            reason=(
                f"thin history (need >= {min_samples} samples/agent); "
                f"falling back to static tier distribution"
            ),
            candidates=list(candidates),
        )

    # Step 3-4: drop sub-threshold qualifiers.
    qualifying = sorted(
        (
            name for name in eligible
            if _clears_threshold(stats_by_name.get(name), success_threshold)
        ),
        # Deterministic rank so tests don't flake.
        key=lambda n: (
            -stats_by_name[n].success_rate,
            stats_by_name[n].median_tokens,
            n,
        ),
    )

    if not qualifying:
        return StaticFallback(
            min_samples=min_samples,
            threshold=success_threshold,
            reason=(
                f"no candidate cleared success_threshold="
                f"{success_threshold:.2f}; "
                f"falling back to static tier distribution"
            ),
            candidates=list(candidates),
        )

    # Step 5-6: pick.
    picked = qualifying[0]
    ranked_names = list(qualifying) + [
        name for name in candidates if name not in qualifying
    ]

    top_stats = stats_by_name[picked]
    runner_up_name = qualifying[1] if len(qualifying) > 1 else None
    if runner_up_name:
        runner_up = stats_by_name[runner_up_name]
        reason = (
            f"history: picked {picked}/"
            f"(success_rate={top_stats.success_rate:.2f}, "
            f"median_tokens={top_stats.median_tokens:.0f}); "
            f"runner-up {runner_up_name}/"
            f"({runner_up.success_rate:.2f}, "
            f"{runner_up.median_tokens:.0f}) "
            f"from {len(qualifying)} qualifying of {len(candidates)} candidates"
        )
    else:
        reason = (
            f"history: picked {picked}/"
            f"(success_rate={top_stats.success_rate:.2f}, "
            f"median_tokens={top_stats.median_tokens:.0f}); "
            f"only qualifier of {len(candidates)} candidates"
        )

    return RoutingDecision(
        picked=picked,
        ranked=ranked_names,
        min_samples=min_samples,
        threshold=success_threshold,
        reason=reason,
    )


def _has_enough_samples(
    stats: Optional[AgentStats], min_samples: int
) -> bool:
    """True iff ``stats`` represents a trustworthy sample size."""
    return stats is not None and stats.sample_count >= min_samples


def _clears_threshold(
    stats: Optional[AgentStats], threshold: float
) -> bool:
    """True iff ``stats`` meets the success-rate bar.

    Agents without stats don't clear the threshold — they shouldn't
    be picked on faith.
    """
    if stats is None or stats.sample_count == 0:
        return False
    return stats.success_rate >= threshold


def static_fallback_pick(
    candidates: Sequence[str],
    *,
    seed: Optional[int] = None,
) -> str:
    """Pick uniformly at random among ``candidates``.

    The orchestrator uses this when :func:`suggest_routing` returns a
    StaticFallback. ``seed`` is exposed so tests can pin the random
    sequence; production callers leave it None to use system entropy
    (preserving the prior distribute-within-cheapest-tier behavior).
    """
    if not candidates:
        raise ValueError("static_fallback_pick requires at least one candidate")
    if seed is None:
        return random.choice(list(candidates))
    rng = random.Random(seed)
    return rng.choice(list(candidates))


# ── Tier helpers (used by the orchestrator) ──────────────────────────


def cheapest_viable_tier(
    candidates: Sequence[str],
    tier_of: Dict[str, CostTier],
) -> Dict[str, str]:
    """Return ``{agent: tier_name}`` for the cheapest viable tier only.

    The orchestrator groups viable routes by tier, picks the cheapest
    tier with at least one candidate, then asks the suggester to
    rank the names inside that bucket. Bucketing is unchanged from
    the prior behavior — this helper just pulls the existing logic
    into a pure function so tests can pin it.
    """
    tier_order = {CostTier.LOW.value: 0, CostTier.MEDIUM.value: 1, CostTier.HIGH.value: 2}
    bucketed: Dict[str, List[str]] = {"low": [], "medium": [], "high": []}
    for name in candidates:
        tier = tier_of.get(name)
        if tier is None:
            continue
        tier_name = tier.value if hasattr(tier, "value") else str(tier)
        bucketed.setdefault(tier_name, []).append(name)

    viable_tiers = [
        (tier_order.get(t, 99), t, names)
        for t, names in bucketed.items() if names
    ]
    if not viable_tiers:
        return {}
    cheapest = min(viable_tiers, key=lambda r: r[0])
    return {name: cheapest[1] for name in cheapest[2]}
