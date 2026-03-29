# Board → Project Lifecycle — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Django | `taskit/taskit-backend/logs/taskit.log` | Board CRUD, API errors |
| Django detail | `taskit/taskit-backend/logs/taskit_detail.log` | Request/response, serializer errors |
| Odin | `.odin/logs/run_*.jsonl` | Execution events per run |
| Odin detail | `.odin/logs/odin_detail.log` | Tracebacks, worktree operations |
| DAG executor | `taskit/taskit-backend/logs/dag_exec_*.log` | Per-task execution log |
| Frontend | browser console | API call errors, state updates |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Board not appearing in UI | browser network tab | `GET /api/boards/` response |
| Task has no working directory | `.odin/logs/odin_detail.log` | `working_dir` |
| odin exec fails with "no such directory" | `.odin/logs/run_*.jsonl` | `FileNotFoundError` or `No such file` |
| Spec not linked to board | `odin/src/odin/backends/taskit.py` | `board_id` in spec creation payload |
| board_id mismatch CLI vs UI | `.odin/config.yaml` vs UI board detail | compare `taskit.board_id` with UI board ID |
| Spec branch not created | `.odin/logs/odin_detail.log` | `Failed to create spec branch` |
| No git repo after init | project directory | `ls -la .git/` |
| Branch/merge shows `—` in UI | spec metadata | check `spec.metadata.branch` — absent means no git repo at plan time |
| Wrong odin binary used | error traceback | `pipx/venvs/odin/` = stable (stale), `harness-kit-dev/odin/src/` = dev (current) |
| Config missing agents | `.odin/config.yaml` | YAML has no `agents` section — built-in defaults should still be injected |
| Base agent not found | odin stderr | `Base agent 'claude' not found in config` — stale binary or config loading bug |

## Quick commands

```bash
# Check board exists and its state
cd taskit/taskit-backend && python testing_tools/board_overview.py <board_id>

# Check what board_id odin is configured to use
grep board_id <project-dir>/.odin/config.yaml

# Check if odin is initialized in a directory
ls -la <project-dir>/.odin/

# Check if git repo exists (required for worktree isolation)
git -C <project-dir> rev-parse --git-dir 2>/dev/null && echo "git repo exists" || echo "NO git repo"

# Check spec metadata for branch info
cat <project-dir>/.odin/specs/<spec_id>.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('metadata',{}), indent=2))"

# Check which odin binary is being used
which odin && which odin-dev
# Verify editable install points to dev source
pipx list 2>/dev/null | grep -A2 odin

# Check working_dir resolution for a task
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic

# Verify spec is on correct board
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --brief

# Check global board registry
cat ~/.odin/boards.json

# Full re-init (overwrites config, regenerates everything)
cd <project-dir> && odin-dev init --force --board-id <id> --base-url http://localhost:9101
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_WORKING_DIR` | Fallback working directory for task execution | None |
| `ODIN_CLI_PATH` | Path to odin binary (used by DAG executor) | `odin` |
| `ODIN_EXECUTION_STRATEGY` | `local` or `celery_dag` — how tasks execute | None |
| `ODIN_ADMIN_USER` | TaskIt auth — admin email | None |
| `ODIN_ADMIN_PASSWORD` | TaskIt auth — admin password | None |
| `ODIN_FIREBASE_API_KEY` | TaskIt auth — Firebase API key | None |

## Common breakpoints

- `odin/src/odin/cli.py :: init()` line 180 — odin init entry point
- `odin/src/odin/cli.py` line 262 — git repo check/creation
- `odin/src/odin/config.py :: load_config()` line 48 — config loading with search order
- `odin/src/odin/config.py :: _load_from_yaml()` line 154 — YAML parsing + agent default injection
- `odin/src/odin/orchestrator.py` line 161 — WorktreeManager initialization
- `odin/src/odin/orchestrator.py` line 357 — spec branch creation (plan time)
- `odin/src/odin/worktree.py :: create_spec_branch()` line 75 — git branch creation
- `tasks/views.py :: BoardViewSet.create()` — board creation entry point (UI path)
- `AppHeader.tsx :: board selector` — board dropdown rendering

## Common mistakes

| Mistake | What happens | Fix |
|---------|-------------|-----|
| Ran `odin` instead of `odin-dev` | Uses stale stable binary, code fixes don't apply | Check `which odin` — use `odin-dev` in dev instance |
| Ran `odin plan` without `odin init` first | No git repo → spec branch fails silently → no worktree metadata → UI shows `—` | Run `odin-dev init --board-id <id> --base-url <url>` first |
| Ran `odin init` then `odin plan` (stable then dev) | Config created by stable init, plan run by dev — may have different defaults | Always use same binary for all commands in a session |
| Re-ran `odin init` expecting config overwrite | Config skipped if exists (idempotent safety) | Use `--force` to overwrite, or just edit `.odin/config.yaml` |
| Board created in UI but no `odin init` | Board exists in DB but no project directory linkage | Run `odin-dev init --board-id <id>` in project directory |
