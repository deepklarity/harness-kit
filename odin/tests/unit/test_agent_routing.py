"""Tests for the history-driven agent suggester.

The suggester ranks viable candidates using measured success rate +
median cost from board history. When history is thin (< min_samples)
it falls back to a static ordering (the existing tier-distribute
behavior), keeping Default First semantics: heuristic when we have
signal, deterministic config when we don't.

Tags:
- [simple] — pure logic over plain dataclasses
"""

import pytest

from odin.agent_routing import (
    AgentStats,
    RoutingDecision,
    StaticFallback,
    SuggestionInput,
    suggest_routing,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _stats(name, success=0, total=0, median_tokens=0):
    """Build an AgentStats with explicit fields."""
    rate = (success / total) if total else 0.0
    return AgentStats(
        name=name,
        sample_count=total,
        success_count=success,
        success_rate=rate,
        median_tokens=median_tokens,
    )


# ── [simple] Cheap agent clears threshold → suggested ───────────────


class TestSuggestRoutingCheapestClearsThreshold:
    """Two cheap agents; one is consistently successful, the other isn't.
    The suggester should pick the cheaper tier member that actually
    succeeds rather than random.choice."""

    def test_clear_winner_in_cheapest_tier(self):
        stats = {
            "gemini": _stats("gemini", success=8, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=2, total=10, median_tokens=3500),
        }
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert decision.picked == "gemini"
        assert "success" in decision.reason.lower() or "history" in decision.reason.lower()

    def test_lower_median_cost_wins_among_qualifying_candidates(self):
        """When two qualifying candidates have the same success rate, prefer
        the cheaper one (lower median_tokens)."""
        stats = {
            "gemini": _stats("gemini", success=9, total=10, median_tokens=8000),
            "qwen": _stats("qwen", success=9, total=10, median_tokens=4000),
        }
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert decision.picked == "qwen"


# ── [simple] Only expensive agent succeeds → escalated ───────────────


class TestSuggestRoutingEscalation:
    """When the cheapest tier has below-threshold signals, the suggester
    must escalate to the next viable tier rather than pinning to the
    cheap tier that keeps failing."""

    def test_no_cheapest_tier_qualifier_escalates(self):
        """Gemini has 0 successes; only claude (expensive) succeeded.
        Suggester must escalate to claude."""
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "claude": _stats("claude", success=9, total=10, median_tokens=20000),
        }
        candidates = ["gemini", "claude"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.7,
        )
        assert decision.picked == "claude"
        assert "escalat" in decision.reason.lower() or "history" in decision.reason.lower()

    def test_multi_tier_partial_qualifiers_promote_cheapest(self):
        """Two cheap agents below threshold, mid-tier above threshold.
        Suggester should pick the cheapest *qualifying* tier member."""
        stats = {
            "gemini": _stats("gemini", success=1, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=2, total=10, median_tokens=3500),
            "codex": _stats("codex", success=8, total=10, median_tokens=10000),
        }
        candidates = ["gemini", "qwen", "codex"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.7,
        )
        assert decision.picked == "codex"

    def test_no_qualifier_anywhere_returns_static(self):
        """When NO agent in the candidates clears the threshold, fall back
        to the static fallback ordering (the Default First path)."""
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "claude": _stats("claude", success=1, total=10, median_tokens=20000),
        }
        candidates = ["gemini", "claude"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.99,
        )
        # No candidate cleared 99% threshold → static fallback.
        assert isinstance(decision, StaticFallback) or decision.picked is None


# ── [simple] Thin history → static fallback ─────────────────────────


class TestSuggestRoutingThinHistory:
    """When history is too thin to trust, fall back to the existing
    static ordering. The point of this fallback is to avoid letting a
    single lucky/failed task change who handles everything."""

    def test_all_candidates_below_min_samples_static(self):
        """Even a 100%-success agent with only 2 samples is below
        min_samples=5 — fall back to static."""
        stats = {
            "gemini": _stats("gemini", success=2, total=2, median_tokens=4000),
        }
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert isinstance(decision, StaticFallback)
        assert decision.candidates == ["gemini", "qwen"]

    def test_empty_history_static(self):
        stats = {}
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert isinstance(decision, StaticFallback)

    def test_partial_thin_only_eligible_agents_count(self):
        """Only agents with sample_count >= min_samples contribute to
        eligibility. Agents with thin history are excluded from the
        filtered list but the decision remains a history-driven pick
        among the eligible survivors."""
        stats = {
            "gemini": _stats("gemini", success=10, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=5, total=5, median_tokens=3500),
        }
        # Both qualify (>=5). Suggestion should be deterministic.
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        # Both pass threshold; tiebreak by cost → qwen (3500 < 4000).
        assert decision.picked == "qwen"


# ── [simple] Below-threshold disqualified, others may still win ────


class TestSuggestRoutingThresholdDisqualification:
    """If an agent has enough samples but its success rate is below the
    threshold, it's disqualified — even if it's the cheapest. Only the
    remaining qualifiers compete."""

    def test_below_threshold_disqualified(self):
        stats = {
            "gemini": _stats("gemini", success=3, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=9, total=10, median_tokens=3500),
        }
        candidates = ["gemini", "qwen"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.7,
        )
        # gemini at 30% < 70% → disqualified. qwen at 90% → winner.
        assert decision.picked == "qwen"

    def test_threshold_exact_boundary_passes(self):
        """A success rate exactly equal to the threshold passes the
        greater-than-or-equal test (don't be brittle at the boundary)."""
        stats = {
            "gemini": _stats("gemini", success=5, total=10, median_tokens=4000),
        }
        candidates = ["gemini"]
        decision = suggest_routing(
            candidates,
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert decision.picked == "gemini"


# ── [simple] Decision shape ──────────────────────────────────────────


class TestDecisionShape:
    """Decisions are structured so the routing_reasoning in task
    metadata reads naturally to the operator."""

    def test_history_driven_decision_records_inputs(self):
        stats = {
            "gemini": _stats("gemini", success=9, total=10, median_tokens=4000),
        }
        decision = suggest_routing(
            ["gemini"],
            stats,
            min_samples=5,
            success_threshold=0.5,
        )
        assert isinstance(decision, RoutingDecision)
        assert decision.picked == "gemini"
        assert decision.threshold == 0.5
        assert decision.min_samples == 5
        # Reasoning should mention the rule that fired.
        assert "success" in decision.reason.lower() or "history" in decision.reason.lower()

    def test_static_fallback_records_inputs(self):
        decision = suggest_routing(
            ["gemini", "qwen"],
            {},
            min_samples=5,
            success_threshold=0.5,
        )
        assert isinstance(decision, StaticFallback)
        assert decision.threshold == 0.5
        assert decision.min_samples == 5
        assert "static" in decision.reason.lower() or "thin" in decision.reason.lower()

    def test_history_driving_inputs_dataclass(self):
        inp = SuggestionInput(
            candidates=["gemini", "qwen"],
            stats_by_name={
                "gemini": _stats("gemini", success=9, total=10),
            },
            min_samples=5,
            success_threshold=0.7,
        )
        assert inp.min_samples == 5
        assert inp.candidates == ["gemini", "qwen"]
