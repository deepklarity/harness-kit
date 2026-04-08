# Planning Flow

End-to-end trace from UI spec creation to tasks on the board. Merges the previously separate `ui-planning-terminal/`, `odin-plan-mode/`, and `spec-to-task-planning/` breadcrumbs — they all traced the same pipeline from different entry points.

Split into sub-flows because: UI entry, PTY terminal management, and orchestrator internals fail independently and are debugged with different tools.

## Sub-flows (execution order)

1. **01-spec-creation** — User submits spec in `CreateSpecModal` → API → DB row with `status=planning` → redirect to detail page → terminal mounts
2. **02-pty-session** — WebSocket → PTY → `odin plan --direct` → auto-kickoff → auto-exit → task merge → `status=planning_complete`
3. **03-orchestrator** — `odin plan` internals: spec archive, quota fetch, prompt building, agent dispatch (interactive/auto/quiet), plan parsing, two-pass task creation with routing

## Shared context

- Spec status values: `planning` → `planning_complete` (or `planning_failed`)
- WebSocket route: `ws/planning/<spec_pk>/`
- Session registry: `_active_sessions` dict in `tasks/consumers.py`
- PTY library: `ptyprocess` (gives subprocess a real terminal fd)
- Terminal renderer: `@xterm/xterm` v6 + `@xterm/addon-fit`
- Plan file: `.odin/plans/plan_<spec_id>.json` (written by agent, read by orchestrator)
- `--direct` flag: always passed from web UI, skips tmux (agent runs as direct subprocess in PTY)

## Environment variables

| Variable | Layer | Effect |
|----------|-------|--------|
| `VITE_HARNESS_TIME_API_URL` | Frontend | Base URL, converted to `ws://` for WebSocket |
| `TASKIT_INTERNAL_URL` | Backend | Injected as `ODIN_FORCED_BACKEND_URL` into PTY env |
| `ODIN_WORKING_DIR` | Backend | Fallback cwd if board has no `working_dir` |
| `ODIN_ADMIN_USER` | Odin | Auth user for TaskIt backend API |
| `ODIN_ADMIN_PASSWORD` | Odin | Auth password for TaskIt backend |

## Cross-references

- **Task routing depth**: `intelligent-agent-routing/` — covers the routing algorithm, config, and UI visibility in detail
- **Execution traces**: `trace-data-pipeline/` — covers how task execution traces are captured and rendered (planning trace gap documented in 03-orchestrator)
- **Post-planning lifecycle**: `spec-task-lifecycle/` — picks up where planning ends (DAG dispatch, execution, reflection)
