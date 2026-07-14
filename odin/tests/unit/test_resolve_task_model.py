"""Tests for Orchestrator._resolve_task_model().

Closes the gap where tasks without ``selected_model`` (e.g. those created
by bootstrap scripts or dispatched manually) ended up running without a
``-m`` flag — making opencode-family CLIs hang waiting on stdin.

Tags:
- [mock] — mocked TaskIt routing-config backend
- [simple] — pure logic, no I/O otherwise
"""

import logging
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator
from odin.taskit.models import Task


def _make_orchestrator(tmp_path, agents=None) -> Orchestrator:
    """Minimal orchestrator setup; no backend unless the caller injects one."""
    task_dir = tmp_path / "tasks"
    log_dir = tmp_path / "logs"
    cost_dir = tmp_path / "costs"
    spec_dir = tmp_path / "specs"
    for d in (task_dir, log_dir, cost_dir, spec_dir):
        d.mkdir(parents=True, exist_ok=True)

    if agents is None:
        agents = {
            "glm": AgentConfig(
                cli_command="opencode",
                capabilities=["coding"],
                cost_tier=CostTier.LOW,
                default_model="zai-coding-plan/glm-4.7",
                premium_model="zai-coding-plan/glm-5.1",
            ),
            "minimax": AgentConfig(
                cli_command="opencode",
                capabilities=["coding"],
                cost_tier=CostTier.LOW,
                default_model="minimax-coding-plan/MiniMax-M2.7",
            ),
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["reasoning", "coding"],
                cost_tier=CostTier.HIGH,
                default_model="claude-sonnet-4-6",
                premium_model="claude-opus-4-7",
            ),
        }

    cfg = OdinConfig(
        base_agent="claude",
        board_backend="local",  # avoid trying to reach a real TaskIt instance
        task_storage=str(task_dir),
        log_dir=str(log_dir),
        cost_storage=str(cost_dir),
        spec_storage=str(spec_dir),
        agents=agents,
    )
    return Orchestrator(cfg)


def _make_task(metadata=None) -> Task:
    """Build a Task with the given metadata (model set or unset)."""
    return Task(
        id="test-task-1",
        title="Test task",
        description="Test description",
        metadata=metadata or {},
    )


def _make_routing_config(agents):
    """Shape the same response TaskIt's /routing-config/ would return."""
    return {"agents": agents}


# ── selected_model / task.model precedence ─────────────────────────────


class TestResolveTaskModelExplicit:
    """When the task already carries a model, it's used unchanged."""

    def test_selected_model_in_metadata_is_unchanged(self, tmp_path):
        """Explicit selected_model wins over every fallback."""
        orch = _make_orchestrator(tmp_path)
        task = _make_task(metadata={"selected_model": "zai-coding-plan/glm-5.1"})

        result = orch._resolve_task_model(task, agent_name="glm")

        assert result == "zai-coding-plan/glm-5.1"

    def test_explicit_model_logs_nothing_about_resolution(self, tmp_path, caplog):
        """The INFO "model resolved from agent default" log must NOT fire
        when the task already has a model — that's noise and misleading."""
        orch = _make_orchestrator(tmp_path)
        task = _make_task(metadata={"selected_model": "zai-coding-plan/glm-5.1"})

        with caplog.at_level(logging.INFO, logger="odin.orchestrator"):
            orch._resolve_task_model(task, agent_name="glm")

        resolution_logs = [
            r for r in caplog.records if "model resolved from agent default" in r.message
        ]
        assert resolution_logs == [], (
            "Should not log 'model resolved from agent default' when "
            "selected_model is already set."
        )

    def test_task_dot_model_attribute_used_as_fallback(self, tmp_path):
        """If a backend exposed the model as task.model, prefer it next.

        The Taskit REST API exposes ``model_name`` (already merged into
        ``metadata["selected_model"]`` by the backend). Some backends may
        additionally surface it as ``task.model``. Use a duck-typed object
        so the attribute path can be exercised without breaking Pydantic.
        """
        orch = _make_orchestrator(tmp_path)

        class _BackendTask:
            id = "duck-task"
            metadata = {}
            model = "zai-coding-plan/glm-4.7"

        result = orch._resolve_task_model(_BackendTask(), agent_name="glm")

        assert result == "zai-coding-plan/glm-4.7"

    def test_no_model_at_all_returns_none(self, tmp_path):
        """When every source is empty, graceful None — current behavior.

        Use an orchestrator with no agents configured for ``glm`` so neither
        the lineup nor the config has anything to offer.
        """
        # Empty-agent orchestrator — no config fallback either.
        task_dir = tmp_path / "tasks"
        (task_dir).mkdir(parents=True, exist_ok=True)
        cfg = OdinConfig(
            base_agent="claude",
            board_backend="local",
            task_storage=str(task_dir),
            log_dir=str(tmp_path / "logs"),
            cost_storage=str(tmp_path / "costs"),
            spec_storage=str(tmp_path / "specs"),
            agents={},
        )
        orch = Orchestrator(cfg)
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="unknown-agent")

        assert result is None

    def test_unknown_agent_returns_none(self, tmp_path):
        """An agent name not in either source yields None, not a KeyError."""
        orch = _make_orchestrator(tmp_path)
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="made-up-agent")

        assert result is None

    def test_none_agent_name_does_not_crash(self, tmp_path):
        """Passing agent_name=None degrades gracefully — should not blow up."""
        orch = _make_orchestrator(tmp_path)
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name=None)

        assert result is None


# ── Lineup resolution (mirrors _route_task_api source) ────────────────


class TestResolveTaskModelFromLineup:
    """When selected_model is unset, prefer the TaskIt routing-config lineup."""

    def _install_backend(self, orch, agents):
        """Install a fake backend that exposes fetch_routing_config()."""
        backend = MagicMock()
        backend.fetch_routing_config.return_value = _make_routing_config(agents)
        orch._backend = backend

    def test_lineup_default_used_when_selected_model_missing(self, tmp_path):
        """A task with no model should fall back to the agent's lineup
        default — the same value _route_task_api() picks."""
        orch = _make_orchestrator(tmp_path)
        self._install_backend(
            orch,
            [
                {
                    "name": "glm",
                    "default_model": "zai-coding-plan/glm-5.2",
                    "premium_model": "zai-coding-plan/glm-5.1",
                    "models": [
                        {"name": "zai-coding-plan/glm-5.2", "enabled": True},
                    ],
                },
            ],
        )
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="glm")

        assert result == "zai-coding-plan/glm-5.2"

    def test_lineup_default_skipped_when_agent_unknown_to_lineup(self, tmp_path):
        """If the lineup doesn't list the agent, fall through to config."""
        orch = _make_orchestrator(tmp_path)
        self._install_backend(orch, [{"name": "claude", "default_model": "..."}])
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="glm")

        # Falls through to config default
        assert result == "zai-coding-plan/glm-4.7"

    def test_api_unavailable_falls_back_to_config(self, tmp_path):
        """If fetch_routing_config returns None, the agent config default wins."""
        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.fetch_routing_config.return_value = None
        orch._backend = backend
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="glm")

        assert result == "zai-coding-plan/glm-4.7"

    def test_backend_raises_returns_none_gracefully(self, tmp_path):
        """A backend that raises during fetch must not propagate."""
        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.fetch_routing_config.side_effect = RuntimeError("boom")
        orch._backend = backend
        task = _make_task(metadata={})

        # Should not raise — catches and returns None.
        result = orch._resolve_task_model(task, agent_name="glm")

        # Falls through to config default since the API blow-up is silent.
        assert result == "zai-coding-plan/glm-4.7"

    def test_config_default_used_when_agent_has_no_lineup_default(self, tmp_path):
        """An agent in the lineup without a default_model falls through."""
        orch = _make_orchestrator(tmp_path)
        self._install_backend(
            orch,
            [
                {
                    "name": "glm",
                    # No default_model field — backend didn't seed one.
                    "models": [],
                },
            ],
        )
        task = _make_task(metadata={})

        result = orch._resolve_task_model(task, agent_name="glm")

        # Falls through to config default.
        assert result == "zai-coding-plan/glm-4.7"

    def test_resolution_logs_info_with_task_id(self, tmp_path, caplog):
        """INFO log format: '[task:N] model resolved from agent default: X'."""
        orch = _make_orchestrator(tmp_path)
        self._install_backend(
            orch,
            [
                {
                    "name": "glm",
                    "default_model": "zai-coding-plan/glm-5.2",
                    "models": [],
                },
            ],
        )
        task = _make_task(metadata={})

        with caplog.at_level(logging.INFO, logger="odin.orchestrator"):
            orch._resolve_task_model(task, agent_name="glm")

        resolution_logs = [
            r for r in caplog.records if "model resolved from agent default" in r.message
        ]
        assert len(resolution_logs) == 1
        assert "zai-coding-plan/glm-5.2" in resolution_logs[0].message
        assert "test-task-1" in resolution_logs[0].message


# ── Caching ────────────────────────────────────────────────────────────


class TestLineupCache:
    """The lineup lookup must not re-hit the backend for every task."""

    def test_repeated_lookup_uses_cache(self, tmp_path):
        """Two tasks for the same agent = one backend call."""
        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.fetch_routing_config.return_value = _make_routing_config(
            [
                {
                    "name": "glm",
                    "default_model": "zai-coding-plan/glm-5.2",
                    "models": [],
                }
            ]
        )
        orch._backend = backend

        orch._resolve_task_model(_make_task(metadata={}), agent_name="glm")
        orch._resolve_task_model(_make_task(metadata={}), agent_name="glm")

        # First call hits the backend; second is served from cache.
        assert backend.fetch_routing_config.call_count == 1


# ── Acceptance: opencode `-m` flag ────────────────────────────────────


class TestHarnessReceivesModelFlag:
    """AC: a task with model unset, assigned to glm, builds an opencode
    command containing `-m <glm default>`."""

    def test_glm_task_without_model_passes_m_flag_to_harness(self, tmp_path):
        """Drive the OpenCode-backed GLM harness with a no-model task and
        confirm the resolved default lands in the final CLI argv."""
        from odin.harnesses.glm import GLMHarness

        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.fetch_routing_config.return_value = _make_routing_config(
            [
                {
                    "name": "glm",
                    "default_model": "zai-coding-plan/glm-5.2",
                    "models": [{"name": "zai-coding-plan/glm-5.2", "enabled": True}],
                }
            ]
        )
        orch._backend = backend

        task = _make_task(metadata={})

        # Step 1 — resolution produces the lineup default.
        resolved = orch._resolve_task_model(task, agent_name="glm")
        assert resolved == "zai-coding-plan/glm-5.2"

        # Step 2 — that value lands as `-m` on the harness command line.
        cfg = orch.config.agents["glm"]
        harness = GLMHarness(cfg)
        cmd = harness.build_execute_command(
            "test prompt",
            {"working_dir": str(tmp_path), "model": resolved},
        )

        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "zai-coding-plan/glm-5.2"

    def test_explicit_selected_model_still_passes_through(self, tmp_path):
        """No behavior change when selected_model is already set."""
        from odin.harnesses.glm import GLMHarness

        orch = _make_orchestrator(tmp_path)
        # Even with a lineup installed, the explicit value wins.
        backend = MagicMock()
        backend.fetch_routing_config.return_value = _make_routing_config(
            [{"name": "glm", "default_model": "zai-coding-plan/glm-5.2", "models": []}]
        )
        orch._backend = backend

        task = _make_task(metadata={"selected_model": "zai-coding-plan/glm-4.7"})

        resolved = orch._resolve_task_model(task, agent_name="glm")
        assert resolved == "zai-coding-plan/glm-4.7"

        cfg = orch.config.agents["glm"]
        harness = GLMHarness(cfg)
        cmd = harness.build_execute_command(
            "test prompt",
            {"working_dir": str(tmp_path), "model": resolved},
        )
        assert cmd[cmd.index("-m") + 1] == "zai-coding-plan/glm-4.7"

    def test_minimax_task_without_model_passes_m_flag_to_harness(self, tmp_path):
        """Same acceptance pattern for the other opencode-family agent."""
        from odin.harnesses.minimax import MiniMaxHarness

        orch = _make_orchestrator(tmp_path)
        backend = MagicMock()
        backend.fetch_routing_config.return_value = _make_routing_config(
            [
                {
                    "name": "minimax",
                    "default_model": "minimax-coding-plan/MiniMax-M3",
                    "models": [],
                }
            ]
        )
        orch._backend = backend

        task = _make_task(metadata={})

        resolved = orch._resolve_task_model(task, agent_name="minimax")
        assert resolved == "minimax-coding-plan/MiniMax-M3"

        cfg = orch.config.agents["minimax"]
        harness = MiniMaxHarness(cfg)
        cmd = harness.build_execute_command(
            "test prompt",
            {"working_dir": str(tmp_path), "model": resolved},
        )
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "minimax-coding-plan/MiniMax-M3"
