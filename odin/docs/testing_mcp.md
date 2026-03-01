# MCP Testing Guide

## Architecture: Single Source of Truth

MCP tool names are defined **once** — in `server.py` via `@mcp.tool()` decorators. The `config.py` module introspects the FastMCP instance to discover registered tools, then provides formatter functions that produce per-CLI config files.

```
server.py                     config.py                    orchestrator.py / cli.py
  @mcp.tool()   ──introspect──>  tool_names()  ──used by──>  _generate_mcp_config()
  @mcp.tool()                    format_claude()              _generate_all_mcp_configs()
                                 format_gemini()
                                 MCP_CONFIG_MAP
                                 MCP_FORMATTERS
```

**Key property**: Adding a new `@mcp.tool()` in `server.py` automatically propagates to all config outputs. No hardcoded tool lists to update.

## Tool Approval Per CLI

Each CLI has a different mechanism for granting MCP tool permissions:

| CLI | Mechanism | Where |
|-----|-----------|-------|
| **Claude Code** | `--allowedTools` CLI flag | Harness injects in `build_execute_command()` |
| **Gemini** | `"trust": true` | `.gemini/settings.json` config file |
| **Qwen** | `"trust": true` | `.qwen/settings.json` config file |
| **Kilo Code** | `"alwaysAllow": [...]` | `.kilocode/mcp.json` config file |
| **OpenCode** | `"permission": {"tool": "allow"}` | `opencode.json` config file |

**Important**: Claude Code does NOT support `alwaysAllow` in `.mcp.json`. Its config file only defines the server (command + env). The orchestrator passes `mcp_allowed_tools` (prefixed as `mcp__taskit__<tool>`) in the context dict, and the Claude harness converts them to `--allowedTools` on the command line.

## Contract Tests

`tests/unit/test_mcp_contract.py` guards against the class of bugs where tool lists drift out of sync:

| Test | What it catches |
|------|----------------|
| `test_tool_names_match_server` | FastMCP introspection fails or returns empty |
| `test_claude_config_has_no_alwaysAllow` | Claude config has unsupported alwaysAllow key |
| `test_claude_allowed_tools_injected_in_cli` | Claude harness missing --allowedTools flag |
| `test_claude_tool_names_cover_all_server_tools` | Prefixed tool list drifts from server tools |
| `test_kilocode_alwaysAllow_contains_all_server_tools` | Kilo Code missing a tool |
| `test_opencode_permission_contains_all_server_tools` | OpenCode missing tool → blocked in --auto mode |
| `test_approval_mechanism_present[claude/gemini/qwen/minimax]` | Agent missing its approval mechanism |
| `test_every_formatter_has_config_path` | Formatter added without file path mapping |
| `test_every_config_path_has_formatter` | File path added without formatter |
| `test_all_formatters_produce_valid_output` | Formatter returns empty or unparseable content |

## Running MCP Tests

```bash
cd odin

# Contract tests only
python -m pytest tests/unit/test_mcp_contract.py -v

# Harness integration tests (config generation + CLI flags)
python -m pytest tests/unit/test_mcp_harness_integration.py -v

# Chrome DevTools MCP config tests
python -m pytest tests/unit/test_chrome_devtools_mcp_config.py -v

# All MCP-related tests
python -m pytest tests/unit/test_mcp_contract.py tests/unit/test_mcp_harness_integration.py tests/unit/test_chrome_devtools_mcp_config.py -v

# All unit tests
python -m pytest tests/unit/ -v
```

## How to Add a New MCP Tool

1. Add `@mcp.tool()` decorator in `server.py`
2. Run `python -m pytest tests/unit/test_mcp_contract.py -v` — all tests should pass automatically
3. No other files need updating

The contract tests verify that `tool_names()` includes the new tool and that all approval configs (CLI flags and config-file keys) include it.

## How to Add a New Agent CLI

1. **Add formatter** in `config.py`: `def format_newagent(env: dict) -> str:`
2. **Add to maps** in `config.py`:
   - `MCP_CONFIG_MAP["newagent"] = ".newagent/config.json"`
   - `MCP_FORMATTERS["newagent"] = format_newagent`
   - If the CLI needs tool approval: `AGENTS_WITH_TOOL_APPROVAL["newagent"] = "approval_key"`
3. **If CLI uses a CLI flag** (like Claude's `--allowedTools`): update the harness's `build_execute_command()` to read from `context["mcp_allowed_tools"]`
4. **Add contract test** in `test_mcp_contract.py`:
   - Add to `test_approval_mechanism_present` parametrize list
5. Run tests to verify
