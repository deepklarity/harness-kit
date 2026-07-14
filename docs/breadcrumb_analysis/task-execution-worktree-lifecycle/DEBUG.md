# Task Execution + Worktree Lifecycle — Debug Guide

## Log Locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| DAG executor | `taskit/taskit-backend/logs/taskit.log` | Poll, concurrency gate, worktree-create triggers, dispatch, merge timing |
| Tracebacks | `taskit/taskit-backend/logs/taskit_detail.log` | Full stacks for worktree/merge/sandbox failures |
| odin exec (per task) | `.odin/logs/run_<run_id>.jsonl` | In-worktree execution, microVM boot/run, harness output |
| Worktree ops | odin logger `odin.worktree` | Branch create, worktree add/remove, auto-commit layers, merge results |
| Sandbox | odin logger `odin.harnesses.microsandbox` | `msb` command, VM boot, `_remove_sandbox` results |
| Merge locks | `.odin/locks/merge-<spec>.lock` | Presence = active or stuck merge |

## What to Search For

| Symptom | Where to look | Search term / command |
|---------|--------------|----------------------|
| Task running in project root instead of a worktree | Task status/metadata | Now usually **FAILED** via `no_worktree_no_optin` — check `dispatch_blocked_reason`; root run only if `board.allow_project_root_execution` |
| Worktree created inside another worktree (nested `.odin/worktrees`) | `_get_worktree_manager` root | Confirm manager rooted at `board.working_dir`, not `resolve_working_dir(task)` — the shadow-config trap |
| Agent's config not found inside the worktree | `_copy_agent_configs` | Verify `.odin/config.yaml` was copied into the worktree (`AGENT_CONFIG_PATHS`) |
| `.odin` accidentally committed to a task branch | Auto-commit Layer 1 | Should be unstaged by the runtime-path reset; check `_WORKTREE_LOCAL_PATHS` + worktree `.gitignore` |
| microVM left running after a task | Sandbox cleanup | Per-run `_remove_sandbox` in `_execute_sync` finally; if the process crashed, run `odin gc --prune` |
| Orphaned `odin-msb-*` sandboxes accumulating | Startup sweep | `sweep_startup_orphans` runs at exec start; `odin gc --prune` reaps the rest |
| Huge/junk commit on a task branch | Auto-commit Layer 2/3 | Pollution blocklist + `_AUTO_COMMIT_MAX_NEW_FILES=200` bulk-add guard |
| Merge conflict on task completion | Task comments | `merge-agent@odin` QUESTION comment with per-file hunks; branch preserved |
| Merge never attempted | Task metadata | `merge_status` still `pending` — did reflection pass (REVIEW → TESTING)? |
| Downstream task started before upstream merged | `COMPLETED_STATUSES` | REVIEW does NOT unblock downstream (fable task 214) — a dep in REVIEW means the branch is still pre-merge; only TESTING/DONE unblock dependents |
| Duplicate `odin exec` after worker restart | Stale recovery | `_recover_stale_executions` adopts the live pid; look for "adopt" in `taskit.log` |

## Quick Commands

```bash
# Active worktrees + which branch each is on
git worktree list
odin worktree status <spec_id>

# Confirm the manager rooted correctly (real git root, not a worktree)
cd taskit/taskit-backend && python -c "
from tasks.models import Board
b = Board.objects.get(id=<board_id>); print('working_dir:', b.working_dir)"

# Inspect task worktree/merge metadata
python testing_tools/task_inspect.py <task_id> --json --sections basic

# Reap orphan sandboxes / worktrees / snapshots
odin gc --prune

# See stuck merge locks
ls -la .odin/locks/merge-*.lock 2>/dev/null

# Follow the whole lifecycle live
tail -f taskit/taskit-backend/logs/taskit.log | grep -Ei 'worktree|merge|sandbox|EXECUTING|dispatch'
```

## Common Breakpoints

- `dag_executor.py :: poll_and_execute()` (~224) — concurrency gate, atomic transition, worktree-before-dispatch ordering
- `dag_executor.py :: _get_worktree_manager()` (~1609) — **the shadow-config guard**; verify root path
- `worktree.py :: create_task_worktree()` (~622) — branch name, base ref, `_git_config_lock`
- `microsandbox.py :: _execute_sync()` (~693) — VM boot, and the `finally` at ~782–793 that removes the sandbox
- `worktree.py :: _auto_commit_worktree()` (~713) — the three commit-guard layers
- `worktree.py :: merge_task_into_spec()` (~836) — file lock, merge workspace selection, conflict detection
- `merge_agent.py :: resolve_conflicts_in_worktree()` (~101) — per-file hunk capture before abort

## Gotchas

- **Root the WorktreeManager at `board.working_dir`, never at the task's resolved cwd.** The latter can be a worktree path; rooting there reads the worktree's *copied* `.odin/config.yaml` (the shadow) and creates nested worktrees. This is the single most common worktree pitfall.
- **Per-run sandbox cleanup ≠ orphan sweep.** `_remove_sandbox` in the `_execute_sync` finally is the per-run path and only fires if the process reaches the finally. A hard crash orphans the VM; the startup sweep and `odin gc --prune` are the safety net.
- **REVIEW does NOT count as a completed dependency.** `COMPLETED_STATUSES = {DONE, TESTING}` (fable task 214) — if a downstream task starts while its upstream is in REVIEW, that is a BUG, not by design. Dependents wait for TESTING (post-merge).
- **A worktree failure now FAILS the task by default.** The old "falls back to running in project root" behavior is gated behind `board.allow_project_root_execution`.
