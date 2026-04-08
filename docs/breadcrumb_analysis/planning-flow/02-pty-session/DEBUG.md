# Planning PTY Session — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|--------------|
| Django / ASGI | `taskit/taskit-backend/logs/taskit_detail.log` | WebSocket connect/disconnect, consumer errors |
| Frontend | browser console | WebSocket errors, xterm init errors |
| PTY process (odin) | see `planning-flow/03-orchestrator/DEBUG.md` | odin CLI internal logs |

## What to search for

| Symptom | Where to look | Search term |
|---------|---------------|-------------|
| Terminal shows "Connection closed: 4001" | `consumers.py:connect()` | spec status is not `planning` — check spec status in DB |
| Terminal shows "Connection closed: 4004" | `consumers.py:connect()` | spec doesn't exist — check spec id |
| Terminal shows "WebSocket error — is daphne running?" | browser console | ASGI server (daphne) not running or wrong port |
| Terminal connects but no output | `consumers.py:_start_planning()` | check board `working_dir`, check temp file creation, check `odin` binary on PATH |
| Claude Code opens but kickoff not sent | `consumers.py:on_output()` | `shortcuts` not found in buffer tail — Claude Code welcome screen may have changed, or output encoding issue |
| Plan written but Claude Code doesn't exit | `consumers.py:on_output()` | auto-exit detection: check `.odin/plans/plan_` and `wrote` in buffer, check `>` in last 50 chars. May need to adjust detection patterns. |
| Planning completes but tasks not created | `consumers.py:_complete_planning()` | sibling spec not found — check `run_start` timestamp logic |
| Spec stuck in `planning` status after odin exits | `consumers.py:_complete_planning()` | exception swallowed — add logging to `except` block |
| Terminal reconnects but shows no buffered output | `consumers.py:connect()` | `_active_sessions[spec_pk]` not found — session may have been GC'd or server restarted |
| Retry doesn't start a new session | `consumers.py:receive()` type=start | check `_active_sessions` — old session may still be present from previous attempt |
| `odin plan` command missing `--base-agent` flag | `consumers.py:_build_planning_command()` | `planner_config["agent"]` missing — check what was saved on the spec |
| tmux status bar visible in terminal | `consumers.py:_build_planning_command()` | `--direct` flag missing from command — should always be present for web UI |
| Whitespace on right side of terminal | `consumers.py:_start_planning()` | PTY dimensions not matching terminal — check `start` message has `rows`/`cols`, and `PtySession` receives them |

## Quick commands

```bash
# Check spec status and planner_config
cd taskit/taskit-backend && python manage.py shell -c "
from tasks.models import Spec
s = Spec.objects.get(pk=<spec_id>)
print('status:', s.status)
print('planner_config:', s.planner_config)
print('odin_id:', s.odin_id)
"

# Check if tasks were merged onto the spec
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --sections tasks

# Check for orphaned odin-created sibling specs (temp specs not yet merged or cleaned up)
cd taskit/taskit-backend && python manage.py shell -c "
from tasks.models import Spec
for s in Spec.objects.filter(title__regex=r'spec_\d+_[a-z0-9_]+\.md$'):
    print(s.id, s.title, s.status, s.created_at)
"

# Check if a temp spec file is still on disk (should be deleted after planning)
ls -la <board_working_dir>/spec_<spec_id>_*.md

# Manually reconstruct the odin plan command that would be run for a spec
cd taskit/taskit-backend && python manage.py shell -c "
from tasks.models import Spec
from tasks.consumers import _build_planning_command
s = Spec.objects.get(pk=<spec_id>)
print(_build_planning_command('/tmp/fake_spec.md', s.planner_config))
"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `TASKIT_INTERNAL_URL` | URL injected as `ODIN_FORCED_BACKEND_URL` into PTY env | `http://localhost:8000` |
| `ODIN_WORKING_DIR` | Fallback cwd for PTY if board has no `working_dir` | `os.getcwd()` |
| `VITE_HARNESS_TIME_API_URL` | Frontend base URL — converted to `ws://` for WebSocket | `http://localhost:8000` |

## Common breakpoints

- `tasks/consumers.py:PlanningConsumer.connect()` — check if session is found in `_active_sessions` (reconnect path vs new session)
- `tasks/consumers.py:_start_planning()` — inspect `cmd`, `cwd`, and `spec_path` before PTY spawn
- `tasks/consumers.py:_build_planning_command()` — verify `--base-agent` and `--base-model` flags are present
- `tasks/consumers.py:_complete_planning()` — check if `odin_spec` is found; if None, tasks won't be merged
- `tasks/pty_session.py:PtySession.start()` — `_reader` thread: add print before/after `on_exit(rc)` to confirm exit fires
- `PlanningTerminal.tsx:ws.onclose` — check `planningCompleteRef.current` and `e.code` to distinguish normal vs unexpected close
