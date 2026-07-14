"""Tests for the assignment_reason field on task metadata.

The point of `task.metadata.assignment_reason` is to give the human
operator a *one-line truthful WHY* next to the assignee — agent avatar
without reasoning is opinion. The field is a structured dict, NOT a
free-text string, so the UI can:

  * render the one-line `reason` next to the assignee
  * show `rule` in a tooltip ("history", "suggested-agent", "escalated"…)
  * surface `cheaper_alternatives` so the operator can audit the pick
  * flag `override=true` when a human (not the router) made the call

The dict is built deterministically from the same measured-history math
that already powers `routing_reasoning` — no model call, no planner
cost.

These tests pin the contract end-to-end:

  * _route_task returns the dict alongside the existing 3-tuple
  * the dict is stamped into task.metadata by _create_tasks_from_plan
  * each routing path (suggested-agent, suggested-model, history,
    static-fallback, escalated) produces a recognisable `rule`
  * cheaper_alternatives only lists candidates strictly cheaper than
    the pick (no point listing things that lost on cost tier)
  * twin_consensus is null when there is no history (default first,
    never fabricate a consensus line)

Tags:
- [mock]  — patches the backend's fetch_agent_stats
- [simple] — pure logic, no I/O
"""

import asyncio
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
        board_backend="local",
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
    return patch.object(orch, "_fetch_agent_stats", lambda: stats_by_name)


# ── [simple] Tuple shape ──────────────────────────────────────────────


class TestAssignmentReasonTupleShape:
    """_route_task must return 4 values: (agent, model, reasoning,
    assignment_reason). Existing tests unpack 3 — they will need to
    move to 4 in a follow-up; here we pin the new shape."""

    @pytest.mark.asyncio
    async def test_returns_four_tuple_not_three(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            result = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        # Exactly 4 values — 3-tuple callers will fail loudly.
        assert len(result) == 4, (
            f"_route_task must return (agent, model, reasoning, "
            f"assignment_reason); got {len(result)} values"
        )

    @pytest.mark.asyncio
    async def test_assignment_reason_is_a_dict(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            result = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        agent, model, reasoning, ar = result
        assert isinstance(ar, dict), (
            f"assignment_reason must be a structured dict, not {type(ar)}"
        )

    @pytest.mark.asyncio
    async def test_dict_has_required_top_level_keys(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            _agent, _model, _reasoning, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        # Every path must produce a dict with these keys — the UI reads
        # them without conditionals.
        for key in ("agent", "model", "rule", "reason", "override",
                    "cheaper_alternatives", "twin_consensus"):
            assert key in ar, f"assignment_reason missing required key: {key}"

    @pytest.mark.asyncio
    async def test_dict_agent_and_model_match_tuple(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            agent, model, _reasoning, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["agent"] == agent
        assert ar["model"] == model

    @pytest.mark.asyncio
    async def test_reason_is_one_line_truthful_string(self, tmp_path):
        """The reason string must end up in `routing_reasoning` (the
        human-visible WHY line). Not a different string."""
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            _agent, _model, reasoning, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        # Same source string — the UI shows `reason`, the CLI/old path
        # shows `routing_reasoning`. Same data, two surfaces.
        assert isinstance(ar["reason"], str) and len(ar["reason"]) > 0
        assert ar["reason"] == reasoning

    @pytest.mark.asyncio
    async def test_override_defaults_to_false(self, tmp_path):
        """The router never overrides itself. Human override is a
        separate flag, stamped later in the taskit-backend."""
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            _agent, _model, _reasoning, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["override"] is False

    @pytest.mark.asyncio
    async def test_twin_consensus_is_null_when_no_twins(self, tmp_path):
        """Default First: never fabricate a consensus line. No history
        → twin_consensus is null. The UI renders em-dash on null."""
        orch = _make_orchestrator(tmp_path)
        with _patch_all_available(), _patch_stats(orch, {}):
            _agent, _model, _reasoning, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["twin_consensus"] is None


# ── [mock] Rule variants per routing path ───────────────────────────


class TestAssignmentReasonRules:
    """The `rule` field tells the UI which branch fired. It must match
    the routing_reasoning language so an operator reading the WHY line
    can trace it back to the routing rule."""

    @pytest.mark.asyncio
    async def test_suggested_agent_path_rule(self, tmp_path):
        """Planner suggested qwen → rule = 'suggested-agent'."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=10, total=10),
            "qwen": _stats("qwen", success=0, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="qwen",
                quota=None,
            )
        assert ar["rule"] == "suggested-agent"
        assert ar["agent"] == "qwen"

    @pytest.mark.asyncio
    async def test_suggested_model_path_rule(self, tmp_path):
        """Planner suggested agent AND a specific model → rule =
        'suggested-model'."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=10, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            # _route_task_config honours suggested_model via model_routing.
            agent, model, _r, ar = await orch._route_task_config(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
                suggested_model="gemini-2.5-flash",
            )
        assert ar["rule"] == "suggested-model"
        assert ar["agent"] == "gemini"
        assert ar["model"] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_history_path_rule(self, tmp_path):
        """Suggester picks a clear winner → rule = 'history'."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10),
            "qwen": _stats("qwen", success=2, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["rule"] == "history"
        assert ar["agent"] == "gemini"

    @pytest.mark.asyncio
    async def test_static_fallback_path_rule(self, tmp_path):
        """All candidates thin → StaticFallback → rule = 'static-fallback'."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=1, total=1),  # thin
            "qwen": _stats("qwen", success=1, total=1),  # thin
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["rule"] == "static-fallback"

    @pytest.mark.asyncio
    async def test_escalation_path_rule(self, tmp_path):
        """Low tier fails threshold, HIGH tier qualifies → rule =
        'escalated'."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10),
            "qwen": _stats("qwen", success=1, total=10),
            "claude": _stats("claude", success=9, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["rule"] == "escalated"
        assert ar["agent"] == "claude"


# ── [mock] Cheaper alternatives ─────────────────────────────────────


class TestAssignmentReasonCheaperAlternatives:
    """`cheaper_alternatives` lists viable agents strictly cheaper than
    the picked one, with the success-rate/median-token delta that
    caused them to lose. The operator can audit: 'I see we escalated;
    why?' — and the tooltip shows the cheap-tier losers and their
    measured rates."""

    @pytest.mark.asyncio
    async def test_escalated_pick_lists_cheaper_losers(self, tmp_path):
        """Picked claude (HIGH); gemini + qwen (LOW) were the cheaper
        candidates — both should appear with their stats."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10, median_tokens=4000),
            "qwen": _stats("qwen", success=1, total=10, median_tokens=3500),
            "claude": _stats("claude", success=9, total=10, median_tokens=20000),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        cheaper = ar["cheaper_alternatives"]
        assert isinstance(cheaper, list)
        # Both cheap-tier losers should be present.
        names = sorted(c["agent"] for c in cheaper)
        assert "gemini" in names
        assert "qwen" in names
        # And they should NOT include the picked agent itself.
        assert "claude" not in names

    @pytest.mark.asyncio
    async def test_cheaper_alternative_records_failure_delta(self, tmp_path):
        """Each entry shows the loser's success_rate so the operator
        can see WHY it lost."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=0, total=10),
            "qwen": _stats("qwen", success=1, total=10),
            "claude": _stats("claude", success=9, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        cheaper = ar["cheaper_alternatives"]
        gemini = next((c for c in cheaper if c["agent"] == "gemini"), None)
        assert gemini is not None
        assert gemini["success_rate"] == 0.0
        # And it must explain why it lost.
        assert "fail" in gemini["reason"].lower() or "below" in gemini["reason"].lower()

    @pytest.mark.asyncio
    async def test_cheapest_tier_pick_has_no_cheaper_alternatives(self, tmp_path):
        """If we picked a LOW-tier agent, there is nothing cheaper to
        list — `cheaper_alternatives` is empty (not None, not absent)."""
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10),
            "qwen": _stats("qwen", success=2, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert ar["cheaper_alternatives"] == []

    @pytest.mark.asyncio
    async def test_cheaper_alternatives_filtered_by_viability(self, tmp_path):
        """Agents that aren't viable (missing caps, over quota,
        unavailable) must NOT show up as alternatives — they wouldn't
        have been picked anyway."""
        orch = _make_orchestrator(tmp_path)
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing"],
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
        model_routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="qwen", model="qwen-coder"),
            ModelRoute(agent="claude", model="claude-sonnet-4-5"),
        ]
        cfg = OdinConfig(
            base_agent="claude",
            task_storage=str(tmp_path / "tasks"),
            log_dir=str(tmp_path / "logs"),
            cost_storage=str(tmp_path / "costs"),
            spec_storage=str(tmp_path / "specs"),
            agents=agents,
            model_routing=model_routing,
            board_backend="local",
        )
        for d in [cfg.task_storage, cfg.log_dir, cfg.cost_storage]:
            Path(d).mkdir(parents=True, exist_ok=True)
        orch = Orchestrator(cfg)
        # gemini lacks the 'coding' cap, so it shouldn't be a viable
        # alternative for a 'coding' task.
        stats = {
            "gemini": _stats("gemini", success=0, total=10),
            "qwen": _stats("qwen", success=1, total=10),
            "claude": _stats("claude", success=9, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task(
                required_caps=["writing", "coding"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        names = [c["agent"] for c in ar["cheaper_alternatives"]]
        assert "gemini" not in names
        assert "qwen" in names


# ── [mock] Stamped into task.metadata at dispatch ───────────────────


class TestAssignmentReasonStampedAtDispatch:
    """_create_tasks_from_plan must write `metadata.assignment_reason`
    on every created task. The dict must be the same one returned by
    _route_task — no lossy round-trip."""

    def _build_plan_subtask(self, **overrides):
        return {
            "id": "task_1",
            "title": overrides.pop("title", "ship the WHY line"),
            "description": "Surface the assignment_reason next to the assignee.",
            "required_capabilities": overrides.pop("required_capabilities", ["writing"]),
            "complexity": overrides.pop("complexity", "medium"),
            "suggested_agent": overrides.pop("suggested_agent", None),
            "suggested_model": overrides.pop("suggested_model", None),
            "depends_on": [],
        }

    def test_create_tasks_stamps_assignment_reason(self, tmp_path):
        """When _route_task returns a 4-tuple, the metadata on the
        resulting task must carry `assignment_reason`."""
        from odin.taskit.manager import TaskManager

        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10),
            "qwen": _stats("qwen", success=2, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            sub = self._build_plan_subtask()
            tasks = asyncio.run(orch._create_tasks_from_plan([sub], spec_id="spec_42"))

        assert len(tasks) == 1
        md = tasks[0].metadata or {}
        assert "assignment_reason" in md, (
            f"_create_tasks_from_plan must stamp assignment_reason; "
            f"got keys: {sorted(md.keys())}"
        )
        ar = md["assignment_reason"]
        assert ar["agent"] == "gemini"  # clear history winner
        assert ar["rule"] == "history"
        # And the override flag starts false at dispatch.
        assert ar["override"] is False

    def test_stamped_dict_matches_route_task_return(self, tmp_path):
        """Round-trip: the dict written into metadata is the same
        object (or an equivalent dict) returned by _route_task."""
        from odin.taskit.manager import TaskManager
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10),
            "qwen": _stats("qwen", success=2, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            sub = self._build_plan_subtask()
            tasks = asyncio.run(orch._create_tasks_from_plan([sub], spec_id="spec_42"))
            direct = asyncio.run(orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            ))
        _a, _m, _r, direct_ar = direct
        stamped_ar = tasks[0].metadata["assignment_reason"]
        # Same rule, same agent — proof that what the UI shows is what
        # _route_task actually decided.
        assert stamped_ar["rule"] == direct_ar["rule"]
        assert stamped_ar["agent"] == direct_ar["agent"]
        assert stamped_ar["model"] == direct_ar["model"]
        assert stamped_ar["reason"] == direct_ar["reason"]


# ── [mock] Config fallback path also produces the dict ──────────────


class TestAssignmentReasonConfigFallback:
    """When the API is unavailable, _route_task_config takes over.
    Same contract — same dict shape — so the UI gets a truthful WHY
    even when the data source is config, not the routing API."""

    @pytest.mark.asyncio
    async def test_config_fallback_returns_assignment_reason(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        stats = {
            "gemini": _stats("gemini", success=9, total=10),
        }
        with _patch_all_available(), _patch_stats(orch, stats):
            _agent, _model, _r, ar = await orch._route_task_config(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )
        assert isinstance(ar, dict)
        for key in ("agent", "model", "rule", "reason", "override",
                    "cheaper_alternatives", "twin_consensus"):
            assert key in ar
        # Same picked agent as the API path.
        assert ar["agent"] == "gemini"