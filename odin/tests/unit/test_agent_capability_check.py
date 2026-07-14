"""Tests for the pre-flight agent CLI capability check.

A codex-only first-timer (no claude installed) gets a plain RuntimeError
naming the fix — not a deep subprocess crash — when the default
``base_agent="claude"`` points at a CLI that isn't on PATH.

Tags: [pure] + [mock] — mocks ``shutil.which`` only; no real subprocesses.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator


# ── helpers ───────────────────────────────────────────────────────────────


def _agents(**overrides) -> dict[str, AgentConfig]:
    """Five fable agents with realistic CLI defaults."""
    base = {
        "claude": AgentConfig(cli_command="claude", cost_tier=CostTier.HIGH, enabled=True),
        "codex": AgentConfig(cli_command="codex", cost_tier=CostTier.MEDIUM, enabled=True),
        "gemini": AgentConfig(cli_command="gemini", cost_tier=CostTier.LOW, enabled=True),
        "glm": AgentConfig(cli_command="opencode", cost_tier=CostTier.LOW, enabled=True),
        "minimax": AgentConfig(cli_command="opencode", cost_tier=CostTier.LOW, enabled=True),
    }
    base.update(overrides)
    return base


def _make_orchestrator(tmp_path, agents=None, base_agent="claude") -> Orchestrator:
    """Orchestrator with real tmp dirs and local backend."""
    for sub in ("tasks", "logs", "costs", "specs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    cfg = OdinConfig(
        base_agent=base_agent,
        board_backend="local",
        task_storage=str(tmp_path / "tasks"),
        log_dir=str(tmp_path / "logs"),
        cost_storage=str(tmp_path / "costs"),
        spec_storage=str(tmp_path / "specs"),
        agents=agents or _agents(),
    )
    return Orchestrator(cfg)


# ─ _resolve_agent_cli ───────────────────────────────────────────────────


class TestResolveAgentCli:
    def test_uses_explicit_cli_command(self):
        from odin.orchestrator import _resolve_agent_cli
        cfg = AgentConfig(cli_command="/custom/claude")
        assert _resolve_agent_cli("claude", cfg) == "/custom/claude"

    def test_falls_back_to_default_for_claude(self):
        from odin.orchestrator import _resolve_agent_cli
        assert _resolve_agent_cli("claude", AgentConfig()) == "claude"

    def test_falls_back_to_opencode_for_glm(self):
        from odin.orchestrator import _resolve_agent_cli
        assert _resolve_agent_cli("glm", AgentConfig()) == "opencode"

    def test_falls_back_to_opencode_for_minimax(self):
        from odin.orchestrator import _resolve_agent_cli
        assert _resolve_agent_cli("minimax", AgentConfig()) == "opencode"

    def test_falls_back_to_agent_name_for_unknown(self):
        from odin.orchestrator import _resolve_agent_cli
        assert _resolve_agent_cli("agy", AgentConfig()) == "agy"


# ─ _list_available_agents ───────────────────────────────────────────────


class TestListAvailableAgents:
    def test_returns_only_agents_with_cli_on_path(self):
        from odin.orchestrator import _list_available_agents
        config = OdinConfig(base_agent="claude", board_backend="local", agents=_agents())

        def fake_which(cli):
            return f"/usr/bin/{cli}" if cli == "codex" else None

        with patch("odin.orchestrator.shutil.which", side_effect=fake_which):
            result = _list_available_agents(config)
        assert result == ["codex"]

    def test_returns_multiple_when_multiple_installed(self):
        from odin.orchestrator import _list_available_agents
        config = OdinConfig(base_agent="claude", board_backend="local", agents=_agents())

        def fake_which(cli):
            if cli in ("codex", "opencode"):
                return f"/usr/bin/{cli}"
            return None

        with patch("odin.orchestrator.shutil.which", side_effect=fake_which):
            result = _list_available_agents(config)
        assert "codex" in result
        assert "glm" in result
        assert "minimax" in result
        assert "claude" not in result

    def test_skips_disabled_agents(self):
        from odin.orchestrator import _list_available_agents
        agents = _agents(claude=AgentConfig(cli_command="claude", enabled=False))
        config = OdinConfig(base_agent="claude", board_backend="local", agents=agents)
        with patch("odin.orchestrator.shutil.which", return_value="/usr/bin/x"):
            result = _list_available_agents(config)
        assert "claude" not in result

    def test_empty_when_none_installed(self):
        from odin.orchestrator import _list_available_agents
        config = OdinConfig(base_agent="claude", board_backend="local", agents=_agents())
        with patch("odin.orchestrator.shutil.which", return_value=None):
            assert _list_available_agents(config) == []


# ─ _assert_agent_cli_available ──────────────────────────────────────────


class TestAssertAgentCliAvailable:
    def test_passes_silently_when_cli_present(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with patch("odin.orchestrator.shutil.which", return_value="/usr/bin/claude"):
            orch._assert_agent_cli_available("claude", action="planning")

    def test_raises_clear_error_when_cli_missing(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with patch("odin.orchestrator.shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                orch._assert_agent_cli_available("claude", action="planning")
        msg = str(exc_info.value).lower()
        assert "claude" in msg
        assert "not installed" in msg or "not found" in msg

    def test_error_names_available_alternatives(self, tmp_path):
        """A codex-only user sees codex as the available alternative."""
        orch = _make_orchestrator(tmp_path)

        def fake_which(cli):
            return f"/usr/bin/{cli}" if cli == "codex" else None

        with patch("odin.orchestrator.shutil.which", side_effect=fake_which):
            with pytest.raises(RuntimeError) as exc_info:
                orch._assert_agent_cli_available("claude", action="planning")
        msg = str(exc_info.value).lower()
        assert "codex" in msg
        assert "base_agent" in msg or "config" in msg

    def test_error_points_to_odin_doctor(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with patch("odin.orchestrator.shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                orch._assert_agent_cli_available("claude", action="planning")
        assert "odin doctor" in str(exc_info.value).lower()

    def test_error_when_no_agent_available(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with patch("odin.orchestrator.shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                orch._assert_agent_cli_available("claude", action="planning")
        msg = str(exc_info.value).lower()
        assert "no agent" in msg or "none" in msg

    def test_action_label_appears_in_message(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        with patch("odin.orchestrator.shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                orch._assert_agent_cli_available("claude", action="execution")
        assert "execution" in str(exc_info.value).lower()


# ─ dispatch points call the check ─────────────────────────────────────────


class TestDecomposeCallsCheck:
    """_decompose raises a clear error — not a subprocess crash — when the
    planning agent's CLI is missing."""

    @pytest.mark.asyncio
    async def test_decompose_raises_before_harness_when_cli_missing(self, tmp_path):
        orch = _make_orchestrator(tmp_path, base_agent="claude")

        harness_called = []
        import odin.orchestrator as orch_mod
        original = orch_mod.get_harness

        def tracking_get_harness(name, cfg):
            harness_called.append(name)
            return original(name, cfg)

        with patch("odin.orchestrator.shutil.which", return_value=None), \
             patch.object(orch_mod, "get_harness", side_effect=tracking_get_harness):
            with pytest.raises(RuntimeError, match="claude"):
                await orch._decompose("prompt", str(tmp_path), "sp_test")
        assert harness_called == [], "get_harness must not be reached when CLI is missing"

    @pytest.mark.asyncio
    async def test_decompose_proceeds_when_cli_present(self, tmp_path):
        """When the CLI IS on PATH, the check passes silently and _decompose
        proceeds to harness execution (mocked here)."""
        from unittest.mock import AsyncMock, MagicMock
        orch = _make_orchestrator(tmp_path, base_agent="codex")
        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=MagicMock(
            success=True, output="[]", duration_ms=1, agent="codex", error=None,
        ))
        mock_harness.build_execute_command.return_value = None

        with patch("odin.orchestrator.shutil.which", return_value="/usr/bin/codex"), \
             patch("odin.orchestrator.get_harness", return_value=mock_harness):
            result = await orch._decompose("prompt", str(tmp_path), "sp_test")
        assert result.success
