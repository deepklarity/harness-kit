"""Chrome DevTools MCP server config: tool names and per-CLI server fragments.

chrome-devtools-mcp is an external npm package that provides browser
automation, debugging, performance tracing, and network inspection tools.
Unlike the TaskIt MCP server, it cannot be introspected at config-generation
time (it's not a Python package), so tool names are hardcoded here.

Usage::

    from odin.mcps.chrome_devtools_mcp.config import (
        chrome_devtools_tool_names, claude_chrome_devtools_tool_names,
        server_fragment,
    )
"""

from __future__ import annotations

from typing import Dict, List, Union

# ---------------------------------------------------------------------------
# Tool names (from chrome-devtools-mcp)
# ---------------------------------------------------------------------------

CHROME_DEVTOOLS_TOOL_NAMES: List[str] = sorted([
    "click",
    "close_page",
    "drag",
    "emulate",
    "evaluate_script",
    "fill",
    "fill_form",
    "get_console_message",
    "get_network_request",
    "handle_dialog",
    "hover",
    "list_console_messages",
    "list_network_requests",
    "list_pages",
    "navigate_page",
    "new_page",
    "performance_analyze_insight",
    "performance_start_trace",
    "performance_stop_trace",
    "press_key",
    "resize_page",
    "select_page",
    "take_memory_snapshot",
    "take_screenshot",
    "take_snapshot",
    "type_text",
    "upload_file",
    "wait_for",
])


def chrome_devtools_tool_names() -> List[str]:
    """Return the sorted list of chrome-devtools-mcp tool names."""
    return list(CHROME_DEVTOOLS_TOOL_NAMES)


def claude_chrome_devtools_tool_names() -> List[str]:
    """Return tool names in Claude Code's ``mcp__chrome-devtools__<name>`` format."""
    return [f"mcp__chrome-devtools__{name}" for name in CHROME_DEVTOOLS_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Per-harness server fragments
# ---------------------------------------------------------------------------

_NPX_CMD = "npx"
_NPX_ARGS = ["-y", "chrome-devtools-mcp@latest"]


def _server_entry_claude(args: List[str]) -> Dict:
    """Claude Code / generic mcpServers entry."""
    return {"chrome-devtools": {"command": _NPX_CMD, "args": args}}


def _server_entry_gemini(args: List[str]) -> Dict:
    """Gemini — same as Claude but with ``trust: true``."""
    return {"chrome-devtools": {"command": _NPX_CMD, "args": args, "trust": True}}


def _server_entry_qwen(args: List[str]) -> Dict:
    """Qwen — same structure as Gemini."""
    return {"chrome-devtools": {"command": _NPX_CMD, "args": args, "trust": True}}


def _server_entry_codex(args: List[str]) -> List[str]:
    """Codex — returns list of ``-c`` flag pairs for CLI injection."""
    args_toml = "[" + ", ".join(f'"{a}"' for a in args) + "]"
    return [
        "-c", f'mcp_servers.chrome-devtools.command="{_NPX_CMD}"',
        "-c", f"mcp_servers.chrome-devtools.args={args_toml}",
    ]


def _server_entry_kilocode(args: List[str]) -> Dict:
    """Kilo Code — includes ``alwaysAllow`` for auto-approval."""
    return {"chrome-devtools": {
        "command": _NPX_CMD,
        "args": args,
        "alwaysAllow": list(CHROME_DEVTOOLS_TOOL_NAMES),
    }}


def _server_entry_opencode(args: List[str]) -> Dict:
    """OpenCode — different structure: ``type``, ``command`` as array."""
    return {"chrome-devtools": {
        "type": "local",
        "command": [_NPX_CMD, *args],
    }}


def _opencode_permissions() -> Dict[str, str]:
    """OpenCode permission entries for chrome-devtools tools."""
    return {t: "allow" for t in CHROME_DEVTOOLS_TOOL_NAMES}


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


def server_fragment(
    agent_name: str, *, headless: bool = False,
) -> Union[Dict, List[str]]:
    """Return the chrome-devtools MCP server fragment for a given agent.

    For most agents, returns a dict to merge into ``mcpServers``.
    For Codex, returns a list of ``-c`` flag pairs.
    For OpenCode agents (minimax/glm), returns the ``mcp`` dict entry.

    When *headless* is True, ``--headless`` is appended to the npx args.
    """
    args = list(_NPX_ARGS)
    if headless:
        args.append("--headless")
    fn = _FRAGMENT_MAP.get(agent_name, _server_entry_claude)
    return fn(args)
