"""Unit tests for chrome-devtools MCP config: tool names, prefixed names, server fragments.

Tests verify:
- CHROME_DEVTOOLS_TOOL_NAMES has exactly 28 entries (matching chrome-devtools-mcp)
- Tool names are sorted (deterministic config generation)
- Claude-prefixed names use ``mcp__chrome-devtools__<name>`` format
- Server fragments are structurally correct per harness
- No env vars needed (chrome-devtools-mcp doesn't require auth)
- headless=True appends ``--headless`` to args for every agent type
"""

import pytest

from odin.mcps.chrome_devtools_mcp.config import (
    CHROME_DEVTOOLS_TOOL_NAMES,
    _NPX_ARGS,
    chrome_devtools_tool_names,
    claude_chrome_devtools_tool_names,
    server_fragment,
    _opencode_permissions,
)


class TestChromeDevtoolsToolNames:
    def test_has_28_entries(self):
        assert len(CHROME_DEVTOOLS_TOOL_NAMES) == 28

    def test_sorted(self):
        assert CHROME_DEVTOOLS_TOOL_NAMES == sorted(CHROME_DEVTOOLS_TOOL_NAMES)

    def test_returns_copy(self):
        names = chrome_devtools_tool_names()
        assert names == CHROME_DEVTOOLS_TOOL_NAMES
        # Must be a copy, not the same object
        names.append("extra")
        assert len(CHROME_DEVTOOLS_TOOL_NAMES) == 28


class TestClaudeChromeDevtoolsToolNames:
    def test_all_prefixed(self):
        names = claude_chrome_devtools_tool_names()
        assert len(names) == 28
        for name in names:
            assert name.startswith("mcp__chrome-devtools__")

    def test_contains_known_tools(self):
        names = claude_chrome_devtools_tool_names()
        assert "mcp__chrome-devtools__take_screenshot" in names
        assert "mcp__chrome-devtools__navigate_page" in names
        assert "mcp__chrome-devtools__evaluate_script" in names


class TestServerFragmentClaude:
    def test_no_env_needed(self):
        frag = server_fragment("claude")
        assert "chrome-devtools" in frag
        assert "env" not in frag["chrome-devtools"]

    def test_command_is_npx(self):
        frag = server_fragment("claude")
        assert frag["chrome-devtools"]["command"] == "npx"
        assert frag["chrome-devtools"]["args"] == ["-y", "chrome-devtools-mcp@latest"]


class TestServerFragmentGemini:
    def test_has_trust(self):
        frag = server_fragment("gemini")
        assert frag["chrome-devtools"]["trust"] is True

    def test_command_is_npx(self):
        frag = server_fragment("gemini")
        assert frag["chrome-devtools"]["command"] == "npx"


class TestServerFragmentQwen:
    def test_has_trust(self):
        frag = server_fragment("qwen")
        assert frag["chrome-devtools"]["trust"] is True


class TestServerFragmentCodex:
    def test_returns_flag_list(self):
        frag = server_fragment("codex")
        assert isinstance(frag, list)
        assert "-c" in frag

    def test_contains_chrome_devtools_command(self):
        frag = server_fragment("codex")
        assert 'mcp_servers.chrome-devtools.command="npx"' in frag


class TestServerFragmentOpencode:
    def test_structure(self):
        frag = server_fragment("minimax")
        assert frag["chrome-devtools"]["type"] == "local"
        assert isinstance(frag["chrome-devtools"]["command"], list)
        assert frag["chrome-devtools"]["command"][0] == "npx"

    def test_glm_same_as_minimax(self):
        assert server_fragment("glm") == server_fragment("minimax")


class TestServerFragmentKilocode:
    def test_has_always_allow(self):
        frag = server_fragment("kilocode")
        assert "alwaysAllow" in frag["chrome-devtools"]
        assert len(frag["chrome-devtools"]["alwaysAllow"]) == 28


class TestOpenCodePermissions:
    def test_all_tools_allowed(self):
        perms = _opencode_permissions()
        assert len(perms) == 28
        assert all(v == "allow" for v in perms.values())


class TestUnknownAgent:
    def test_falls_back_to_claude(self):
        frag = server_fragment("unknown_agent")
        assert "chrome-devtools" in frag
        assert frag["chrome-devtools"]["command"] == "npx"
        assert "trust" not in frag["chrome-devtools"]


class TestHeadlessFlag:
    """Verify headless=True appends --headless to args for every agent type."""

    def test_claude_headless(self):
        frag = server_fragment("claude", headless=True)
        args = frag["chrome-devtools"]["args"]
        assert args[-1] == "--headless"
        assert args[:-1] == list(_NPX_ARGS)

    def test_gemini_headless(self):
        frag = server_fragment("gemini", headless=True)
        assert "--headless" in frag["chrome-devtools"]["args"]
        assert frag["chrome-devtools"]["trust"] is True

    def test_qwen_headless(self):
        frag = server_fragment("qwen", headless=True)
        assert "--headless" in frag["chrome-devtools"]["args"]

    def test_codex_headless(self):
        frag = server_fragment("codex", headless=True)
        # Codex returns -c flag pairs; args should include --headless in the TOML array
        args_line = [v for v in frag if "args=" in v or "args =" in v]
        assert len(args_line) == 1
        assert '"--headless"' in args_line[0]

    def test_kilocode_headless(self):
        frag = server_fragment("kilocode", headless=True)
        assert "--headless" in frag["chrome-devtools"]["args"]

    def test_opencode_headless(self):
        frag = server_fragment("minimax", headless=True)
        cmd = frag["chrome-devtools"]["command"]
        assert "--headless" in cmd

    def test_headless_false_no_flag(self):
        """Default headless=False should not include --headless."""
        frag = server_fragment("claude", headless=False)
        assert "--headless" not in frag["chrome-devtools"]["args"]

    def test_default_no_headless(self):
        """Calling without headless kwarg should not include --headless."""
        frag = server_fragment("claude")
        assert "--headless" not in frag["chrome-devtools"]["args"]

    def test_does_not_mutate_module_constant(self):
        """headless=True must not mutate the shared _NPX_ARGS list."""
        original = list(_NPX_ARGS)
        server_fragment("claude", headless=True)
        assert _NPX_ARGS == original
