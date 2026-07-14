"""Tests for task routing — verifying that _route_task() respects the LLM's
suggested_agent from decomposition and only falls back to model_routing
priority when the suggestion is invalid.

Tags:
- [mock] — mocked harness availability
- [simple] — pure logic
"""

import asyncio
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch, AsyncMock

import pytest

from odin.models import (
    AgentConfig,
    CostTier,
    ModelRoute,
    OdinConfig,
)
from odin.orchestrator import Orchestrator


def _make_orchestrator(tmp_path, model_routing=None, agents=None):
    """Build an Orchestrator with configurable routing and agents."""
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
                capabilities=["writing", "coding", "research"],
                cost_tier=CostTier.LOW,
            ),
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["writing", "coding", "planning", "reasoning"],
                cost_tier=CostTier.HIGH,
            ),
            "glm": AgentConfig(
                api_key="fake-key",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
            ),
        }

    if model_routing is None:
        model_routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="glm", model="GLM-4.7"),
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


def _mock_all_available():
    """Patch _is_available_cached to return True for all agents."""
    async def _available(self, name, cfg):
        return True
    return patch.object(Orchestrator, "_is_available_cached", _available)


def _mock_availability(available_agents: set):
    """Patch _is_available_cached to return True only for listed agents."""
    async def _available(self, name, cfg):
        return name in available_agents
    return patch.object(Orchestrator, "_is_available_cached", _available)


# ── [mock] Suggested agent respected ──────────────────────────────────


class TestRouteTaskSuggestionRespected:
    """_route_task() should honour the LLM's suggested_agent when it is
    valid, enabled, available, and has the required capabilities."""

    @pytest.mark.asyncio
    async def test_suggested_agent_used_when_valid(self, tmp_path):
        """When the LLM suggests 'gemini' and gemini is available + capable,
        the task should be assigned to gemini, not glm (first in routing)."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent == "gemini", (
            f"Should respect LLM suggestion 'gemini', got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_gets_model_from_routing(self, tmp_path):
        """The model for the suggested agent should come from model_routing."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_suggested_glm_respected(self, tmp_path):
        """Even API-based agents like GLM should be respected when suggested."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="glm",
                quota=None,
            )

        assert agent == "glm"
        assert model == "GLM-4.7"

    @pytest.mark.asyncio
    async def test_suggested_claude_respected(self, tmp_path):
        """High-cost agent like claude should be respected when suggested."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["reasoning"],
                complexity="high",
                suggested="claude",
                quota=None,
            )

        assert agent == "claude"
        assert model == "claude-sonnet-4-5"

    @pytest.mark.asyncio
    async def test_multiple_tasks_different_agents(self, tmp_path):
        """Different suggestions → different agents. The bug was that ALL tasks
        got assigned to glm regardless of suggestion."""
        orch = _make_orchestrator(tmp_path)

        results = []
        with _mock_all_available():
            for suggested in ["gemini", "glm", "claude"]:
                agent, model, _reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=suggested,
                    quota=None,
                )
                results.append((agent, suggested))

        for agent, suggested in results:
            assert agent == suggested, (
                f"Suggested '{suggested}' but got '{agent}'"
            )


# ── [mock] Suggested agent fallback scenarios ─────────────────────────


class TestRouteTaskSuggestionFallback:
    """_route_task() should fall back to model_routing when the suggestion
    is invalid, unavailable, or missing capabilities."""

    @pytest.mark.asyncio
    async def test_no_suggestion_picks_from_cheapest_tier(self, tmp_path):
        """When suggested=None, distribute among cheapest viable tier (LOW)."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        low_tier_agents = {"gemini", "glm"}
        assert agent in low_tier_agents, (
            f"Without suggestion, should pick from LOW tier {low_tier_agents}, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_unavailable_falls_back(self, tmp_path):
        """If suggested agent isn't available, fall back to routing."""
        orch = _make_orchestrator(tmp_path)

        # Gemini is suggested but unavailable
        with _mock_availability({"claude", "glm"}):
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent in {"glm"}, (
            f"Gemini unavailable, should fall back to LOW tier (glm), got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_coding_agent_satisfies_run_shell_command_capability(self, tmp_path):
        """Coding-capable agents should satisfy shell-capable tasks even when
        older routing metadata omitted explicit tool-style capabilities."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["run_shell_command"],
                complexity="low",
                suggested="gemini",
                quota=None,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_suggested_agent_missing_caps_falls_back(self, tmp_path):
        """If suggested agent lacks required capabilities, fall back."""
        orch = _make_orchestrator(tmp_path)

        # glm doesn't have "research" capability
        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["research"],
                complexity="medium",
                suggested="glm",
                quota=None,
            )

        assert agent == "gemini", (
            f"GLM lacks 'research', should fall to gemini, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_disabled_raises_named_error(self, tmp_path):
        """W10.4 follow-on: a planner-suggested agent that is in the lineup
        but flagged ``enabled=False`` no longer silently falls back to a
        different agent. The previous behaviour (assert agent == "glm")
        was the exact symptom board 6 surfaced — the orchestrator picked
        a viable tier instead of failing loud so the operator could fix
        the cause (settings). Now it raises ``SuggestedAgentDisabled`` so
        the error message names the bad agent instead of hiding it.

        Symmetric with ``_route_task_api``'s Phase 0 guard and with the
        dispatch-side ``AgentNotEnabledOnBoard`` — every layer surfaces
        the same named error so the operator sees one consistent line
        whether the trigger is the planner, the dispatcher, or a manual
        assign.
        """
        from odin.orchestrator import SuggestedAgentDisabled

        agents = {
            "glm": AgentConfig(
                cli_command="glm",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
            ),
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
                enabled=False,  # disabled — operator flipped the switch
            ),
        }
        orch = _make_orchestrator(tmp_path, agents=agents)

        with _mock_all_available():
            with pytest.raises(SuggestedAgentDisabled) as exc_info:
                await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested="gemini",
                    quota=None,
                )

        # The error must name the agent so the operator sees exactly
        # which switch to flip — not a generic "no viable route" line.
        assert exc_info.value.agent == "gemini"

    @pytest.mark.asyncio
    async def test_suggested_agent_unknown_raises_named_error(self, tmp_path):
        """W10.4 follow-on: an unknown suggested agent no longer silently
        falls back to the cheapest tier. The previous behaviour
        (assert agent in {\"gemini\", \"glm\"}) was the exact symptom
        board 6 surfaced — the orchestrator picked a viable tier
        instead of failing loud so the operator could fix the cause
        (settings). Now it raises ``SuggestedAgentDisabled`` so the
        error message names the bad agent instead of hiding it.
        """
        from odin.orchestrator import SuggestedAgentDisabled

        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            with pytest.raises(SuggestedAgentDisabled) as exc_info:
                await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested="nonexistent_agent",
                    quota=None,
                )

        # Error names the agent — the operator sees exactly which
        # switch to flip / which lineup entry to add.
        assert exc_info.value.agent == "nonexistent_agent"

    @pytest.mark.asyncio
    async def test_suggested_agent_over_quota_falls_back(self, tmp_path):
        """If suggested agent is >80% quota (and not high complexity), fall back."""
        orch = _make_orchestrator(tmp_path)

        quota = {
            "gemini": {"usage_pct": 85, "remaining_pct": 15},
        }

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=quota,
            )

        assert agent in {"glm"}, (
            f"Gemini over quota, should fall back to LOW tier (glm), got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_over_quota_but_high_complexity_kept(self, tmp_path):
        """High complexity tasks keep the suggested agent even if over quota."""
        orch = _make_orchestrator(tmp_path)

        quota = {
            "gemini": {"usage_pct": 85, "remaining_pct": 15},
        }

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested="gemini",
                quota=quota,
            )

        assert agent == "gemini", (
            f"High complexity should keep gemini despite quota, got '{agent}'"
        )


# ── [mock] No viable route raises RuntimeError ────────────────────────


class TestRouteTaskNoViableRoute:
    """_route_task() should raise RuntimeError when the priority list is
    exhausted and no route is viable."""

    @pytest.mark.asyncio
    async def test_no_agents_available_raises(self, tmp_path):
        """When no agents are available, _route_task raises RuntimeError."""
        orch = _make_orchestrator(tmp_path)

        with _mock_availability(set()):
            with pytest.raises(RuntimeError, match="No viable route"):
                await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )

    @pytest.mark.asyncio
    async def test_no_capable_agents_raises(self, tmp_path):
        """When no agents have the required capability, raises RuntimeError."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            with pytest.raises(RuntimeError, match="No viable route"):
                await orch._route_task(
                    required_caps=["teleportation"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )

    @pytest.mark.asyncio
    async def test_error_message_includes_tried_routes(self, tmp_path):
        """The RuntimeError message lists what routes were tried."""
        orch = _make_orchestrator(tmp_path)

        with _mock_availability(set()):
            with pytest.raises(RuntimeError, match=r"glm/GLM-4.7") as exc_info:
                await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
            assert "gemini/gemini-2.5-flash" in str(exc_info.value)


# ── [mock] Tier-based distribution ─────────────────────────────────────


class TestRouteTaskApiCapabilityNormalization:
    @pytest.mark.asyncio
    async def test_api_routing_treats_coding_agent_as_shell_capable(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        routing_config = {
            "agents": [
                {
                    "name": "gemini",
                    "capabilities": ["coding", "writing", "research"],
                    "cost_tier": "low",
                    "default_model": "gemini-2.5-flash",
                    "premium_model": None,
                    "models": [{"name": "gemini-2.5-flash", "enabled": True}],
                }
            ]
        }

        with _mock_all_available():
            agent, model, _reasoning, _ar = await orch._route_task_api(
                required_caps=["run_shell_command"],
                complexity="low",
                suggested="gemini",
                quota=None,
                routing_config=routing_config,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-flash"


class TestRouteTaskTierDistribution:
    """_route_task() should distribute across agents within the same
    cost tier instead of always picking the first match."""

    @pytest.mark.asyncio
    async def test_distributes_across_low_tier(self, tmp_path):
        """Route 40 tasks without suggestion — more than 1 LOW-tier agent used."""
        orch = _make_orchestrator(tmp_path)
        agents_seen = Counter()

        with _mock_all_available():
            for _ in range(40):
                agent, model, reasoning, _ar = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agents_seen[agent] += 1

        # All should be LOW-tier
        assert set(agents_seen.keys()).issubset({"gemini", "glm"})
        # Distribution: more than 1 agent should appear
        assert len(agents_seen) > 1, (
            f"Expected distribution across LOW-tier agents, got only: {dict(agents_seen)}"
        )

    @pytest.mark.asyncio
    async def test_cheapest_tier_preferred(self, tmp_path):
        """When LOW and HIGH tier agents are both viable, only LOW tier picked."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert agent in {"gemini", "glm"}, (
            f"Should pick from LOW tier, not HIGH (claude), got '{agent}'"
        )
        assert "LOW" in reasoning

    @pytest.mark.asyncio
    async def test_reasoning_string_generated(self, tmp_path):
        """routing_reasoning should contain tier, agent count, and agent names."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert "LOW tier" in reasoning
        assert "viable" in reasoning
        assert agent in reasoning

    @pytest.mark.asyncio
    async def test_suggested_agent_reasoning(self, tmp_path):
        """When suggested agent is used, reasoning says 'suggested by planner'."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent == "gemini"
        assert "suggested by planner" in reasoning


# ── [mock] Premium model upgrade ───────────────────────────────────────


class TestRouteTaskPremiumUpgrade:
    """High-complexity tasks should be upgraded to premium_model when the
    router picked the model (no planner suggestion). When the planner
    explicitly suggested a model, that choice is respected — the planner
    already knows the complexity."""

    @pytest.mark.asyncio
    async def test_high_complexity_upgrades_when_no_model_suggested(self, tmp_path):
        """When the router picks the model (no suggested_model), high complexity
        triggers premium upgrade."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
                premium_model="gemini-2.5-pro",
            ),
        }
        routing = [ModelRoute(agent="gemini", model="gemini-2.5-flash")]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested=None,
                quota=None,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-pro", (
            f"High complexity should upgrade to premium when router picked model, got '{model}'"
        )
        assert "premium" in reasoning

    @pytest.mark.asyncio
    async def test_high_complexity_upgrades_when_only_agent_suggested(self, tmp_path):
        """When planner suggests agent but NOT model, high complexity still
        triggers premium upgrade — the router picked the model."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
                premium_model="gemini-2.5-pro",
            ),
        }
        routing = [ModelRoute(agent="gemini", model="gemini-2.5-flash")]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested="gemini",
                quota=None,
                suggested_model=None,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-pro", (
            f"High complexity + agent-only suggestion should upgrade to premium, got '{model}'"
        )
        assert "premium" in reasoning

    @pytest.mark.asyncio
    async def test_high_complexity_no_upgrade_when_model_suggested(self, tmp_path):
        """When planner explicitly suggests a model, high complexity does NOT
        override it. The planner already considered complexity when choosing."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
                premium_model="gemini-2.5-pro",
            ),
        }
        routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="gemini", model="gemini-2.0-flash"),
        ]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested="gemini",
                quota=None,
                suggested_model="gemini-2.0-flash",
            )

        assert agent == "gemini"
        assert model == "gemini-2.0-flash", (
            f"Planner's explicit model choice should be respected even at high complexity, got '{model}'"
        )
        assert "premium" not in reasoning
        assert "suggested model" in reasoning

    @pytest.mark.asyncio
    async def test_medium_complexity_no_upgrade(self, tmp_path):
        """Medium-complexity tasks should NOT upgrade to premium."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
                premium_model="gemini-2.5-pro",
            ),
        }
        routing = [ModelRoute(agent="gemini", model="gemini-2.5-flash")]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert model == "gemini-2.5-flash", (
            f"Medium complexity should NOT upgrade, got '{model}'"
        )

    @pytest.mark.asyncio
    async def test_premium_upgrade_skipped_if_banned(self, tmp_path):
        """Premium model should not be used if it's on the ban list."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
                premium_model="gemini-2.5-pro",
            ),
        }
        routing = [ModelRoute(agent="gemini", model="gemini-2.5-flash")]
        cfg = OdinConfig(
            base_agent="gemini",
            task_storage=str(tmp_path / "tasks"),
            log_dir=str(tmp_path / "logs"),
            cost_storage=str(tmp_path / "costs"),
            spec_storage=str(tmp_path / "specs"),
            agents=agents,
            model_routing=routing,
            banned_models=["gemini-2.5-pro"],
        )
        for d in ["tasks", "logs", "costs", "specs"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        orch = Orchestrator(cfg)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested=None,
                quota=None,
            )

        assert model == "gemini-2.5-flash", (
            f"Banned premium model should not be used, got '{model}'"
        )


# ── [mock] Suggested model scenarios ─────────────────────────────────


class TestRouteTaskSuggestedModel:
    """_route_task() should honour the planner's suggested_model when valid."""

    @pytest.mark.asyncio
    async def test_suggested_model_used_when_valid(self, tmp_path):
        """Planner suggests agent + model, both valid → exact model used."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
        }
        routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="gemini", model="gemini-2.0-flash"),
        ]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="gemini",
                quota=None,
                suggested_model="gemini-2.0-flash",
            )

        assert agent == "gemini"
        assert model == "gemini-2.0-flash", (
            f"Should use planner's suggested model, got '{model}'"
        )
        assert "suggested model" in reasoning

    @pytest.mark.asyncio
    async def test_suggested_model_not_in_routing_falls_back(self, tmp_path):
        """Planner suggests a model not in routing → fall back to agent default."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
        }
        routing = [ModelRoute(agent="gemini", model="gemini-2.5-flash")]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="gemini",
                quota=None,
                suggested_model="nonexistent-model",
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-flash", (
            f"Invalid suggested model should fall back to agent default, got '{model}'"
        )
        assert "suggested by planner" in reasoning

    @pytest.mark.asyncio
    async def test_suggested_model_without_agent_ignored(self, tmp_path):
        """suggested_model without suggested agent → tier routing picks."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested=None,
                quota=None,
                suggested_model="gemini-2.0-flash",
            )

        # Should fall through to tier routing
        assert agent in {"gemini", "glm"}
        assert "tier" in reasoning

    @pytest.mark.asyncio
    async def test_suggested_model_agent_unavailable_falls_back(self, tmp_path):
        """Planner suggests agent + model but agent unavailable → tier routing."""
        orch = _make_orchestrator(tmp_path)

        with _mock_availability({"glm"}):
            agent, model, reasoning, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="gemini",
                quota=None,
                suggested_model="gemini-2.0-flash",
            )

        assert agent in {"glm"}, (
            f"Unavailable agent should fall back to tier routing, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_reasoning_distinguishes_model_vs_agent_suggestion(self, tmp_path):
        """Reasoning says 'suggested model' when model was honoured,
        'suggested by planner' when only agent was used."""
        agents = {
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
            ),
        }
        routing = [
            ModelRoute(agent="gemini", model="gemini-2.5-flash"),
            ModelRoute(agent="gemini", model="gemini-2.0-flash"),
        ]
        orch = _make_orchestrator(tmp_path, model_routing=routing, agents=agents)

        # With suggested_model
        with _mock_all_available():
            _, _, reasoning_with, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="gemini",
                quota=None,
                suggested_model="gemini-2.0-flash",
            )

        # Without suggested_model
        with _mock_all_available():
            _, _, reasoning_without, _ar = await orch._route_task(
                required_caps=["writing"],
                complexity="low",
                suggested="gemini",
                quota=None,
            )

        assert "suggested model" in reasoning_with
        assert "suggested by planner" in reasoning_without

