"""Single source of truth for MCP tool names and per-CLI config formatters.

This module introspects the FastMCP server instance to discover registered
tool names, then provides formatter functions that produce the correct
config file content for each agent CLI.  Adding a new ``@mcp.tool()`` in
``server.py`` automatically propagates to all generated configs.

Usage::

    from odin.mcps.taskit_mcp.config import (
        tool_names, MCP_CONFIG_MAP, MCP_FORMATTERS,
        format_claude, format_gemini, ...
    )

    names = tool_names()          # ["taskit_add_comment", "taskit_add_attachment"]
    content = format_claude(env)  # JSON string for .mcp.json
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List


# ---------------------------------------------------------------------------
# Tool name introspection
# ---------------------------------------------------------------------------

# Fallback tool names used when FastMCP introspection fails (e.g. fastmcp
# not installed).  Keep in sync with ``@mcp.tool()`` registrations in
# ``server.py``.  Introspection is preferred — this list is the safety net.
_FALLBACK_TOOL_NAMES: List[str] = sorted([
    "taskit_add_comment",
    "taskit_add_attachment",
])


def get_tool_names() -> List[str]:
    """Return tool names registered on the FastMCP server instance.

    Uses ``asyncio.run(mcp.list_tools())`` to introspect the server.
    If already inside an event loop, falls back to creating a new loop
    in a thread.  Falls back to ``_FALLBACK_TOOL_NAMES`` if the server
    module can't be imported (e.g. missing ``fastmcp`` dependency).
    """
    try:
        from odin.mcps.taskit_mcp.server import mcp
    except Exception:
        return list(_FALLBACK_TOOL_NAMES)

    import asyncio

    async def _list():
        tools = await mcp.list_tools()
        return sorted(t.name for t in tools)

    def _run_in_new_loop():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_list())
        finally:
            loop.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        return asyncio.run(_list())

    # Already inside an event loop — run in a thread with its own loop
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run_in_new_loop).result(timeout=5)


@lru_cache(maxsize=1)
def tool_names() -> List[str]:
    """Cached wrapper around :func:`get_tool_names`.

    Safe to call repeatedly — the result is computed once per process.
    """
    return get_tool_names()


def server_name() -> str:
    """Return the MCP server name (e.g. ``"taskit"``)."""
    try:
        from odin.mcps.taskit_mcp.server import mcp
        return mcp.name
    except Exception:
        return "taskit"


def claude_tool_names() -> List[str]:
    """Return tool names in Claude Code's prefixed format.

    Claude Code references MCP tools as ``mcp__<server>__<tool>``.
    The ``alwaysAllow`` list in ``.mcp.json`` must use this format,
    otherwise Claude Code will prompt for permission on every call
    (or silently skip tools in ``-p`` mode).
    """
    prefix = f"mcp__{server_name()}__"
    return [f"{prefix}{name}" for name in tool_names()]


# ---------------------------------------------------------------------------
# Server entry dicts (reusable building blocks for multi-server merging)
# ---------------------------------------------------------------------------


def _server_entry_claude(env: dict) -> Dict:
    """Return the taskit server dict for Claude Code's ``mcpServers``."""
    return {"taskit": {"command": "taskit-mcp", "env": env}}


def _server_entry_gemini(env: dict) -> Dict:
    """Return the taskit server dict for Gemini (with ``trust: true``)."""
    return {"taskit": {"command": "taskit-mcp", "env": env, "trust": True}}


def _server_entry_qwen(env: dict) -> Dict:
    """Return the taskit server dict for Qwen (with ``trust: true``)."""
    return {"taskit": {"command": "taskit-mcp", "env": env, "trust": True}}


def _server_entry_codex(env: dict) -> Dict:
    """Return the taskit TOML section lines as a dict (command + env)."""
    return {"taskit": {"command": "taskit-mcp", "env": env}}


def _server_entry_kilocode(env: dict) -> Dict:
    """Return the taskit server dict for Kilo Code (with ``alwaysAllow``)."""
    return {"taskit": {
        "command": "taskit-mcp", "env": env,
        "alwaysAllow": tool_names(),
    }}


def _server_entry_opencode(env: dict) -> Dict:
    """Return the taskit server dict for OpenCode."""
    return {"taskit": {
        "type": "local",
        "command": ["taskit-mcp"],
        "environment": env,
    }}


_SERVER_ENTRY_MAP = {
    "claude": _server_entry_claude,
    "gemini": _server_entry_gemini,
    "qwen": _server_entry_qwen,
    "codex": _server_entry_codex,
    "kilocode": _server_entry_kilocode,
    "minimax": _server_entry_opencode,
    "glm": _server_entry_opencode,
}


def server_entry(agent_name: str, env: dict) -> Dict:
    """Return the taskit server entry dict for *agent_name*.

    This is the building block used by the orchestrator to merge multiple
    MCP servers into a single config.  Each entry is a dict like
    ``{"taskit": {...}}`` that can be merged with entries from other MCP
    packages.
    """
    fn = _SERVER_ENTRY_MAP.get(agent_name, _server_entry_claude)
    return fn(env)


# ---------------------------------------------------------------------------
# Per-CLI config formatters (backward-compatible wrappers)
# ---------------------------------------------------------------------------


def format_claude(env: dict) -> str:
    """Claude Code ``.mcp.json`` format.

    Claude Code does NOT support ``alwaysAllow`` in ``.mcp.json``.
    Tool permissions are granted via the ``--allowedTools`` CLI flag,
    which the Claude harness injects in ``build_execute_command()``.

    The config file only defines the MCP server (command + env).
    """
    return json.dumps({"mcpServers": _server_entry_claude(env)}, indent=2)


def format_gemini(env: dict) -> str:
    """Gemini ``.gemini/settings.json`` format.

    ``trust: true`` bypasses all tool-call confirmations so -p mode works.
    """
    return json.dumps({"mcpServers": _server_entry_gemini(env)}, indent=2)


def format_qwen(env: dict) -> str:
    """Qwen ``.qwen/settings.json`` format.

    ``trust: true`` bypasses all tool-call confirmations so -p mode works.
    """
    return json.dumps({"mcpServers": _server_entry_qwen(env)}, indent=2)


def format_codex(env: dict) -> str:
    """Codex ``.codex/config.toml`` format (TOML)."""
    lines = ["[mcp_servers.taskit]", 'command = "taskit-mcp"', "", "[mcp_servers.taskit.env]"]
    for k, v in env.items():
        lines.append(f'{k} = "{v}"')
    return "\n".join(lines) + "\n"


def format_kilocode(env: dict) -> str:
    """Kilo Code ``.kilocode/mcp.json`` format.

    ``alwaysAllow`` auto-approves the listed tools without prompting.
    """
    return json.dumps({"mcpServers": _server_entry_kilocode(env)}, indent=2)


def format_claude_settings(mcps: List[str] | None = None) -> str:
    """Claude Code ``.claude/settings.local.json`` permissions.

    Grants Read/Write/Edit/Bash and all MCP tools so Claude Code
    can operate non-interactively when spawned by ``odin exec``.

    When *mcps* includes ``"mobile"``, mobile tool names and the mobile
    MCP server are added to permissions and enabled servers.
    """
    allowed_tools = list(claude_tool_names())
    enabled_servers = [server_name()]

    if mcps and "mobile" in mcps:
        from odin.mcps.mobile_mcp.config import claude_mobile_tool_names
        allowed_tools.extend(claude_mobile_tool_names())
        enabled_servers.append("mobile")

    if mcps and "chrome-devtools" in mcps:
        from odin.mcps.chrome_devtools_mcp.config import claude_chrome_devtools_tool_names
        allowed_tools.extend(claude_chrome_devtools_tool_names())
        enabled_servers.append("chrome-devtools")

    return json.dumps({
        "permissions": {
            "allow": [
                "Read",
                "Write",
                "Edit",
                "Bash(*)",
            ] + allowed_tools,
        },
        "enabledMcpjsonServers": enabled_servers,
        "enableAllProjectMcpServers": True,
    }, indent=2)


def format_opencode(env: dict) -> str:
    """OpenCode ``opencode.json`` format.

    OpenCode uses ``"environment"`` (not ``"env"``) for env vars,
    ``"mcp"`` as the top-level key (not ``"mcpServers"``), and
    ``"command"`` as an array.

    ``permission`` with ``"allow"`` auto-approves MCP tools so they
    work in non-interactive ``--auto`` mode.
    """
    permission = {t: "allow" for t in tool_names()}
    return json.dumps({
        "permission": permission,
        "mcp": _server_entry_opencode(env),
    }, indent=2)


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

# Agent name -> relative config path within working directory.
MCP_CONFIG_MAP: Dict[str, str] = {
    "claude":    ".mcp.json",
    "gemini":    ".gemini/settings.json",
    "qwen":      ".qwen/settings.json",
    "codex":     ".codex/config.toml",
    "kilocode":  ".kilocode/mcp.json",
    "minimax":   "opencode.json",
    "glm":       "opencode.json",
}

# Agent name -> formatter function.
MCP_FORMATTERS: Dict[str, callable] = {
    "claude":    format_claude,
    "gemini":    format_gemini,
    "qwen":      format_qwen,
    "codex":     format_codex,
    "kilocode":  format_kilocode,
    "minimax":   format_opencode,
    "glm":       format_opencode,
}

# Documents which CLIs require explicit tool approval and what config key
# they use.  Used by contract tests to verify approval is always present.
AGENTS_WITH_TOOL_APPROVAL: Dict[str, str] = {
    "claude":    "permissions",     # .claude/settings.local.json -> permissions.allow
    "gemini":    "trust",           # .gemini/settings.json -> mcpServers.taskit.trust
    "qwen":      "trust",           # .qwen/settings.json -> mcpServers.taskit.trust
    "kilocode":  "alwaysAllow",     # .kilocode/mcp.json -> mcpServers.taskit.alwaysAllow
    "minimax":   "permission",      # opencode.json -> permission
    "glm":       "permission",      # opencode.json -> permission
}
