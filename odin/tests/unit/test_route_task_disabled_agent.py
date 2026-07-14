"""Tests for the orchestrator's planner-route-path disabled-agent guard.

W10.4 follow-on: the previous commit added dispatch/assign guards that refuse
to send work to a roster-disabled agent — but the orchestrator's
``_route_task_api`` could still silently fall back. When the planner (LLM)
named a disabled agent in a plan (e.g. ``suggested_agent="gemini"`` on a
board where only ``claude`` and ``codex`` are enrolled), the routing code
hit ``suggested not in agents_by_name``, skipped Phase 1 silently, and
Phase 2 picked the cheapest viable agent. The operator saw ``assignee=
claude`` with no explanation — the exact symptom board 6 surfaced.

These tests pin the contract: when routing_config is sourced from the
board roster and ``suggested`` does not resolve to a roster-enabled
agent, the orchestrator raises a named error that points at the
settings URL. Two layers:

  * ``_route_task_api`` — direct unit test with a synthetic routing_config
    showing the disabled-agent scenario.
  * ``_route_task_config`` — config-fallback path with a ``suggested``
    agent that is not in the active lineup: same error (the lineup *is*
    the roster when no backend roster is available).

Each test asserts both the exception type and that the message names
the agent + carries the settings path. The backend side (``views.py``,
``dag_executor.py``) already raises ``AgentNotEnabledOnBoard`` with the
same shape; the orchestrator side mirrors it for symmetry so error
surfaces look the same whether the trigger is the planner or the
dispatcher.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from odin.models import (
    AgentConfig,
    CostTier,
    ModelRoute,
    OdinConfig,
)
from odin.orchestrator import Orchestrator


# ── Helpers ─────────────────────────────────────────────────────────


def _make_orchestrator(tmp_path, agents=None, model_routing=None):
    task_dir = str(tmp_path / "tasks")
    log_dir = str(tmp_path / "logs")
    cost_dir = str(tmp_path / "costs")
    spec_dir = str(tmp_path / "specs")
    for d in [task_dir, log_dir, cost_dir, spec_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    if agents is None:
        agents = {
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["writing", "coding", "reasoning"],
                cost_tier=CostTier.HIGH,
            ),
            "codex": AgentConfig(
                cli_command="codex",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.MEDIUM,
            ),
        }

    if model_routing is None:
        model_routing = [
            ModelRoute(agent="claude", model="claude-sonnet-4-5"),
            ModelRoute(agent="codex", model="codex-mini"),
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


def _patch_all_available():
    async def _available(self, name, cfg):
        return True
    return patch.object(Orchestrator, "_is_available_cached", _available)


def _route_task_api(orch, suggested, routing_config):
    """Call _route_task_api with the test convention."""
    return orch._route_task_api(
        required_caps=["writing"],
        complexity="medium",
        suggested=suggested,
        quota=None,
        routing_config=routing_config,
    )


def _route_task_config(orch, suggested):
    """Call _route_task_config (the lineup-based fallback)."""
    return orch._route_task_config(
        required_caps=["writing"],
        complexity="medium",
        suggested=suggested,
        quota=None,
    )


# ── [mock] _route_task_api refuses a planner-suggested disabled agent ────


class TestRouteTaskApiRefusesDisabledSuggestedAgent:
    """When the planner names an agent that is NOT on this board's roster,
    ``_route_task_api`` must raise a named exception that names the agent
    and points at the settings path. The previous silent fallback (Phase 2
    cheapest-tier pick) is the failure mode these tests pin against.
    """

    def _routing_config(self, settings_path):
        """Synthetic routing_config where the suggested agent is absent —
        the endpoint filters disabled agents out via board membership, so
        an unknown name here means "operator disabled this on board N".
        """
        return {
            "agents": [
                {
                    "name": "claude",
                    "cost_tier": "high",
                    "capabilities": ["writing", "coding", "reasoning"],
                    "default_model": "claude-sonnet-4-5",
                    "premium_model": "claude-sonnet-4-5",
                    "models": [
                        {"name": "claude-sonnet-4-5", "enabled": True, "is_default": True},
                    ],
                },
                {
                    "name": "codex",
                    "cost_tier": "medium",
                    "capabilities": ["writing", "coding"],
                    "default_model": "codex-mini",
                    "premium_model": "codex-mini",
                    "models": [
                        {"name": "codex-mini", "enabled": True, "is_default": True},
                    ],
                },
            ],
            "settings_path": settings_path,
            "empty": False,
        }

    @pytest.mark.asyncio
    async def test_disabled_suggested_agent_raises_named_error(self, tmp_path):
        """Planner suggests 'gemini' on a board where only claude+codex are
        enrolled — must raise, not silently route to claude."""
        orch = _make_orchestrator(tmp_path)
        routing_config = self._routing_config(settings_path="/api/boards/42/agents/")

        with _patch_all_available():
            with pytest.raises(Exception) as exc_info:
                await _route_task_api(orch, "gemini", routing_config)

        exc = exc_info.value
        # The exception must be a known named error (mirrors the dispatch
        # guard's AgentNotEnabledOnBoard). If a bare RuntimeError
        # surfaces here, this test fails — that's the silent-fallback
        # regression we're pinning against.
        from odin.orchestrator import SuggestedAgentDisabled
        assert isinstance(exc, SuggestedAgentDisabled), (
            f"expected SuggestedAgentDisabled, got {type(exc).__name__}: "
            f"{exc}"
        )
        # The exception must carry the agent name + the settings URL so
        # the operator sees a single named line, not a stack trace.
        assert exc.agent == "gemini"
        assert exc.settings_url == "/api/boards/42/agents/"
        # And the message must read like a hint, not a fallback log.
        msg = str(exc).lower()
        assert "gemini" in str(exc).lower()
        assert "/agents/" in msg or "settings" in msg or "/boards/" in msg

    @pytest.mark.asyncio
    async def test_flagged_enabled_false_on_planner_suggested(self, tmp_path):
        """Defensive layer: if routing_config ever carries an explicit
        ``enabled=False`` (a stricter contract than today's silent
        filtering), still raise rather than fallback.
        """
        orch = _make_orchestrator(tmp_path)
        routing_config = {
            "agents": [
                {
                    "name": "claude",
                    "enabled": True,
                    "cost_tier": "high",
                    "capabilities": ["writing", "coding", "reasoning"],
                    "default_model": "claude-sonnet-4-5",
                    "premium_model": "claude-sonnet-4-5",
                    "models": [
                        {"name": "claude-sonnet-4-5", "enabled": True, "is_default": True},
                    ],
                },
                {
                    "name": "gemini",
                    "enabled": False,
                    "cost_tier": "medium",
                    "capabilities": ["writing", "coding"],
                    "default_model": "gemini-pro",
                    "premium_model": "gemini-pro",
                    "models": [
                        {"name": "gemini-pro", "enabled": True, "is_default": True},
                    ],
                },
            ],
            "settings_path": "/api/boards/7/agents/",
            "empty": False,
        }

        with _patch_all_available():
            from odin.orchestrator import SuggestedAgentDisabled
            with pytest.raises(SuggestedAgentDisabled) as exc_info:
                await _route_task_api(orch, "gemini", routing_config)

        assert exc_info.value.agent == "gemini"
        assert exc_info.value.settings_url == "/api/boards/7/agents/"

    @pytest.mark.asyncio
    async def test_enabled_agent_routes_via_phase_1(self, tmp_path):
        """Sanity check: when the planner suggests an enabled agent, the
        guard is a no-op — the existing Phase 1 path still wins."""
        orch = _make_orchestrator(tmp_path)
        routing_config = self._routing_config(settings_path="/api/boards/42/agents/")

        with _patch_all_available():
            agent, _model, reasoning, _ar = await _route_task_api(
                orch, "claude", routing_config,
            )

        assert agent == "claude"
        assert "suggested" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_no_suggestion_still_routes_via_phase_2(self, tmp_path):
        """When the planner offers no suggestion (suggested=None), the
        cheapest-viable-tier path runs unchanged — not the loud-error
        guard. The guard fires ONLY when the planner explicitly names
        a roster-disabled agent."""
        orch = _make_orchestrator(tmp_path)
        routing_config = self._routing_config(settings_path="/api/boards/42/agents/")
        stats = {}  # thin history → static fallback

        with _patch_all_available(), patch.object(
            orch, "_fetch_agent_stats", lambda: stats,
        ):
            agent, _model, _reasoning, _ar = await _route_task_api(
                orch, None, routing_config,
            )

        assert agent in {"claude", "codex"}


# ── [mock] _route_task_config refuses a planner-suggested unknown agent ───


class TestRouteTaskConfigRefusesUnknownSuggestedAgent:
    """When the API is unavailable and we fall back to the config-based
    lineup, an unknown ``suggested_agent`` (not in the lineup) must
    raise the same named error — not silently route to a different agent.
    """

    @pytest.mark.asyncio
    async def test_unknown_suggested_agent_in_lineup_raises(self, tmp_path):
        """gemini is active in many lineups but not in this orchestrator's
        ``OdinConfig.agents``. The planner suggests 'gemini' (e.g. carried
        from an older config). The fallback path must surface the name +
        settings path, not silently route to claude."""
        orch = _make_orchestrator(tmp_path)

        # Fallback path doesn't carry a routing_config dict, so the hint
        # resolves to a generic "see settings" line — what the operator
        # needs to act. The error still names the agent.
        with _patch_all_available():
            from odin.orchestrator import SuggestedAgentDisabled
            with pytest.raises(SuggestedAgentDisabled) as exc_info:
                await _route_task_config(orch, "gemini")

        assert exc_info.value.agent == "gemini"
        # Even with no settings_path the error must point the operator
        # somewhere useful (text contains "settings" or generic agent name).
        msg = str(exc_info.value).lower()
        assert "gemini" in msg
        assert "settings" in msg or "agent" in msg or "board" in msg

    @pytest.mark.asyncio
    async def test_known_suggested_agent_in_lineup_routes(self, tmp_path):
        """Sanity check: a known agent in the lineup still routes via
        Phase 1 — the guard does not over-fire."""
        orch = _make_orchestrator(tmp_path)

        with _patch_all_available():
            agent, _model, reasoning, _ar = await _route_task_config(
                orch, "codex",
            )

        assert agent == "codex"
        assert "suggested" in reasoning.lower()
