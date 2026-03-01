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
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["writing", "coding"],
                cost_tier=CostTier.LOW,
            ),
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
            ModelRoute(agent="qwen", model="qwen3-coder"),
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
        the task should be assigned to gemini, not qwen (first in routing)."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
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
            agent, model, _reasoning = await orch._route_task(
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
            agent, model, _reasoning = await orch._route_task(
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
            agent, model, _reasoning = await orch._route_task(
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
        got assigned to qwen regardless of suggestion."""
        orch = _make_orchestrator(tmp_path)

        results = []
        with _mock_all_available():
            for suggested in ["qwen", "gemini", "glm", "claude"]:
                agent, model, _reasoning = await orch._route_task(
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
            agent, model, _reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        low_tier_agents = {"qwen", "gemini", "glm"}
        assert agent in low_tier_agents, (
            f"Without suggestion, should pick from LOW tier {low_tier_agents}, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_unavailable_falls_back(self, tmp_path):
        """If suggested agent isn't available, fall back to routing."""
        orch = _make_orchestrator(tmp_path)

        # Gemini is suggested but unavailable
        with _mock_availability({"qwen", "claude", "glm"}):
            agent, model, _reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent in {"qwen", "glm"}, (
            f"Gemini unavailable, should fall back to LOW tier (qwen/glm), got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_missing_caps_falls_back(self, tmp_path):
        """If suggested agent lacks required capabilities, fall back."""
        orch = _make_orchestrator(tmp_path)

        # qwen doesn't have "research" capability
        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
                required_caps=["research"],
                complexity="medium",
                suggested="qwen",
                quota=None,
            )

        assert agent == "gemini", (
            f"Qwen lacks 'research', should fall to gemini, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_disabled_falls_back(self, tmp_path):
        """If suggested agent is disabled, fall back to routing."""
        agents = {
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
            ),
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["writing"],
                cost_tier=CostTier.LOW,
                enabled=False,  # disabled
            ),
        }
        orch = _make_orchestrator(tmp_path, agents=agents)

        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent == "qwen", (
            f"Gemini disabled, should fall back to qwen, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_unknown_falls_back(self, tmp_path):
        """If suggested agent doesn't exist in config, fall back."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="nonexistent_agent",
                quota=None,
            )

        assert agent in {"qwen", "gemini", "glm"}, (
            f"Unknown agent should fall back to LOW tier, got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_over_quota_falls_back(self, tmp_path):
        """If suggested agent is >80% quota (and not high complexity), fall back."""
        orch = _make_orchestrator(tmp_path)

        quota = {
            "gemini": {"usage_pct": 85, "remaining_pct": 15},
            "qwen": {"usage_pct": 10, "remaining_pct": 90},
        }

        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=quota,
            )

        assert agent in {"qwen", "glm"}, (
            f"Gemini over quota, should fall back to LOW tier (qwen/glm), got '{agent}'"
        )

    @pytest.mark.asyncio
    async def test_suggested_agent_over_quota_but_high_complexity_kept(self, tmp_path):
        """High complexity tasks keep the suggested agent even if over quota."""
        orch = _make_orchestrator(tmp_path)

        quota = {
            "gemini": {"usage_pct": 85, "remaining_pct": 15},
            "qwen": {"usage_pct": 10, "remaining_pct": 90},
        }

        with _mock_all_available():
            agent, model, _reasoning = await orch._route_task(
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
            with pytest.raises(RuntimeError, match=r"qwen/qwen3-coder") as exc_info:
                await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
            assert "gemini/gemini-2.5-flash" in str(exc_info.value)


# ── [mock] Tier-based distribution ─────────────────────────────────────


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
                agent, model, reasoning = await orch._route_task(
                    required_caps=["writing"],
                    complexity="medium",
                    suggested=None,
                    quota=None,
                )
                agents_seen[agent] += 1

        # All should be LOW-tier
        assert set(agents_seen.keys()).issubset({"qwen", "gemini", "glm"})
        # Distribution: more than 1 agent should appear
        assert len(agents_seen) > 1, (
            f"Expected distribution across LOW-tier agents, got only: {dict(agents_seen)}"
        )

    @pytest.mark.asyncio
    async def test_cheapest_tier_preferred(self, tmp_path):
        """When LOW and HIGH tier agents are both viable, only LOW tier picked."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested=None,
                quota=None,
            )

        assert agent in {"qwen", "gemini", "glm"}, (
            f"Should pick from LOW tier, not HIGH (claude), got '{agent}'"
        )
        assert "LOW" in reasoning

    @pytest.mark.asyncio
    async def test_reasoning_string_generated(self, tmp_path):
        """routing_reasoning should contain tier, agent count, and agent names."""
        orch = _make_orchestrator(tmp_path)

        with _mock_all_available():
            agent, model, reasoning = await orch._route_task(
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
            agent, model, reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="medium",
                suggested="gemini",
                quota=None,
            )

        assert agent == "gemini"
        assert "suggested by planner" in reasoning


# ── [mock] Premium model upgrade ───────────────────────────────────────


class TestRouteTaskPremiumUpgrade:
    """High-complexity tasks should be upgraded to premium_model when available."""

    @pytest.mark.asyncio
    async def test_high_complexity_upgrades_to_premium(self, tmp_path):
        """A high-complexity task assigned to an agent with premium_model
        should be upgraded."""
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
            agent, model, reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested=None,
                quota=None,
            )

        assert agent == "gemini"
        assert model == "gemini-2.5-pro", (
            f"High complexity should upgrade to premium, got '{model}'"
        )
        assert "premium" in reasoning

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
            agent, model, reasoning = await orch._route_task(
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
            agent, model, reasoning = await orch._route_task(
                required_caps=["writing"],
                complexity="high",
                suggested=None,
                quota=None,
            )

        assert model == "gemini-2.5-flash", (
            f"Banned premium model should not be used, got '{model}'"
        )

