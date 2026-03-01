"""Mobile MCP server config: tool names and per-CLI server fragments.

mobile-mcp is an external npm package (``@mobilenext/mobile-mcp``) that
controls iOS Simulators and Android emulators.  Unlike the TaskIt MCP server,
it cannot be introspected at config-generation time (it's not a Python
package), so tool names are hardcoded here.

Usage::

    from odin.mcps.mobile_mcp.config import (
        mobile_tool_names, claude_mobile_tool_names, server_fragment,
    )
"""

from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# Tool names (from @mobilenext/mobile-mcp v0.1.x)
# ---------------------------------------------------------------------------

MOBILE_TOOL_NAMES: List[str] = sorted([
    "mobile_click_on_screen_at_coordinates",
    "mobile_drag_on_screen",
    "mobile_find_and_click_element",
    "mobile_get_element_tree",
    "mobile_get_screen_size",
    "mobile_install_app",
    "mobile_launch_app",
    "mobile_list_available_devices",
    "mobile_long_press_on_screen_at_coordinates",
    "mobile_navigate_back",
    "mobile_open_url",
    "mobile_save_screenshot",
    "mobile_scroll_down",
    "mobile_scroll_up",
    "mobile_set_screen_brightness",
    "mobile_swipe_on_screen",
    "mobile_terminate_app",
    "mobile_type_keys",
    "mobile_wait",
])


def mobile_tool_names() -> List[str]:
    """Return the sorted list of mobile-mcp tool names."""
    return list(MOBILE_TOOL_NAMES)


def claude_mobile_tool_names() -> List[str]:
    """Return tool names in Claude Code's ``mcp__mobile__<name>`` format."""
    return [f"mcp__mobile__{name}" for name in MOBILE_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Per-harness server fragments
# ---------------------------------------------------------------------------

_NPX_CMD = "npx"
_NPX_ARGS = ["-y", "@mobilenext/mobile-mcp@latest"]


def _server_entry_claude() -> Dict:
    """Claude Code / generic mcpServers entry."""
    return {"mobile": {"command": _NPX_CMD, "args": _NPX_ARGS}}


def _server_entry_gemini() -> Dict:
    """Gemini — same as Claude but with ``trust: true``."""
    return {"mobile": {"command": _NPX_CMD, "args": _NPX_ARGS, "trust": True}}


def _server_entry_qwen() -> Dict:
    """Qwen — same structure as Gemini."""
    return {"mobile": {"command": _NPX_CMD, "args": _NPX_ARGS, "trust": True}}


def _server_entry_codex() -> List[str]:
    """Codex — returns list of ``-c`` flag pairs for CLI injection."""
    return [
        "-c", f'mcp_servers.mobile.command="{_NPX_CMD}"',
        "-c", 'mcp_servers.mobile.args=["-y", "@mobilenext/mobile-mcp@latest"]',
    ]


def _server_entry_kilocode() -> Dict:
    """Kilo Code — includes ``alwaysAllow`` for auto-approval."""
    return {"mobile": {
        "command": _NPX_CMD,
        "args": _NPX_ARGS,
        "alwaysAllow": list(MOBILE_TOOL_NAMES),
    }}


def _server_entry_opencode() -> Dict:
    """OpenCode — different structure: ``type``, ``command`` as array, ``permission``."""
    return {"mobile": {
        "type": "local",
        "command": [_NPX_CMD, *_NPX_ARGS],
    }}


def _opencode_permissions() -> Dict[str, str]:
    """OpenCode permission entries for mobile tools."""
    return {t: "allow" for t in MOBILE_TOOL_NAMES}


# Dispatcher mapping agent name -> fragment function
_FRAGMENT_MAP = {
    "claude": _server_entry_claude,
    "gemini": _server_entry_gemini,
    "qwen": _server_entry_qwen,
    "codex": _server_entry_codex,
    "kilocode": _server_entry_kilocode,
    "minimax": _server_entry_opencode,
    "glm": _server_entry_opencode,
}


def server_fragment(agent_name: str) -> Dict | List[str]:
    """Return the mobile MCP server fragment for a given agent.

    For most agents, returns a dict to merge into ``mcpServers``.
    For Codex, returns a list of ``-c`` flag pairs.
    For OpenCode agents (minimax/glm), returns the ``mcp`` dict entry.
    """
    fn = _FRAGMENT_MAP.get(agent_name, _server_entry_claude)
    return fn()
