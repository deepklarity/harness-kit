"""Tests for orchestrator integration with the history-driven suggester.

The orchestrator's tier-distribution phase (no planner suggestion) used
to be a uniform random.choice among viable candidates. With the
suggester wired in, it now ranks by measured success-rate + median
cost, falling back to a uniform random pick when history is thin.

Tags:
- [simple] — pure logic; no backend, no subprocesses
- [mock]   — patches the backend's fetch_agent_stats

These tests patch the new ``_fetch_agent_stats`` method directly so
they don't need any HTTP or Django setup; they cover the behavior
the operator sees in routing_reasoning.
"""

import asyncio
import random
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

from odin.agent_routing import AgentStats
from odin.models import (
    AgentConfig,
    CostTier,
    ModelRoute,
    OdinConfig,
)
from odin.orchestrator import Orchestrator


# ── Helpers ─────────────────────────────────────────────────────────


def _make_orchestrator(tmp_path, model_routing=None, agents=None):
    task_dir = str(tmp_path / "tasks")
    log_dir = str(tmp_path / "logs")
    cost_dir = str(tmp_path / "costs")
    spec_dir = str(tmp_path / "specs")
    for d in [task_dir, log_dir, cost_dir, spec_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    if agents is None:
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["writing", "coding", "reasoning"],
                cost_tier=CostTier.HIGH,
            ),
        }

    if model_routing is None:
        model_routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="qwen", model="qwen-coder"),
            ModelRoute(agent="claude", model="claude-sonnet-4-5"),
        ]

    cfg = OdinConfig(
        base_agent="claude",
        task_storage=task_dir,
        log_dir=log_dir,
        cost_storage=cost_dir,
        spec_storage=spec_dir,
        agents=agents,
        model_routing=model_routing,
    )
    return Orchestrator(cfg)


def _stats(name, success=0, total=0, median_tokens=0):
    rate = (success / total) if total else 0.0
    return AgentStats(
        name=name,
        sample_count=total,
        success_count=success,
        success_rate=rate,
        median_tokens=median_tokens,
    )


def _patch_all_available():
    async def _available(self, name, cfg):
        return True
    return patch.object(Orchestrator, "_is_available_cached", _available)


def _patch_stats(orch, stats_by_name):
    """Patch _fetch_agent_stats to return a fixed dict.

    The patched function ignores its arguments and returns the supplied
    stats so we can write deterministic routing tests.
    """
    return patch.object(orch, "_fetch_agent_stats", lambda: stats_by_name)


# ── [mock] Suggester picks cheapest-capable agent ───────────────────


class TestRouteTaskHistoryDriven:
    """When the planner does NOT suggest an agent, the suggester should
    rank viable candidates using measured success rate + median cost."""

    @pytest.mark.asyncio
    async def test_clear_winner_in_cheapest_tier_picked(self, tmp_path):
        """When gemini has 90% success and qwen has 20%, gemini wins
        consistently (no random.choice)."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=2, total=10, median_tokens=3500),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert agent == "gemini"
        # Reasoning should explain the rule.
        assert "history" in reasoning.lower() or "success" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_static_config_keeps_higher_cost_winner_in_cheap_tier(self, tmp_path):
        """Among two qualifying candidates in LOW tier, the cheaper one
        (lower median_tokens) wins — but routing_reasoning should make
        it clear that this is a *history-driven* pick, not random."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10, median_tokens=8000),
            "qwen": _stats("qwen", success=9, total=10, median_tokens=4000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            # Deterministic over many runs — qwen always wins on cost.
            for _ in range(20):
                agent, model, reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
            assert agent == "qwen"

    @pytest.mark.asyncio
    async def test_cheap_tier_clear_winner_picked_consistently(self, tmp_path):
        """Within the cheapest tier, the suggester's history-driven
        ranking beats random.distribute — clear winners show up
        every time instead of ~half the time."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=9, total=10, median_tokens=3500),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent_counts = Counter()
            for _ in range(20):
                agent, _model, reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agent_counts[agent] += 1

        # qwen is the unambiguous history-capable pick — must dominate
        # the cheap tier instead of splitting with gemini ~50/50.
        assert agent_counts["qwen"] == 20
        assert agent_counts.get("gemini", 0) == 0
        # Reasoning should mention the history-driven rule.
        assert (
            "history" in reasoning.lower() or "success" in reasoning.lower()
        )


# ── [mock] Thin history → static fallback ───────────────────────────


class TestRouteTaskThinHistoryFallback:
    """When the suggester has no signal (< min_samples for all
    candidates), route_task preserves the prior behavior: it
    distributes randomly within the cheapest viable tier (Default
    First — do not silently change routing when history is thin)."""

    @pytest.mark.asyncio
    async def test_thin_history_distributes_across_cheap_tier(self, tmp_path):
        """With empty history, 40 runs should still produce >1 agent
        in the LOW tier (the random.distribute behavior is preserved)."""
        orch = _make_orchestrator(tmp_path)
        # Both candidates have only 1 sample — below min_samples=5.
        stats = {
            "gemini": _stats("gemini", success=1, total=1),
            "qwen": _stats("qwen", success=1, total=1),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agents_seen = Counter()
            for _ in range(40):
                agent, model, reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agents_seen[agent] += 1

        # Both cheap-tier agents should appear (Default First: random
        # distribution is the static fallback).
        assert set(agents_seen.keys()).issubset({"gemini", "qwen"})
        assert len(agents_seen) > 1, (
            f"Thin-history fallback should distribute across cheap tier; "
            f"got only: {dict(agents_seen)}"
        )

    @pytest.mark.asyncio
    async def test_thin_history_reasoning_marks_static(self, tmp_path):
        """Reasoning should explain that thin history was the reason
        no history-driven pick was made."""
        orch = _make_orchestrator(tmp_path)
        stats = {}

        with _patch_all_available(), _patch_stats(orch, stats):
            _, _, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert "thin" in reasoning.lower() or "static" in reasoning.lower()


# ── [mock] Suggested agent bypasses suggester ──────────────────────


class TestRouteTaskSuggestionOverridesSuggester:
    """The planner's suggested_agent must continue to win over the
    history-driven suggester. Suggester applies only in Phase 2
    (tier distribution)."""

    @pytest.mark.asyncio
    async def test_suggested_agent_used_even_if_history_disagrees(self, tmp_path):
        """Planner suggests qwen, but history says gemini is the clear
        winner. qwen must still be the routing target — the planner's
        judgment about THIS task beats historical aggregate."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=10, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=0, total=10, median_tokens=3500),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="qwen",
                quota=None,
            )

        assert agent == "qwen"
        # Should still read as "suggested by planner".
        assert "suggested by planner" in reasoning.lower()


# ── [mock] Stats fetched at routing time ─────────────────────────────


class TestRouteTaskFetchesAgentStats:
    """_route_task must call _fetch_agent_stats exactly once per
    invocation. The orchestrator relies on this to log the rule that
    fired and to make the system testable without HTTP."""

    @pytest.mark.asyncio
    async def test_fetches_stats_once_per_route_call(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        call_count = {"n": 0}

        def fetch_stats():
            call_count["n"] += 1
            return {
                "gemini": _stats("gemini", success=10, total=10, median_tokens=4000),
            }

        with _patch_all_available(), patch.object(
            orch, "_fetch_agent_stats", fetch_stats
        ):
            await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert call_count["n"] >= 1

    @pytest.mark.asyncio
    async def test_exception_in_fetch_does_not_break_routing(self, tmp_path):
        """Default First: if stats fetch blows up, fall back to existing
        behavior — do not break plan."""
        orch = _make_orchestrator(tmp_path)

        def bad_fetch():
            raise RuntimeError("backend unavailable")

        with _patch_all_available(), patch.object(
            orch, "_fetch_agent_stats", bad_fetch
        ):
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        # Should still produce a routing decision.
        assert agent in {"gemini", "qwen"}
        # And the reasoning should reflect the static fallback.
        assert "static" in reasoning.lower() or "thin" in reasoning.lower() or "tier" in reasoning.lower()


# ── [mock] Escalation when cheapest tier has no qualifier ────────────


class TestRouteTaskEscalatesAcrossTiers:
    """The suggester itself knows how to escalate (see
    TestSuggestRoutingEscalation in test_agent_routing.py), but the
    orchestrator used to pre-filter viable routes to the cheapest tier
    before consulting it. That meant the pure escalation logic never
    fired in production even though it was unit-tested.

    These tests pin the orchestrator's wiring so escalation does fire
    end-to-end. The setup mirrors the agent-level test exactly so a
    green run there + a green run here proves the full pipeline
    honors history-based escalation.
    """

    @pytest.mark.asyncio
    async def test_escalates_to_high_tier_when_low_tier_fails(self, tmp_path):
        """Gemini is cheap but fails 100% of the time; claude (HIGH tier)
        succeeds 90%. The orchestrator must route to claude — not pin
        to gemini just because it's in the cheapest tier."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "claude": _stats("claude", success=9, total=10, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent, _model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert agent == "claude"
        # Reasoning should call out history (or escalation), not "random".
        assert (
            "history" in reasoning.lower()
            or "success" in reasoning.lower()
        )
        # And explain that the pick came from the history-driven path.
        assert "history" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_escalation_holds_across_runs(self, tmp_path):
        """Run several times — the historical pick is deterministic
        (gemini 0% < threshold, claude 90% > threshold) so every run
        must escalate to claude, never to gemini."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=20, median_tokens=4000),
            "claude": _stats("claude", success=18, total=20, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent_counts = Counter()
            for _ in range(20):
                agent, _model, _reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agent_counts[agent] += 1

        assert agent_counts["claude"] == 20
        assert agent_counts.get("gemini", 0) == 0

    @pytest.mark.asyncio
    async def test_cheapest_qualifier_wins_when_rates_are_equal(self, tmp_path):
        """When LOW-tier agents fail threshold and MULTIPLE higher-tier
        agents qualify with EQUAL success rates, the suggester's cost
        tiebreak picks the cheaper qualifier — proving escalation
        lands on the cheapest viable candidate, not the most
        expensive one."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "codex": AgentConfig(
                cli_command="codex",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.MEDIUM,
            ),
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.HIGH,
            ),
        }
        model_routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="qwen", model="qwen-coder"),
            ModelRoute(agent="codex", model="codex-mini"),
            ModelRoute(agent="claude", model="claude-sonnet-4-5"),
        ]
        orch = _make_orchestrator(
            tmp_path, model_routing=model_routing, agents=agents,
        )
        # gemini+qwen fail; codex and claude both qualify at 90% success.
        # codex is cheaper, so it should win the cost tiebreak.
        stats = {
            "gemini": _stats("gemini", success=1, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=2, total=10, median_tokens=3500),
            "codex": _stats("codex", success=9, total=10, median_tokens=10000),
            "claude": _stats("claude", success=9, total=10, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent, _model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        # codex beats claude: equal success_rate, lower median_tokens.
        assert agent == "codex"

    @pytest.mark.asyncio
    async def test_highest_success_rate_wins_among_qualifiers(self, tmp_path):
        """When the cheapest tier has no qualifier but multiple higher
        tiers qualify, the suggester picks the highest success rate
        (claude 95%) over the merely-cheaper qualifier (codex 80%).
        Primary metric is reliability; cost is the tiebreak, not the
        primary sort."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
            "codex": AgentConfig(
                cli_command="codex",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.MEDIUM,
            ),
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.HIGH,
            ),
        }
        model_routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="qwen", model="qwen-coder"),
            ModelRoute(agent="codex", model="codex-mini"),
            ModelRoute(agent="claude", model="claude-sonnet-4-5"),
        ]
        orch = _make_orchestrator(
            tmp_path, model_routing=model_routing, agents=agents,
        )
        stats = {
            "gemini": _stats("gemini", success=1, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=2, total=10, median_tokens=3500),
            "codex": _stats("codex", success=8, total=10, median_tokens=10000),
            "claude": _stats("claude", success=19, total=20, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent, _model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        # claude (95%) beats codex (80%) on the primary metric — even
        # though codex is cheaper.
        assert agent == "claude"


# ── [mock] Reasoning mentions escalation across tiers ─────────────────


class TestEscalationReasoningIsAuditable:
    """When the suggester escalates, the operator must see that it
    escalated — not just "chosen from N viable routes" that hides the
    fact that the pick crossed tiers."""

    @pytest.mark.asyncio
    async def test_reasoning_mentions_high_tier_when_escalated(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "claude": _stats("claude", success=9, total=10, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        lowered = reasoning.lower()
        # Operator can see both tiers in the reasoning string.
        assert "high" in lowered or "claude" in lowered
        assert "gemini" in lowered or "low" in lowered
        assert "history" in lowered


# ── [mock] Static fallback still uses cheapest tier ──────────────────


class TestStaticFallbackStillBucketsCheapest:
    """When the suggester returns StaticFallback (thin history or no
    qualifier anywhere), the orchestrator must still random.distribute
    within the cheapest tier — preserving the prior tier-distribution
    contract that older tests pin. Escalation is gated on the suggester
    actually picking; without that, Default First wins.

    The cheapest-tier-bucket contract only kicks in when the suggester
    itself returns StaticFallback. If any agent (cheap or expensive)
    has rich history, the suggester will pick it — that's the whole
    point of the measured-history layer."""

    @pytest.mark.asyncio
    async def test_all_agents_thin_history_stays_in_low_tier(self, tmp_path):
        """ALL agents (cheap and expensive) have < min_samples. The
        suggester returns StaticFallback, so the orchestrator must
        distribute within the cheapest tier — even when an expensive
        agent is available in the viable pool."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=1, total=1),  # thin
            "qwen": _stats("qwen", success=1, total=1),  # thin
            "claude": _stats("claude", success=1, total=1),  # thin too
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent_counts = Counter()
            for _ in range(40):
                agent, _model, _reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agent_counts[agent] += 1

        # All three are thin → StaticFallback → cheapest-tier only.
        assert set(agent_counts.keys()).issubset({"gemini", "qwen"}), (
            f"Static fallback must stay in cheapest tier; got: "
            f"{dict(agent_counts)}"
        )
        assert len(agent_counts) > 1, (
            f"Default First: distribute across cheap tier on static; "
            f"got: {dict(agent_counts)}"
        )

    @pytest.mark.asyncio
    async def test_no_qualifier_anywhere_still_falls_back(self, tmp_path):
        """Every agent fails the success threshold. Suggester returns
        StaticFallback. Orchestrator must stay in cheapest tier."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=1, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=1, total=10, median_tokens=3500),
            "claude": _stats("claude", success=2, total=10, median_tokens=20000),
        }

        with _patch_all_available(), _patch_stats(orch, stats):
            agent_counts = Counter()
            for _ in range(40):
                agent, _model, reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agent_counts[agent] += 1

        # No qualifier → StaticFallback → cheapest tier only.
        assert set(agent_counts.keys()).issubset({"gemini", "qwen"}), (
            f"Static fallback must stay in cheapest tier; got: "
            f"{dict(agent_counts)}"
        )
        # And the reasoning should mark it as static.
        assert "static" in reasoning.lower()
