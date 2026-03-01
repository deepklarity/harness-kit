"""Contract tests: MCP config output matches what server.py actually registers.

These tests are the guardrails that would have caught the ``alwaysAllow`` bug
where Claude tasks silently had no MCP messages.  They verify that:

1. Tool names introspected from the FastMCP instance match ``server.py``
2. Claude Code uses ``--allowedTools`` CLI flag (not config-file-based approval)
3. Other CLIs' approval keys (``trust`` / ``permission``) include ALL server tools
4. Every formatter has a config path and vice versa
5. All formatters produce valid, parseable output
"""

import json

import pytest

from odin.mcps.taskit_mcp.config import (
    AGENTS_WITH_TOOL_APPROVAL,
    MCP_CONFIG_MAP,
    MCP_FORMATTERS,
    claude_tool_names,
    format_claude,
    format_gemini,
    format_kilocode,
    format_opencode,
    format_qwen,
    get_tool_names,
    server_name,
    tool_names,
)


# ── Tool name introspection ──────────────────────────────────


class TestToolNames:
    """Verify tool_names() reflects the actual server.py registrations."""

    def test_tool_names_match_server(self):
        """get_tool_names() returns the tools registered on the FastMCP instance."""
        names = get_tool_names()
        assert len(names) > 0, "No tools found — server.py may have changed"
        # Verify known tools are present
        assert "taskit_add_comment" in names
        assert "taskit_add_attachment" in names

    def test_tool_names_cached_matches_uncached(self):
        """Cached tool_names() returns the same result as get_tool_names()."""
        assert tool_names() == get_tool_names()

    def test_tool_names_are_sorted(self):
        """Tool names are returned in sorted order for deterministic configs."""
        names = tool_names()
        assert names == sorted(names)


# ── Claude Code tool approval (CLI flag, not config key) ─────


class TestClaudeToolApproval:
    """Claude Code uses --allowedTools CLI flag, not alwaysAllow in config."""

    def test_claude_config_has_no_alwaysAllow(self):
        """Claude's .mcp.json must NOT contain alwaysAllow.

        Claude Code does not support alwaysAllow in .mcp.json.
        Tool permissions are granted via --allowedTools CLI flag.
        """
        env = {"TASKIT_URL": "http://test:8000"}
        data = json.loads(format_claude(env))
        taskit = data["mcpServers"]["taskit"]
        assert "alwaysAllow" not in taskit, (
            "alwaysAllow found in Claude config — Claude Code ignores this key. "
            "Use --allowedTools CLI flag instead."
        )

    def test_claude_config_has_command_and_env(self):
        """Claude's .mcp.json must define server command and env vars."""
        env = {"TASKIT_URL": "http://test:8000"}
        data = json.loads(format_claude(env))
        taskit = data["mcpServers"]["taskit"]
        assert taskit["command"] == "taskit-mcp"
        assert taskit["env"]["TASKIT_URL"] == "http://test:8000"

    def test_claude_tool_names_are_prefixed(self):
        """claude_tool_names() returns mcp__<server>__<tool> format.

        The --allowedTools flag requires this prefix format.
        """
        prefix = f"mcp__{server_name()}__"
        for name in claude_tool_names():
            assert name.startswith(prefix), (
                f"Tool '{name}' missing prefix '{prefix}'"
            )

    def test_claude_tool_names_cover_all_server_tools(self):
        """Every server tool must appear in claude_tool_names()."""
        prefixed = set(claude_tool_names())
        prefix = f"mcp__{server_name()}__"
        expected = {f"{prefix}{t}" for t in tool_names()}
        assert prefixed == expected

    def test_claude_allowed_tools_injected_in_cli(self):
        """Claude harness must add --allowedTools when mcp_allowed_tools is in context."""
        from odin.harnesses.claude import ClaudeHarness
        from odin.models import AgentConfig, CostTier

        config = AgentConfig(cli_command="claude", capabilities=["coding"], cost_tier=CostTier.HIGH)
        harness = ClaudeHarness(config)
        context = {
            "mcp_config": "/tmp/mcp.json",
            "mcp_allowed_tools": claude_tool_names(),
        }
        cmd = harness.build_execute_command("test prompt", context)
        assert "--allowedTools" in cmd
        # All tools should be in the comma-separated value
        allowed_idx = cmd.index("--allowedTools")
        allowed_value = cmd[allowed_idx + 1]
        for tool in claude_tool_names():
            assert tool in allowed_value, (
                f"Tool '{tool}' missing from --allowedTools value"
            )


# ── Other CLIs' approval contracts ───────────────────────────


class TestOtherCliApproval:
    """Non-Claude CLIs use config-file-based approval mechanisms."""

    def test_kilocode_alwaysAllow_contains_all_server_tools(self):
        """Kilo Code's alwaysAllow must list every tool from server.py."""
        env = {"TASKIT_URL": "http://test:8000"}
        data = json.loads(format_kilocode(env))
        always_allow = data["mcpServers"]["taskit"]["alwaysAllow"]

        for tool in tool_names():
            assert tool in always_allow, (
                f"Tool '{tool}' registered in server.py but missing from "
                f"Kilo Code alwaysAllow"
            )

    def test_opencode_permission_contains_all_server_tools(self):
        """OpenCode's permission block must include every tool from server.py."""
        env = {"TASKIT_URL": "http://test:8000"}
        data = json.loads(format_opencode(env))
        permission = data["permission"]

        for tool in tool_names():
            assert tool in permission, (
                f"Tool '{tool}' registered in server.py but missing from "
                f"OpenCode permission block"
            )
            assert permission[tool] == "allow"


# ── Approval mechanism presence ──────────────────────────────


class TestApprovalMechanismPresent:
    """Each agent that needs tool approval has the correct mechanism."""

    @pytest.mark.parametrize("agent_name,approval_key", [
        ("claude", "--allowedTools"),
        ("gemini", "trust"),
        ("qwen", "trust"),
        ("minimax", "permission"),
    ])
    def test_approval_mechanism_present(self, agent_name, approval_key):
        """Config for {agent_name} must contain the '{approval_key}' mechanism."""
        formatter = MCP_FORMATTERS[agent_name]
        env = {"TASKIT_URL": "http://test:8000"}
        data = json.loads(formatter(env))

        if approval_key == "permission":
            assert approval_key in data, (
                f"'{approval_key}' key missing from {agent_name} config"
            )
        elif approval_key == "trust":
            taskit = data["mcpServers"]["taskit"]
            assert taskit.get("trust") is True, (
                f"trust not set to True in {agent_name} config"
            )
        elif approval_key == "--allowedTools":
            # Claude uses CLI flag, not config key — verify config is clean
            taskit = data["mcpServers"]["taskit"]
            assert "alwaysAllow" not in taskit, (
                f"alwaysAllow should not be in Claude config — "
                f"use --allowedTools CLI flag instead"
            )


# ── Map consistency ──────────────────────────────────────────


class TestMapConsistency:
    """MCP_CONFIG_MAP and MCP_FORMATTERS must stay in sync."""

    def test_every_formatter_has_config_path(self):
        """Every agent in MCP_FORMATTERS must have a path in MCP_CONFIG_MAP."""
        for agent in MCP_FORMATTERS:
            assert agent in MCP_CONFIG_MAP, (
                f"Agent '{agent}' has a formatter but no config path"
            )

    def test_every_config_path_has_formatter(self):
        """Every agent in MCP_CONFIG_MAP must have a formatter in MCP_FORMATTERS."""
        for agent in MCP_CONFIG_MAP:
            assert agent in MCP_FORMATTERS, (
                f"Agent '{agent}' has a config path but no formatter"
            )

    def test_approval_agents_have_formatters(self):
        """Every agent in AGENTS_WITH_TOOL_APPROVAL has a formatter."""
        for agent in AGENTS_WITH_TOOL_APPROVAL:
            assert agent in MCP_FORMATTERS, (
                f"Agent '{agent}' in AGENTS_WITH_TOOL_APPROVAL but no formatter"
            )


# ── Formatter output validity ────────────────────────────────


class TestFormatterOutputValidity:
    """All formatters must produce non-empty, parseable output."""

    @pytest.mark.parametrize("agent_name", list(MCP_FORMATTERS.keys()))
    def test_all_formatters_produce_valid_output(self, agent_name):
        """Formatter for {agent_name} must return non-empty, parseable content."""
        formatter = MCP_FORMATTERS[agent_name]
        env = {"TASKIT_URL": "http://test:8000", "TASKIT_TASK_ID": "42"}
        content = formatter(env)

        assert content, f"Formatter for {agent_name} returned empty string"
        assert len(content) > 10, f"Formatter for {agent_name} returned suspiciously short content"

        # JSON formatters should parse; TOML (codex) should have expected structure
        if agent_name == "codex":
            assert "[mcp_servers.taskit]" in content
            assert 'command = "taskit-mcp"' in content
        else:
            data = json.loads(content)
            assert isinstance(data, dict)

    def test_formatters_include_env_vars(self):
        """All formatters propagate environment variables into the output."""
        env = {"TASKIT_URL": "http://test:8000", "TASKIT_TASK_ID": "99"}
        for agent_name, formatter in MCP_FORMATTERS.items():
            content = formatter(env)
            assert "http://test:8000" in content, (
                f"{agent_name} formatter did not include TASKIT_URL"
            )
