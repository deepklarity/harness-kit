# Harness Isolation Testing — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Odin orchestrator | `.odin/logs/run_<spec_id>.jsonl` | task_assigned, run_started, run_completed events |
| Harness output | `.odin/logs/output_<task_id>.txt` | Extracted text from agent subprocess |
| Harness trace | `.odin/logs/trace_<task_id>.jsonl` | Raw stream-json from agent subprocess |
| MCP config | `.odin/logs/mcp_<task_id>.json` | Generated MCP config (Claude only) |
| Working dir configs | `.mcp.json`, `.gemini/settings.json`, etc. | Auto-discovery configs for non-Claude agents |
| Test output | terminal / pytest capture | Test assertions, fixture setup/teardown |

## What to search for

| Symptom | Where to look | Search term / command |
|---------|--------------|----------------------|
| Agent can't see MCP tools | Generated config file for that agent | Check the config file exists and has correct structure |
| Claude prompt for MCP approval | `.claude/settings.local.json` | Verify `permissions.allow` includes `mcp__taskit__*` |
| Codex ignores MCP env | Codex `-c` flags in test output | `mcp_servers.taskit` in command list |
| Token extraction returns empty | Trace file for the task | `modelUsage` or `step_finish` in trace JSONL |
| Cost not recorded | `costs_sp_<spec_id>.json` | Check file exists, has `input_tokens` > 0 |
| Streaming chunks arrive all at once | `mock/test_streaming.py` assertions | Time span between first and last chunk |
| Wrong text extracted from stream | `extract_text_from_line` logic | Test with actual JSON line from the problematic CLI |
| Config file in wrong location | `_generate_mcp_config()` in orchestrator | Check `working_dir` vs `log_dir` path |
| Mobile tools missing from allowed list | `context["mcp_allowed_tools"]` | `mcp__mobile__` prefix in the list |
| `mcps` field ignored from YAML | `config.py :: _load_from_yaml()` | `raw.get("mcps")` line exists |

## Quick commands

```bash
# Run all harness-related unit tests
cd odin && python -m pytest tests/unit/test_mcp_harness_integration.py tests/unit/test_mobile_mcp_config.py tests/unit/test_config.py -v

# Run MCP config generation tests only (fastest feedback loop)
cd odin && python -m pytest tests/unit/test_mcp_harness_integration.py -v -k "McpConfig"

# Run multi-server merging tests only
cd odin && python -m pytest tests/unit/test_mcp_harness_integration.py -v -k "MultiServer"

# Run streaming tests
cd odin && python -m pytest tests/mock/test_streaming.py -v

# Run trace/text extraction tests
cd odin && python -m pytest tests/mock/test_trace_logging.py -v

# Run token/cost tests
cd odin && python -m pytest tests/unit/test_cost_estimator.py tests/disk/test_cost_tracking.py -v

# Run mock harness tests (quick sanity check)
cd odin && python -m pytest tests/mock/test_mock_harness.py -v

# Run ALL harness isolation tests (unit + mock, no live agents)
cd odin && python -m pytest tests/unit/test_mcp_harness_integration.py tests/unit/test_mobile_mcp_config.py tests/unit/test_config.py tests/mock/test_streaming.py tests/mock/test_trace_logging.py tests/mock/test_mock_harness.py -v

# Run live integration tests (requires agent CLIs on PATH)
cd odin && python -m pytest tests/integration/test_real.py -v

# Inspect a generated MCP config file
cat .odin/logs/mcp_<task_id>.json | python -m json.tool

# Check what MCP tools are registered on the FastMCP server
python -c "from odin.mcps.taskit_mcp.config import tool_names; print(tool_names())"

# Check mobile tool names
python -c "from odin.mcps.mobile_mcp.config import mobile_tool_names; print(mobile_tool_names())"

# Check Claude's full allowed tool list (taskit + mobile)
python -c "
from odin.mcps.taskit_mcp.config import claude_tool_names
from odin.mcps.mobile_mcp.config import claude_mobile_tool_names
print('Taskit:', claude_tool_names())
print('Mobile:', claude_mobile_tool_names())
"

# Verify a specific harness command includes MCP flags
python -c "
from odin.harnesses.claude import ClaudeHarness
from odin.models import AgentConfig
h = ClaudeHarness(AgentConfig(cli_command='claude'))
cmd = h.build_execute_command('test', {
    'mcp_config': '/tmp/mcp.json',
    'mcp_allowed_tools': ['mcp__taskit__taskit_add_comment'],
})
print(' '.join(cmd))
"

# Check what config.yaml mcps field resolves to
python -c "
from odin.config import load_config
cfg = load_config()
print('mcps:', cfg.mcps)
"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `TASKIT_URL` | Base URL for MCP server env injection | `http://localhost:8000` |
| `TASKIT_AUTH_TOKEN` | Auth token injected into MCP config env | Empty (graceful) |
| `TASKIT_TASK_ID` | Task ID for MCP tool calls | Set by orchestrator per-task |
| `TASKIT_AUTHOR_EMAIL` | Actor identity in MCP comments | `<agent>@odin.agent` |
| `ODIN_ADMIN_USER` | Admin email for auth token fetch | None |
| `ODIN_ADMIN_PASSWORD` | Admin password for auth token fetch | None |

## Common breakpoints

- `orchestrator.py :: _generate_mcp_config()` — check `mcps`, `has_taskit`, `has_mobile` before branching
- `orchestrator.py :: _execute_task()` around line 1859 — inspect `context` dict after MCP setup
- `taskit_mcp/config.py :: server_entry()` — verify which `_server_entry_*` is dispatched
- `mobile_mcp/config.py :: server_fragment()` — verify fragment shape per agent
- `claude.py :: build_execute_command()` — verify final CLI command list
- `codex.py :: build_execute_command()` around line 34 — verify `-c` flag injection
- `claude.py :: _extract_token_usage()` — check if `modelUsage` or `step_finish` matched
- `base.py :: extract_text_from_line()` — check which JSON type branch fires
- `config.py :: _load_from_yaml()` line with `mcps=raw.get("mcps")` — verify YAML parsing

## Coverage gaps (known)

| Gap | Impact | Priority |
|-----|--------|----------|
| No live MCP round-trip test | Can't verify agent actually calls MCP tools | Medium |
| Token extraction only for Claude | Gemini/Qwen/Codex usage not captured | Low (CLIs don't emit usage yet) |
| No e2e snapshot tests | No golden-file regression for trace parsing | Medium |
| Mobile MCP integration tests skip-marked | Full flow untested without emulator | Low (hardware dependency) |
| `_generate_all_mcp_configs` not unit-tested with mobile | Init-time mobile config untested | Medium |
| KiloCode harness not in streaming parametrize | MiniMax/GLM streaming untested | Low |
