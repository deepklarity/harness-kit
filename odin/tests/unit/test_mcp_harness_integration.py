"""Unit tests for MCP config generation and harness CLI flag injection.

Tests verify:
- Orchestrator generates correct per-CLI MCP config files
- Claude harness adds --mcp-config flag (only CLI that supports it)
- Gemini/Qwen harnesses do NOT add --mcp-config (auto-discover from project files)
- Codex harness ignores MCP config (uses .codex/config.toml)
- Config contains correct env vars (URL, auth token, task ID, author)
- Per-CLI format correctness (JSON, TOML, OpenCode structure)
- Generated configs use tool names from the server (single source of truth)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.gemini import GeminiHarness
from odin.harnesses.qwen import QwenHarness
from odin.harnesses.codex import CodexHarness
from odin.mcps.taskit_mcp.config import claude_tool_names, tool_names
from odin.models import AgentConfig, CostTier, OdinConfig, TaskItConfig


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def agent_config():
    return AgentConfig(
        cli_command="claude",
        capabilities=["coding"],
        cost_tier=CostTier.HIGH,
    )


@pytest.fixture
def gemini_config():
    return AgentConfig(
        cli_command="gemini",
        capabilities=["coding"],
        cost_tier=CostTier.LOW,
    )


@pytest.fixture
def qwen_config():
    return AgentConfig(
        cli_command="qwen",
        capabilities=["coding"],
        cost_tier=CostTier.LOW,
    )


@pytest.fixture
def codex_config():
    return AgentConfig(
        cli_command="codex",
        capabilities=["coding"],
        cost_tier=CostTier.MEDIUM,
    )


# ── Harness MCP flag injection ───────────────────────────────


class TestClaudeHarnessMcpConfig:
    """Claude Code supports --mcp-config flag."""

    def test_adds_mcp_config_flag(self, agent_config):
        harness = ClaudeHarness(agent_config)
        context = {"mcp_config": "/tmp/mcp_123.json"}
        cmd = harness.build_execute_command("do something", context)
        assert "--mcp-config" in cmd
        assert "/tmp/mcp_123.json" in cmd

    def test_no_mcp_config_no_flag(self, agent_config):
        harness = ClaudeHarness(agent_config)
        context = {}
        cmd = harness.build_execute_command("do something", context)
        assert "--mcp-config" not in cmd

    def test_mcp_config_in_interactive_command(self, agent_config):
        harness = ClaudeHarness(agent_config)
        context = {"mcp_config": "/tmp/mcp_plan.json"}
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", context)
        assert "--mcp-config" in cmd
        assert "/tmp/mcp_plan.json" in cmd

    def test_mcp_config_with_model(self, agent_config):
        harness = ClaudeHarness(agent_config)
        context = {
            "model": "claude-sonnet-4-5",
            "mcp_config": "/tmp/mcp.json",
        }
        cmd = harness.build_execute_command("task", context)
        assert "--model" in cmd
        assert "--mcp-config" in cmd

    def test_adds_allowed_tools_flag(self, agent_config):
        """--allowedTools grants MCP tool permissions in -p mode."""
        harness = ClaudeHarness(agent_config)
        context = {
            "mcp_config": "/tmp/mcp.json",
            "mcp_allowed_tools": [
                "mcp__taskit__taskit_add_comment",
                "mcp__taskit__taskit_add_attachment",
            ],
        }
        cmd = harness.build_execute_command("task", context)
        assert "--allowedTools" in cmd
        allowed_idx = cmd.index("--allowedTools")
        allowed_value = cmd[allowed_idx + 1]
        assert "mcp__taskit__taskit_add_comment" in allowed_value
        assert "mcp__taskit__taskit_add_attachment" in allowed_value

    def test_no_allowed_tools_without_context(self, agent_config):
        harness = ClaudeHarness(agent_config)
        cmd = harness.build_execute_command("task", {})
        assert "--allowedTools" not in cmd

    def test_allowed_tools_in_interactive_command(self, agent_config):
        harness = ClaudeHarness(agent_config)
        context = {
            "mcp_config": "/tmp/mcp.json",
            "mcp_allowed_tools": ["mcp__taskit__taskit_add_comment"],
        }
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", context)
        assert "--allowedTools" in cmd


class TestGeminiHarnessNoMcpFlag:
    """Gemini CLI has no --mcp-config flag — uses .gemini/settings.json."""

    def test_no_mcp_flag_even_with_config(self, gemini_config):
        harness = GeminiHarness(gemini_config)
        context = {"mcp_config": "/tmp/mcp_456.json"}
        cmd = harness.build_execute_command("do something", context)
        assert "--mcp-config" not in cmd

    def test_no_mcp_flag_empty_context(self, gemini_config):
        harness = GeminiHarness(gemini_config)
        cmd = harness.build_execute_command("do something", {})
        assert "--mcp-config" not in cmd

    def test_interactive_no_mcp_flag(self, gemini_config):
        harness = GeminiHarness(gemini_config)
        context = {"mcp_config": "/tmp/mcp.json"}
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", context)
        assert "--mcp-config" not in cmd


class TestQwenHarnessNoMcpFlag:
    """Qwen CLI has no --mcp-config flag — uses .qwen/settings.json."""

    def test_no_mcp_flag_even_with_config(self, qwen_config):
        harness = QwenHarness(qwen_config)
        context = {"mcp_config": "/tmp/mcp_789.json"}
        cmd = harness.build_execute_command("do something", context)
        assert "--mcp-config" not in cmd

    def test_no_mcp_flag_empty_context(self, qwen_config):
        harness = QwenHarness(qwen_config)
        cmd = harness.build_execute_command("do something", {})
        assert "--mcp-config" not in cmd

    def test_interactive_no_mcp_flag(self, qwen_config):
        harness = QwenHarness(qwen_config)
        context = {"mcp_config": "/tmp/mcp.json"}
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", context)
        assert "--mcp-config" not in cmd


class TestCodexHarnessMcpFlags:
    """Codex harness injects -c flags for MCP config (bypasses trust check)."""

    def test_no_mcp_flag_even_with_config(self, codex_config):
        harness = CodexHarness(codex_config)
        context = {"mcp_config": "/tmp/mcp.json"}
        cmd = harness.build_execute_command("do something", context)
        assert "--mcp-config" not in cmd

    def test_injects_c_flags_when_mcp_env_present(self, codex_config):
        """When mcp_env is in context, Codex adds -c flags for MCP server config."""
        harness = CodexHarness(codex_config)
        context = {
            "mcp_env": {
                "TASKIT_URL": "http://localhost:8000",
                "TASKIT_AUTH_TOKEN": "tok-123",
                "TASKIT_TASK_ID": "42",
                "TASKIT_AUTHOR_EMAIL": "codex@odin.agent",
                "TASKIT_AUTHOR_LABEL": "codex",
            }
        }
        cmd = harness.build_execute_command("do something", context)

        # Should have -c for the command itself
        assert "-c" in cmd
        assert 'mcp_servers.taskit.command="taskit-mcp"' in cmd

        # Should have -c for each env var
        assert 'mcp_servers.taskit.env.TASKIT_URL="http://localhost:8000"' in cmd
        assert 'mcp_servers.taskit.env.TASKIT_TASK_ID="42"' in cmd
        assert 'mcp_servers.taskit.env.TASKIT_AUTH_TOKEN="tok-123"' in cmd

        # Prompt should still be the last argument
        assert cmd[-1] == "do something"

    def test_no_c_flags_without_mcp_env(self, codex_config):
        """Without mcp_env, no -c flags are added."""
        harness = CodexHarness(codex_config)
        context = {}
        cmd = harness.build_execute_command("do something", context)
        assert "-c" not in cmd

    def test_json_flag_always_present(self, codex_config):
        """Codex always gets --json for JSONL streaming output."""
        harness = CodexHarness(codex_config)
        cmd = harness.build_execute_command("do something", {})
        assert "--json" in cmd


# ── MCP config generation (orchestrator) ──────────────────────


class TestMcpConfigGeneration:
    """Test Orchestrator._generate_mcp_config() per-CLI format generation."""

    def _make_orchestrator(self, tmp_path, with_auth=True):
        """Create an Orchestrator with mocked backend for testing."""
        from odin.orchestrator import Orchestrator

        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=TaskItConfig(base_url="http://localhost:8000"),
            log_dir=str(tmp_path / "logs"),
        )

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        if with_auth:
            mock_auth = MagicMock()
            mock_auth.get_token.return_value = "test-bearer-token"
            mock_client = MagicMock()
            mock_client.auth = mock_auth
            mock_backend = MagicMock()
            mock_backend._client = mock_client
            orch._backend = mock_backend
        else:
            orch._backend = None

        return orch

    # ── Claude (returns path for --mcp-config) ──

    def test_claude_returns_config_path(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        assert config_path is not None
        assert "mcp_42.json" in config_path

    def test_claude_generates_valid_json(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        data = json.loads(Path(config_path).read_text())
        assert "mcpServers" in data
        assert data["mcpServers"]["taskit"]["command"] == "taskit-mcp"

    def test_claude_config_has_correct_env_vars(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        data = json.loads(Path(config_path).read_text())
        env = data["mcpServers"]["taskit"]["env"]
        assert env["TASKIT_URL"] == "http://localhost:8000"
        assert env["TASKIT_AUTH_TOKEN"] == "test-bearer-token"
        assert env["TASKIT_TASK_ID"] == "42"
        assert env["TASKIT_AUTHOR_EMAIL"] == "claude@odin.agent"
        assert env["TASKIT_AUTHOR_LABEL"] == "claude"

    def test_claude_config_has_no_alwaysAllow(self, tmp_path):
        """Claude Code does NOT support alwaysAllow in .mcp.json.

        Tool permissions are granted via --allowedTools CLI flag instead.
        The harness injects this flag in build_execute_command().
        """
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        data = json.loads(Path(config_path).read_text())
        assert "alwaysAllow" not in data["mcpServers"]["taskit"]

    # ── Gemini (writes .gemini/settings.json, returns None) ──

    def test_gemini_returns_none(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        result = orch._generate_mcp_config(
            "42", "gemini", tmp_path / "logs", working_dir=str(tmp_path),
        )
        assert result is None

    def test_gemini_writes_settings_json(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "gemini", tmp_path / "logs", working_dir=str(tmp_path),
        )
        config_path = tmp_path / ".gemini" / "settings.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTHOR_EMAIL"] == "gemini@odin.agent"

    def test_gemini_has_trust_true(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "gemini", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["trust"] is True

    # ── Qwen (writes .qwen/settings.json, returns None) ──

    def test_qwen_returns_none(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        result = orch._generate_mcp_config(
            "42", "qwen", tmp_path / "logs", working_dir=str(tmp_path),
        )
        assert result is None

    def test_qwen_writes_settings_json(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "qwen", tmp_path / "logs", working_dir=str(tmp_path),
        )
        config_path = tmp_path / ".qwen" / "settings.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTHOR_EMAIL"] == "qwen@odin.agent"

    def test_qwen_has_trust_true(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "qwen", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["trust"] is True

    # ── Codex (writes .codex/config.toml, returns None) ──

    def test_codex_returns_none(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        result = orch._generate_mcp_config(
            "42", "codex", tmp_path / "logs", working_dir=str(tmp_path),
        )
        assert result is None

    def test_codex_writes_toml(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "codex", tmp_path / "logs", working_dir=str(tmp_path),
        )
        config_path = tmp_path / ".codex" / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert "[mcp_servers.taskit]" in content
        assert 'command = "taskit-mcp"' in content
        assert 'TASKIT_URL = "http://localhost:8000"' in content

    # ── MiniMax / kilo (writes opencode.json — same format as GLM) ──

    def test_minimax_writes_opencode_json(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        config_path = tmp_path / "opencode.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert "mcp" in data
        assert data["mcp"]["taskit"]["environment"]["TASKIT_AUTHOR_EMAIL"] == "minimax@odin.agent"

    def test_minimax_opencode_format(self, tmp_path):
        """kilo CLI reads opencode.json with 'mcp' key and 'environment' (not 'env')."""
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert data["mcp"]["taskit"]["type"] == "local"
        assert data["mcp"]["taskit"]["command"] == ["taskit-mcp"]
        assert "environment" in data["mcp"]["taskit"]

    def test_minimax_has_permission_allow(self, tmp_path):
        """kilo --auto mode silently blocks un-approved tools; permission must be set."""
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert "permission" in data
        assert data["permission"]["taskit_add_comment"] == "allow"
        assert data["permission"]["taskit_add_attachment"] == "allow"

    # ── OpenCode / GLM (writes opencode.json with different structure) ──

    def test_glm_writes_opencode_json(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "glm", tmp_path / "logs", working_dir=str(tmp_path),
        )
        config_path = tmp_path / "opencode.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        # OpenCode uses "mcp" key (not "mcpServers") and different structure
        assert "mcp" in data
        assert data["mcp"]["taskit"]["type"] == "local"
        assert data["mcp"]["taskit"]["command"] == ["taskit-mcp"]
        assert data["mcp"]["taskit"]["environment"]["TASKIT_AUTHOR_EMAIL"] == "glm@odin.agent"

    def test_glm_has_permission_allow(self, tmp_path):
        """GLM also uses opencode format — needs same permission auto-approve."""
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "glm", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert data["permission"]["taskit_add_comment"] == "allow"
        assert data["permission"]["taskit_add_attachment"] == "allow"

    # ── Shared behavior ──

    def test_without_taskit_still_has_mobile_and_chrome(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch.config.taskit = None
        result = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        # Mobile and chrome-devtools are in default mcps, so config is still generated
        assert result is not None
        data = json.loads(Path(result).read_text())
        assert "taskit" not in data["mcpServers"]
        assert "mobile" in data["mcpServers"]
        assert "chrome-devtools" in data["mcpServers"]

    def test_no_auth_sets_empty_token(self, tmp_path):
        orch = self._make_orchestrator(tmp_path, with_auth=False)
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        data = json.loads(Path(config_path).read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTH_TOKEN"] == ""

    def test_auth_failure_sets_empty_token(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._backend._client.auth.get_token.side_effect = RuntimeError("auth failed")
        config_path = orch._generate_mcp_config(
            "42", "claude", tmp_path / "logs"
        )
        data = json.loads(Path(config_path).read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTH_TOKEN"] == ""

    def test_unknown_agent_falls_back_to_claude_format(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config(
            "42", "unknown_agent", tmp_path / "logs"
        )
        assert config_path is not None
        data = json.loads(Path(config_path).read_text())
        assert "mcpServers" in data


# ── CLI _generate_all_mcp_configs ─────────────────────────────


class TestGenerateAllMcpConfigs:
    """Test the CLI helper that generates all 6 config files at once."""

    def test_creates_all_six_files(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        env = {"TASKIT_URL": "http://localhost:8000"}
        created = _generate_all_mcp_configs(tmp_path, env)

        assert len(created) == 6
        assert (tmp_path / ".mcp.json").exists()
        assert (tmp_path / ".gemini" / "settings.json").exists()
        assert (tmp_path / ".qwen" / "settings.json").exists()
        assert (tmp_path / ".codex" / "config.toml").exists()
        assert (tmp_path / ".kilocode" / "mcp.json").exists()
        assert (tmp_path / "opencode.json").exists()

    def test_claude_format(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"]["taskit"]["command"] == "taskit-mcp"
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_URL"] == "http://test:8000"

    def test_codex_toml_format(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.taskit]" in content
        assert 'command = "taskit-mcp"' in content

    def test_opencode_format(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert "mcp" in data
        assert data["mcp"]["taskit"]["type"] == "local"
        assert data["mcp"]["taskit"]["command"] == ["taskit-mcp"]
        # OpenCode uses "environment" (not "env") per its schema
        assert "environment" in data["mcp"]["taskit"]
        assert "env" not in data["mcp"]["taskit"]
        assert data["mcp"]["taskit"]["environment"]["TASKIT_URL"] == "http://test:8000"

    def test_gemini_trust_true(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["trust"] is True

    def test_qwen_trust_true(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["trust"] is True

    def test_opencode_used_for_kilo_and_glm(self, tmp_path):
        """Both kilo (minimax) and opencode (glm) read opencode.json."""
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert data["mcp"]["taskit"]["type"] == "local"
        assert data["mcp"]["taskit"]["command"] == ["taskit-mcp"]
        assert data["mcp"]["taskit"]["environment"]["TASKIT_URL"] == "http://test:8000"

    def test_opencode_has_permission_allow(self, tmp_path):
        """opencode.json must have permission block for --auto mode."""
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert data["permission"]["taskit_add_comment"] == "allow"
        assert data["permission"]["taskit_add_attachment"] == "allow"

    def test_claude_has_no_alwaysAllow(self, tmp_path):
        """Claude Code uses --allowedTools CLI flag, not alwaysAllow in config."""
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "alwaysAllow" not in data["mcpServers"]["taskit"]

    def test_env_vars_propagated(self, tmp_path):
        from odin.cli import _generate_all_mcp_configs

        env = {"TASKIT_URL": "http://test:8000", "TASKIT_TASK_ID": "42"}
        _generate_all_mcp_configs(tmp_path, env)

        # Verify a sample config has both env vars
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_TASK_ID"] == "42"

    def test_generated_configs_use_server_tools(self, tmp_path):
        """Generated permission/approval lists must match server tools."""
        from odin.cli import _generate_all_mcp_configs

        _generate_all_mcp_configs(tmp_path, {"TASKIT_URL": "http://test:8000"})

        # Claude config has no alwaysAllow (uses --allowedTools CLI flag)
        claude_data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "alwaysAllow" not in claude_data["mcpServers"]["taskit"]

        # OpenCode's permission includes taskit + mobile + chrome-devtools tools (all defaults)
        from odin.mcps.mobile_mcp.config import mobile_tool_names
        from odin.mcps.chrome_devtools_mcp.config import chrome_devtools_tool_names
        opencode_data = json.loads((tmp_path / "opencode.json").read_text())
        expected_tools = set(tool_names()) | set(mobile_tool_names()) | set(chrome_devtools_tool_names())
        assert set(opencode_data["permission"].keys()) == expected_tools


# ── Cross-harness consistency ──────────────────────────────────


class TestAllHarnessConfigsConsistency:
    """Verify all 6 harness configs include taskit tools and correct env."""

    def _make_orchestrator(self, tmp_path):
        from odin.orchestrator import Orchestrator

        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=TaskItConfig(base_url="http://localhost:8000"),
            log_dir=str(tmp_path / "logs"),
        )

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "test-bearer-token"
        mock_client = MagicMock()
        mock_client.auth = mock_auth
        mock_backend = MagicMock()
        mock_backend._client = mock_client
        orch._backend = mock_backend

        return orch

    def test_all_harness_configs_include_taskit_tools(self, tmp_path):
        """All 6 harness configs reference the taskit MCP server."""
        orch = self._make_orchestrator(tmp_path)
        harness_names = ["claude", "gemini", "qwen", "codex", "minimax", "glm"]
        for name in harness_names:
            orch._generate_mcp_config(
                "42", name, tmp_path / "logs", working_dir=str(tmp_path),
            )

        # Claude: .mcp.json → mcpServers.taskit
        claude_data = json.loads(Path(orch._generate_mcp_config("42", "claude", tmp_path / "logs")).read_text())
        assert "taskit" in claude_data["mcpServers"]

        # Gemini: .gemini/settings.json → mcpServers.taskit
        gemini_data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert "taskit" in gemini_data["mcpServers"]

        # Qwen: .qwen/settings.json → mcpServers.taskit
        qwen_data = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert "taskit" in qwen_data["mcpServers"]

        # Codex: .codex/config.toml → [mcp_servers.taskit]
        codex_content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "taskit" in codex_content

        # MiniMax + GLM: opencode.json → mcp.taskit
        opencode_data = json.loads((tmp_path / "opencode.json").read_text())
        assert "taskit" in opencode_data["mcp"]

    def test_mcp_env_includes_auth_token(self, tmp_path):
        """Generated env has TASKIT_AUTH_TOKEN for all harnesses."""
        orch = self._make_orchestrator(tmp_path)

        # Claude (JSON config path)
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTH_TOKEN"] == "test-bearer-token"

        # Gemini (project-local settings.json)
        orch._generate_mcp_config("42", "gemini", tmp_path / "logs", working_dir=str(tmp_path))
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTH_TOKEN"] == "test-bearer-token"

        # Qwen
        orch._generate_mcp_config("42", "qwen", tmp_path / "logs", working_dir=str(tmp_path))
        data = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTH_TOKEN"] == "test-bearer-token"

    def test_mcp_env_includes_author_identity(self, tmp_path):
        """Generated env has correct TASKIT_AUTHOR_EMAIL per harness."""
        orch = self._make_orchestrator(tmp_path)

        expected = {
            "claude": "claude@odin.agent",
            "gemini": "gemini@odin.agent",
            "qwen": "qwen@odin.agent",
            "codex": "codex@odin.agent",
            "minimax": "minimax@odin.agent",
            "glm": "glm@odin.agent",
        }

        for harness_name, expected_email in expected.items():
            orch._generate_mcp_config(
                "42", harness_name, tmp_path / "logs", working_dir=str(tmp_path),
            )

        # Verify Claude
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTHOR_EMAIL"] == "claude@odin.agent"

        # Verify Gemini
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTHOR_EMAIL"] == "gemini@odin.agent"

        # Verify Qwen
        data = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert data["mcpServers"]["taskit"]["env"]["TASKIT_AUTHOR_EMAIL"] == "qwen@odin.agent"

        # Verify MiniMax/GLM (share opencode.json — last writer wins)
        # The last one generated was glm
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert data["mcp"]["taskit"]["environment"]["TASKIT_AUTHOR_EMAIL"] == "glm@odin.agent"

    def test_claude_mcp_config_includes_question_tool(self, tmp_path):
        """Claude config allows mcp__taskit__taskit_add_comment (question tool)."""
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        # The MCP server exposes tools; Claude discovers them dynamically.
        # Verify the taskit MCP server is configured (tools are server-side).
        assert data["mcpServers"]["taskit"]["command"] == "taskit-mcp"
        # Claude's --allowedTools flag is set by the harness, not in the config.
        # Verify tool names are importable from the config module.
        assert "mcp__taskit__taskit_add_comment" in claude_tool_names()
        assert "mcp__taskit__taskit_add_attachment" in claude_tool_names()


# ── Multi-server merging (taskit + mobile) ─────────────────────


class TestMultiServerMerging:
    """Verify merged MCP configs contain both taskit and mobile servers."""

    def _make_orchestrator(self, tmp_path, mcps=None):
        from odin.orchestrator import Orchestrator

        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=TaskItConfig(base_url="http://localhost:8000"),
            mcps=mcps or ["taskit", "mobile"],
            log_dir=str(tmp_path / "logs"),
        )

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "test-bearer-token"
        mock_client = MagicMock()
        mock_client.auth = mock_auth
        mock_backend = MagicMock()
        mock_backend._client = mock_client
        orch._backend = mock_backend

        return orch

    def test_merged_config_claude_has_both_servers(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "taskit" in data["mcpServers"]
        assert "mobile" in data["mcpServers"]
        assert data["mcpServers"]["mobile"]["command"] == "npx"

    def test_merged_config_gemini_has_both_servers(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "gemini", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert "taskit" in data["mcpServers"]
        assert "mobile" in data["mcpServers"]
        assert data["mcpServers"]["mobile"]["trust"] is True

    def test_merged_config_codex_has_both_servers(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "codex", tmp_path / "logs", working_dir=str(tmp_path),
        )
        content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.taskit]" in content
        assert "[mcp_servers.mobile]" in content
        assert 'command = "npx"' in content

    def test_merged_config_opencode_has_both_servers(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert "taskit" in data["mcp"]
        assert "mobile" in data["mcp"]
        assert data["mcp"]["mobile"]["type"] == "local"

    def test_opencode_permission_includes_mobile_tools(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        # Should have both taskit and mobile tool permissions
        assert "mobile_save_screenshot" in data["permission"]
        assert data["permission"]["mobile_save_screenshot"] == "allow"
        # Taskit tools should still be present
        assert any("taskit" in k for k in data["permission"])

    def test_default_mcps_no_mobile(self, tmp_path):
        """When mcps defaults to ['taskit'], no mobile server in config."""
        orch = self._make_orchestrator(tmp_path, mcps=["taskit"])
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "taskit" in data["mcpServers"]
        assert "mobile" not in data["mcpServers"]

    def test_mobile_only_no_taskit(self, tmp_path):
        """When only mobile is configured (no taskit), only mobile appears."""
        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=None,
            mcps=["mobile"],
            log_dir=str(tmp_path / "logs"),
        )
        from odin.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "mobile" in data["mcpServers"]
        assert "taskit" not in data["mcpServers"]


class TestChromeDevtoolsServerMerging:
    """Verify merged MCP configs contain chrome-devtools server entries."""

    def _make_orchestrator(self, tmp_path, mcps=None):
        from odin.orchestrator import Orchestrator

        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=TaskItConfig(base_url="http://localhost:8000"),
            mcps=mcps or ["taskit", "chrome-devtools"],
            log_dir=str(tmp_path / "logs"),
        )

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "test-bearer-token"
        mock_client = MagicMock()
        mock_client.auth = mock_auth
        mock_backend = MagicMock()
        mock_backend._client = mock_client
        orch._backend = mock_backend

        return orch

    def test_claude_has_chrome_devtools_server(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "chrome-devtools" in data["mcpServers"]
        assert data["mcpServers"]["chrome-devtools"]["command"] == "npx"
        assert data["mcpServers"]["chrome-devtools"]["args"] == ["-y", "chrome-devtools-mcp@latest"]

    def test_gemini_has_chrome_devtools_with_trust(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "gemini", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
        assert "chrome-devtools" in data["mcpServers"]
        assert data["mcpServers"]["chrome-devtools"]["trust"] is True

    def test_codex_has_chrome_devtools_section(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "codex", tmp_path / "logs", working_dir=str(tmp_path),
        )
        content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.chrome-devtools]" in content
        assert 'command = "npx"' in content

    def test_opencode_has_chrome_devtools_server(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert "chrome-devtools" in data["mcp"]
        assert data["mcp"]["chrome-devtools"]["type"] == "local"

    def test_opencode_permission_includes_chrome_devtools_tools(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        orch._generate_mcp_config(
            "42", "minimax", tmp_path / "logs", working_dir=str(tmp_path),
        )
        data = json.loads((tmp_path / "opencode.json").read_text())
        assert "take_screenshot" in data["permission"]
        assert data["permission"]["take_screenshot"] == "allow"

    def test_chrome_devtools_only_no_taskit(self, tmp_path):
        """When only chrome-devtools is configured, only it appears."""
        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=None,
            mcps=["chrome-devtools"],
            log_dir=str(tmp_path / "logs"),
        )
        from odin.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "chrome-devtools" in data["mcpServers"]
        assert "taskit" not in data["mcpServers"]

    def test_all_three_servers_merged(self, tmp_path):
        """taskit + mobile + chrome-devtools all present."""
        orch = self._make_orchestrator(tmp_path, mcps=["taskit", "mobile", "chrome-devtools"])
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "taskit" in data["mcpServers"]
        assert "mobile" in data["mcpServers"]
        assert "chrome-devtools" in data["mcpServers"]

    def test_no_chrome_devtools_when_not_in_mcps(self, tmp_path):
        orch = self._make_orchestrator(tmp_path, mcps=["taskit"])
        config_path = orch._generate_mcp_config("42", "claude", tmp_path / "logs")
        data = json.loads(Path(config_path).read_text())
        assert "chrome-devtools" not in data["mcpServers"]


class TestMobileToolApproval:
    """Verify mobile tools appear in allowed tools list."""

    def test_claude_allowed_tools_include_mobile(self):
        from odin.mcps.mobile_mcp.config import claude_mobile_tool_names
        mobile_tools = claude_mobile_tool_names()
        assert "mcp__mobile__mobile_save_screenshot" in mobile_tools
        assert "mcp__mobile__mobile_list_available_devices" in mobile_tools

    def test_claude_settings_includes_mobile_tools(self):
        from odin.mcps.taskit_mcp.config import format_claude_settings
        settings = json.loads(format_claude_settings(mcps=["taskit", "mobile"]))
        allow = settings["permissions"]["allow"]
        assert "mcp__mobile__mobile_save_screenshot" in allow
        assert "mobile" in settings["enabledMcpjsonServers"]

    def test_claude_settings_no_mobile_by_default(self):
        from odin.mcps.taskit_mcp.config import format_claude_settings
        settings = json.loads(format_claude_settings())
        allow = settings["permissions"]["allow"]
        assert not any("mobile" in t for t in allow if "mcp__" in t and "taskit" not in t)
        assert "mobile" not in settings["enabledMcpjsonServers"]


class TestChromeDevtoolsToolApproval:
    """Verify chrome-devtools tools appear in allowed tools and settings."""

    def test_claude_allowed_tools_include_chrome_devtools(self):
        from odin.mcps.chrome_devtools_mcp.config import claude_chrome_devtools_tool_names
        tools = claude_chrome_devtools_tool_names()
        assert "mcp__chrome-devtools__take_screenshot" in tools
        assert "mcp__chrome-devtools__navigate_page" in tools

    def test_claude_settings_includes_chrome_devtools_tools(self):
        from odin.mcps.taskit_mcp.config import format_claude_settings
        settings = json.loads(format_claude_settings(mcps=["taskit", "chrome-devtools"]))
        allow = settings["permissions"]["allow"]
        assert "mcp__chrome-devtools__take_screenshot" in allow
        assert "chrome-devtools" in settings["enabledMcpjsonServers"]

    def test_claude_settings_no_chrome_devtools_by_default(self):
        from odin.mcps.taskit_mcp.config import format_claude_settings
        settings = json.loads(format_claude_settings())
        allow = settings["permissions"]["allow"]
        assert not any("chrome-devtools" in t for t in allow)
        assert "chrome-devtools" not in settings["enabledMcpjsonServers"]


class TestWrapPromptMobile:
    """Verify _wrap_prompt includes/excludes mobile section."""

    def test_wrap_prompt_includes_mobile_section(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42", mcps=["taskit", "mobile"],
        )
        assert "Mobile MCP Tools" in wrapped
        assert "mobile_list_available_devices" in wrapped
        assert "mobile_save_screenshot" in wrapped

    def test_wrap_prompt_no_mobile_when_not_configured(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42", mcps=["taskit"],
        )
        assert "Mobile MCP Tools" not in wrapped

    def test_wrap_prompt_no_mobile_when_mcps_none(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42",
        )
        assert "Mobile MCP Tools" not in wrapped


class TestWrapPromptChromeDevtools:
    """Verify _wrap_prompt includes/excludes chrome-devtools section."""

    def test_wrap_prompt_includes_chrome_devtools_section(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42", mcps=["taskit", "chrome-devtools"],
        )
        assert "Chrome DevTools MCP" in wrapped
        assert "navigate_page" in wrapped
        assert "take_screenshot" in wrapped

    def test_wrap_prompt_no_chrome_devtools_when_not_configured(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42", mcps=["taskit"],
        )
        assert "Chrome DevTools MCP" not in wrapped

    def test_wrap_prompt_no_chrome_devtools_when_mcps_none(self):
        from odin.orchestrator import Orchestrator
        wrapped = Orchestrator._wrap_prompt(
            "do something", mcp_task_id="42",
        )
        assert "Chrome DevTools MCP" not in wrapped


class TestCodexMobileFlags:
    """Verify Codex harness injects mobile -c flags."""

    def test_mobile_flags_when_enabled(self):
        config = AgentConfig(cli_command="codex")
        harness = CodexHarness(config)
        context = {"mobile_mcp_enabled": True}
        cmd = harness.build_execute_command("test prompt", context)
        assert 'mcp_servers.mobile.command="npx"' in cmd

    def test_no_mobile_flags_when_not_enabled(self):
        config = AgentConfig(cli_command="codex")
        harness = CodexHarness(config)
        context = {}
        cmd = harness.build_execute_command("test prompt", context)
        assert not any("mobile" in str(c) for c in cmd)


class TestCodexChromeDevtoolsFlags:
    """Verify Codex harness injects chrome-devtools -c flags."""

    def test_chrome_devtools_flags_when_enabled(self):
        config = AgentConfig(cli_command="codex")
        harness = CodexHarness(config)
        context = {"chrome_devtools_mcp_enabled": True}
        cmd = harness.build_execute_command("test prompt", context)
        assert 'mcp_servers.chrome-devtools.command="npx"' in cmd

    def test_no_chrome_devtools_flags_when_not_enabled(self):
        config = AgentConfig(cli_command="codex")
        harness = CodexHarness(config)
        context = {}
        cmd = harness.build_execute_command("test prompt", context)
        assert not any("chrome-devtools" in str(c) for c in cmd)
