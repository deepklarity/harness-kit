# CLAUDE.md

This file provides project-specific guidance for coding agents working in `odin/`.

## Mission

Odin is a task-board orchestration CLI for human + AI collaboration. Treat it as a flexible board system where planning, execution, review, and assembly are all represented as tasks, not hardcoded pipeline stages.

## Core Product Rules

- **Default first**: `odin run` and staged commands should work out of the box with sensible defaults.
- **Suggestive, not prescriptive**: planner assignments are recommendations; users can reassign before execution.
- **Everything is a task**: implementation, review, validation, and assembly can all be created as normal tasks.
- **Human in the loop**: preserve user control and easy override paths in CLI and task state.

## Workflow Expectations

- Keep staged flow healthy: `plan` -> review (`specs` / `status` / `show` / `assign`) -> `exec`.
- Do not introduce rigid behavior that forces one fixed sequence.
- Preserve prefix-based task ID UX (`odin show a1b2`, `odin assign d4e5 gemini`).

## Architectural Guardrails

- `src/odin/orchestrator.py` coordinates planning and single-task execution. Creates spec archives and tags tasks with `spec_id`.
- `src/odin/specs.py` manages spec archives (`.odin/specs/`) and derives spec status from tasks (pure function, no stored status).
- `src/odin/taskit/` is the task backend (JSON files + index) with task lifecycle, prefix resolution, and `spec_id` filtering.
- `src/odin/backends/` contains pluggable task backends (local disk, TaskIt REST). Registry/decorator pattern.
- `src/odin/harnesses/` contains agent integrations using the registry/decorator pattern.
- Keep graceful degradation when optional integrations are unavailable (for example quota data from `harness_usage_status`).
- **No `run.json`** — all state lives in spec archives + tasks. Spec status is always derived.
- **Single-task executor** — odin executes one task at a time via `exec_task()`. Dependency resolution and scheduling belong to TaskIt + Celery. No bulk `exec_all()`.
- **Mock mode** — `exec_task(mock=True)` skips all backend writes (status, comments, cost tracking). The harness still runs and returns results.
- `src/odin/tools/core.py` is `TaskItToolClient` — shared HTTP client for task comments, questions, and proof-of-work. Used by both the CLI tool and the MCP server.
- `src/odin/mcps/taskit_mcp/` is the TaskIt MCP server. Thin wrapper over `TaskItToolClient` exposed as MCP tools via FastMCP. Blocking question/reply is the key feature — `timeout=0` polls indefinitely.
- **MCP harness integration** — `_generate_mcp_config()` in orchestrator writes per-CLI MCP config files. Each CLI has its own config location and format:
  - **Claude Code**: `.mcp.json` (also supports `--mcp-config` flag — orchestrator writes to `.odin/logs/mcp_<task_id>.json` and passes the path)
  - **Gemini**: `.gemini/settings.json` in working dir (auto-discovered, no CLI flag)
  - **Qwen**: `.qwen/settings.json` in working dir (auto-discovered, no CLI flag)
  - **Codex**: `.codex/config.toml` in working dir (TOML format, auto-discovered)
  - **Kilo Code / OpenCode**: `opencode.json` in working dir (both `kilo` and `opencode` CLIs read this; different JSON structure: `"mcp"` key, `"type": "local"`, `command` as array)

  Only Claude Code supports `--mcp-config` — all other CLIs rely on project-local config auto-discovery. Auth tokens are extracted from the backend's `TaskItAuth` handler.

## Agent Harness Conventions

- CLI harnesses are non-interactive subprocess wrappers with clear timeout/error handling.
- API harnesses should follow the same `BaseHarness` contract and consistent `TaskResult` behavior.
- New harnesses must use `@register_harness("<name>")` and be imported by registry bootstrap.
- Respect `cli_command` overrides from config instead of hardcoding binaries.
- **Streaming vs non-streaming parity**: CLI harnesses have two code paths — `execute()` (non-streaming) and `execute_streaming()` (streaming). Both must produce equivalent filtered output. Raw stream-json from subprocess stdout contains protocol framing (system events, hooks, init messages) that must be stripped via `extract_text_from_stream()`/`extract_text_from_line()` before the output is used. When modifying either path, verify the other stays consistent.

## Config and Defaults

- Honor config precedence: explicit `--config` -> `.odin/config.yaml` -> `~/.odin/config.yaml` -> built-in defaults.
- Keep defaults safe and usable when no config file exists.
- Prefer additive config changes over breaking schema churn.

## TaskIt Authentication

Set env vars in `.env` in the directory where Odin runs. Tokens are cached (1-hour lifetime, auto-refresh at 55 min). Without env vars, Odin connects without auth (for local dev).

```
ODIN_ADMIN_USER=admin@example.com    # must exist in TaskIt with is_admin=True
ODIN_ADMIN_PASSWORD=your_password
```

## Documentation Consistency

When behavior changes, update `odin/docs/` and `odin/README.md` to stay aligned on the task-board model, suggestive assignment, and staged workflows.

## Tests

Two reference docs in `tests/`:

- **`testcase_readme.md`** — Complete test case index. One-liner per test, organized by file. Consult this to understand what's covered and where tests live.
- **`TEST_PLAN.md`** — Living checklist tracking coverage gaps and priorities. Check/update when adding features.

Tests are organized into subdirectories by dependency profile:

- **`unit/`** — Pure logic, no I/O, no mocks. Fastest tests.
- **`disk/`** — Disk I/O but no network, no subprocesses.
- **`mock/`** — Mocked subprocesses/HTTP. No real services.
- **`integration/`** — Real CLI agents required. Excluded by default.

Shared fixtures live in `conftest.py` at the tests root.

When adding features or fixing bugs:
1. Check `tests/TEST_PLAN.md` for related unchecked items
2. After writing tests, mark items as `[x]` in the plan and update `testcase_readme.md`
3. If new testable behavior is introduced, add it to both docs
4. Place new tests in the appropriate subdirectory based on their dependency profile

## Plan Modes

`odin plan` defaults to **interactive mode** (opens a tmux session where the user chats with the planning agent). For non-interactive use:

- `--auto` — one-shot decomposition, streams output to stdout. Use for manual testing.
- `--quiet` — implies `--auto`, shows a spinner instead of streaming. Use for CI.

**Important:** `odin plan` (all modes) invokes `claude -p` as a subprocess. It cannot run from inside another Claude Code session (nested calls fail). Always test from a regular terminal.

## Debugging Techniques

**Run diagnostic scripts first.** See root `CLAUDE.md` → "Data Inspection & Debugging" for the full script reference and decision table.

```bash
# Odin-specific log viewing (from working dir)
odin logs                    # Last 50 lines of latest run log
odin logs debug              # odin_detail.log (tracebacks)
odin logs -f                 # Follow all running tasks
odin logs debug -f           # Tail odin_detail.log
odin logs -b <board_id>     # Access logs from any directory
tail -f ../taskit/taskit-backend/logs/taskit.log  # TaskIt app log
```

## Quick Commands

Run from `odin/`:

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v               # static tests (integration/ excluded by default)
python -m pytest tests/unit/ -v          # pure logic tests only
python -m pytest tests/disk/ -v          # disk I/O tests only
python -m pytest tests/mock/ -v          # mocked subprocess/HTTP tests only
python -m pytest tests/integration/ -v   # integration tests (requires agent CLIs)
python -m pytest -m "not llm and not tmux_real" tests/ -v  # skip markers
odin plan sample_specs/poem_spec.md              # interactive (default, opens tmux)
odin plan sample_specs/poem_spec.md --auto       # non-interactive, streams output
odin plan sample_specs/poem_spec.md --quiet      # non-interactive, spinner only
odin specs
odin status
odin exec <task_id>              # single-task execution (foreground)
odin exec <task_id> --mock       # mock mode (no backend writes)
odin reflect <task_id>           # trigger reflection audit on completed task
odin reflect <task_id> --model claude-opus-4-6 --agent claude  # with explicit model/agent
```

**Working directory:** `odin/temp_test_dir/` is the standard directory for running Odin + TaskIt together. It contains a `.env` with `ODIN_ADMIN_USER`, `ODIN_ADMIN_PASSWORD`, and `ODIN_FIREBASE_API_KEY` for authenticating against the live TaskIt backend. Odin's `load_config()` calls `load_dotenv(cwd/.env)` automatically. Integration tests also load from this `.env` via dotenv.

**Manual integration test:** From `odin/temp_test_dir/`, run `odin init`, then `odin plan ../sample_specs/mini_spec.md --auto` and `odin exec <task_id>`. This directory is not committed — create it as needed. Must run from a regular terminal (not inside Claude Code).

**MCP server test:** `pip install -e ".[mcp]"` then `TASKIT_URL=http://localhost:8000 taskit-mcp` (stdio). Use MCP Inspector: `npx @modelcontextprotocol/inspector taskit-mcp`.
