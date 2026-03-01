# Board → Project Lifecycle — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Django | `taskit/taskit-backend/logs/taskit.log` | Board CRUD, API errors |
| Django detail | `taskit/taskit-backend/logs/taskit_detail.log` | Request/response, serializer errors |
| Odin | `.odin/logs/run_*.jsonl` | Execution events per run |
| DAG executor | `taskit/taskit-backend/logs/dag_exec_*.log` | Per-task execution log |
| Frontend | browser console | API call errors, state updates |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Board not appearing in UI | browser console + network tab | `GET /api/boards/` response |
| Task has no working directory | `execution/local.py` log output | `working_dir` |
| odin exec fails with "no such directory" | `.odin/logs/run_*.jsonl` | `FileNotFoundError` or `No such file` |
| Spec not linked to board | `odin/src/odin/backends/taskit.py` | `board_id` in spec creation payload |
| board_id mismatch between CLI and UI | `.odin/config.yaml` vs UI board detail | compare `taskit.board_id` with UI board ID |
| Task created on wrong board | `tasks/views.py` task create endpoint | `board` field in POST payload |

## Quick commands

```bash
# Check board exists and its state
python taskit/taskit-backend/testing_tools/board_overview.py <board_id>

# Check what board_id odin is configured to use
cat .odin/config.yaml | grep board_id

# Check if odin is initialized in a directory
ls -la <project-dir>/.odin/

# List all boards via API (requires running server)
python taskit/taskit-backend/manage.py shell -c "from tasks.models import Board; print(list(Board.objects.values('id', 'name')))"

# Check working_dir resolution for a task
python taskit/taskit-backend/testing_tools/task_inspect.py <task_id> --json --sections basic

# Verify spec is on correct board
python taskit/taskit-backend/testing_tools/spec_trace.py <spec_id> --brief
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_WORKING_DIR` | Fallback working directory for task execution | None |
| `ODIN_CLI_PATH` | Path to odin binary | `odin` |
| `ODIN_EXECUTION_STRATEGY` | `local` or `celery_dag` — how tasks execute | None |

## Common breakpoints

- `tasks/views.py:BoardViewSet.create()` — board creation entry point
- `execution/local.py:trigger()` line 25 — working directory resolution
- `odin/src/odin/orchestrator.py:plan_spec()` line 246 — spec archiving with board_id
- `odin/src/odin/cli.py:init()` line 172 — odin init entry point
- `AppHeader.tsx:board selector` line 73 — board dropdown rendering
- `App.tsx:handleBoardChange` line 253 — board selection state update
