"""Unit tests for mobile MCP config: tool names, prefixed names, server fragments.

Tests verify:
- MOBILE_TOOL_NAMES has exactly 19 entries (matching mobile-mcp v0.1.x)
- Tool names are sorted (deterministic config generation)
- Claude-prefixed names use ``mcp__mobile__<name>`` format
- Server fragments are structurally correct per harness
- No env vars needed (mobile-mcp doesn't require auth)
"""

import pytest

from odin.mcps.mobile_mcp.config import (
    MOBILE_TOOL_NAMES,
    mobile_tool_names,
    claude_mobile_tool_names,
    server_fragment,
    _opencode_permissions,
)


class TestMobileToolNames:
    def test_has_19_entries(self):
        assert len(MOBILE_TOOL_NAMES) == 19

    def test_sorted(self):
        assert MOBILE_TOOL_NAMES == sorted(MOBILE_TOOL_NAMES)

    def test_mobile_tool_names_returns_copy(self):
        names = mobile_tool_names()
        assert names == MOBILE_TOOL_NAMES
        # Must be a copy, not the same object
        names.append("extra")
        assert len(MOBILE_TOOL_NAMES) == 19


class TestClaudeMobileToolNames:
    def test_all_prefixed(self):
        names = claude_mobile_tool_names()
        assert len(names) == 19
        for name in names:
            assert name.startswith("mcp__mobile__")

    def test_contains_known_tool(self):
        names = claude_mobile_tool_names()
        assert "mcp__mobile__mobile_save_screenshot" in names
        assert "mcp__mobile__mobile_list_available_devices" in names


class TestServerFragmentClaude:
    def test_no_env_needed(self):
        frag = server_fragment("claude")
        assert "mobile" in frag
        assert "env" not in frag["mobile"]

    def test_command_is_npx(self):
        frag = server_fragment("claude")
        assert frag["mobile"]["command"] == "npx"
        assert frag["mobile"]["args"] == ["-y", "@mobilenext/mobile-mcp@latest"]


class TestServerFragmentGemini:
    def test_has_trust(self):
        frag = server_fragment("gemini")
        assert frag["mobile"]["trust"] is True

    def test_command_is_npx(self):
        frag = server_fragment("gemini")
        assert frag["mobile"]["command"] == "npx"


class TestServerFragmentQwen:
    def test_has_trust(self):
        frag = server_fragment("qwen")
        assert frag["mobile"]["trust"] is True


class TestServerFragmentCodex:
    def test_returns_flag_list(self):
        frag = server_fragment("codex")
        assert isinstance(frag, list)
        assert "-c" in frag

    def test_contains_mobile_command(self):
        frag = server_fragment("codex")
        assert 'mcp_servers.mobile.command="npx"' in frag


class TestServerFragmentOpencode:
    def test_structure(self):
        frag = server_fragment("minimax")
        assert frag["mobile"]["type"] == "local"
        assert isinstance(frag["mobile"]["command"], list)
        assert frag["mobile"]["command"][0] == "npx"

    def test_glm_same_as_minimax(self):
        assert server_fragment("glm") == server_fragment("minimax")


class TestServerFragmentKilocode:
    def test_has_always_allow(self):
        from odin.mcps.mobile_mcp.config import _server_entry_kilocode
        frag = _server_entry_kilocode()
        assert "alwaysAllow" in frag["mobile"]
        assert len(frag["mobile"]["alwaysAllow"]) == 19


class TestOpenCodePermissions:
    def test_all_tools_allowed(self):
        perms = _opencode_permissions()
        assert len(perms) == 19
        assert all(v == "allow" for v in perms.values())


class TestUnknownAgent:
    def test_falls_back_to_claude(self):
        frag = server_fragment("unknown_agent")
        assert "mobile" in frag
        assert frag["mobile"]["command"] == "npx"
        assert "trust" not in frag["mobile"]
