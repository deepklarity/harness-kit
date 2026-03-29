# Git Worktree Isolation — Debug Guide

## Log Locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Odin worktree ops | Odin's logger (`odin.worktree`) | Branch creation, worktree add/remove, merge operations, push results |
| Orchestrator | Odin's logger (`odin.orchestrator`) | Worktree creation decisions, merge results, spec finalization |
| DAG executor | `taskit/taskit-backend/logs/taskit.log` | Task state transitions, worktree creation triggers, merge timing |
| Odin exec | `.odin/logs/run_<run_id>.jsonl` | Per-task execution inside worktree |
| Git stderr | Captured in `_git()` helper, logged at debug level | Raw git errors, conflict details |
| Merge locks | `.odin/locks/merge-<spec_id>.lock` | Lock file presence indicates active or stuck merge |

## What to Search For

| Symptom | Where to look | Search term / command |
|---------|--------------|----------------------|
| Task running in project root instead of worktree | Task metadata | `task.metadata["worktree_status"] == "failed"` or no `worktree_path` key |
| Worktree not created | Task comments | Look for "Worktree creation failed" or "Git isolation unavailable" comment |
| Merge conflict on task completion | Task metadata | `task.metadata["merge_status"] == "conflict"` |
| Downstream task missing upstream work | Spec branch | `git log spec/<spec_id> --oneline` — verify upstream merge commit exists |
| Merge never attempted | Task metadata | `merge_status` still `"pending"` — check if reflection passed (REVIEW → TESTING) |
| Spec worktree stale after merge | `_spec` worktree | `cd .odin/worktrees/<spec>/_spec && git log -1` vs `git log spec/<spec_id> -1` |
| Merge lock stuck | `.odin/locks/` | Lock file age > 120s and no active merge process |
| Task branch already exists on create | Orchestrator logs | Not an error — reattachment is expected, worktree picks up existing commits |
| Agent can't find dependencies in worktree | Worktree symlinks | Check if `worktree_symlinks` config includes `node_modules`, `venv`, etc. |
| PR not created on finalize | CLI output / gh CLI | `gh pr list --head spec/<spec_id>` — check gh auth: `gh auth status` |
| Double worktree creation | Task metadata | Both DAG executor and orchestrator tried — check `dag_already_created` guard |
| Worktree works locally but not with remote | Git push logs | Push is best-effort; check `git remote -v` in project root |

## Quick Commands

```bash
# List all active worktrees
git worktree list

# Check worktree status for a spec (branch + merge status per task)
odin worktree status <spec_id>

# See which branch a worktree is on
git -C .odin/worktrees/<spec_id>/<task_id> branch --show-current

# See what's in the spec branch (all merged task work)
git log spec/<spec_id> --oneline --graph

# Check if a task branch has been merged into spec branch
git branch --merged spec/<spec_id> | grep task/<spec_id>/<task_id>

# See unmerged task branches for a spec
git branch --no-merged spec/<spec_id> | grep task/<spec_id>/

# Check merge status via Django ORM
cd taskit/taskit-backend && python -c "
from tasks.models import Task
for t in Task.objects.filter(spec__odin_id='<spec_id>'):
    print(f'{t.short_id}: {t.metadata.get(\"merge_status\", \"n/a\")} — {t.title[:50]}')"

# Inspect task metadata for worktree fields
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic

# Check spec metadata for branch and PR
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --json --sections basic

# Manually merge a conflicted task branch
cd .odin/worktrees/<spec_id>/_spec
git pull origin spec/<spec_id>
git merge task/<spec_id>/<task_id>
# resolve conflicts, then:
git add . && git commit
git push origin spec/<spec_id>

# Remove a stuck worktree
git worktree remove --force .odin/worktrees/<spec_id>/<task_id>

# Clean up all worktrees for a spec
odin worktree clean <spec_id>

# Clean up ALL worktrees
odin worktree clean --all

# Check for stuck merge locks
ls -la .odin/locks/merge-*.lock 2>/dev/null

# Remove a stuck lock (verify no active merge first)
rm .odin/locks/merge-<spec_id>.lock

# View the spec's PR (if finalized)
gh pr list --head spec/<spec_id>

# Diff between spec branch and main (what the PR contains)
git diff main...spec/<spec_id> --stat

# Verify spec worktree is in sync with spec branch
diff <(git -C .odin/worktrees/<spec_id>/_spec log -1 --format=%H) <(git rev-parse spec/<spec_id>)
```

## Config That Affects This Flow

Config file: `.odin/config.yaml` (or project-level odin config)

| Config key | Effect | Default |
|------------|--------|---------|
| `worktree.enabled` | Enables/disables worktree isolation globally | `true` |
| `worktree.base_branch` | Branch that spec branches fork from | `main` |
| `worktree.dir` | Where worktrees are created | `.odin/worktrees` |
| `worktree.post_hooks` | Commands to run in new worktrees (e.g. `npm install`) | `[]` |
| `worktree.symlinks` | Paths to symlink from project root into worktrees | `[]` |
| `worktree.auto_finalize` | Auto-create PR when spec completes | `true` |

Legacy top-level keys (`worktree_enabled`, `base_branch`, etc.) still supported but `worktree:` nested section is preferred.

## Common Breakpoints

- `worktree.py :: create_task_worktree()` — verify branch name, base ref, worktree path, reattachment vs new creation
- `worktree.py :: _merge_in_worktree()` — check merge result, conflict detection via `status --porcelain`
- `worktree.py :: _do_merge()` — verify merge workspace selection (_spec reuse vs temp worktree)
- `orchestrator.py :: exec_task()` — verify `dag_already_created` guard, worktree path assignment to `working_dir`
- `orchestrator.py :: _ensure_git_repo()` — verify lazy init succeeds, `.gitignore` content
- `views.py :: _merge_task_on_reflection_pass()` — verify merge triggers on REVIEW → TESTING (not earlier)
- `dag_executor.py :: poll_and_execute()` — verify worktree creation happens before execution dispatch

## Recovery Procedures

**Task worktree missing but branch exists:**
```bash
# Recreate worktree from existing branch
git worktree add .odin/worktrees/<spec_id>/<task_id> task/<spec_id>/<task_id>
```

**Spec branch accidentally deleted:**
```bash
# Recover from remote
git fetch origin
git checkout -b spec/<spec_id> origin/spec/<spec_id>
```

**All worktrees corrupted (e.g., after git gc or interrupted operation):**
```bash
git worktree prune
# Then re-run pending tasks — worktree creation is idempotent
```

**Merge produced wrong result (need to undo):**
```bash
git log spec/<spec_id> --oneline --merges -5
git -C .odin/worktrees/<spec_id>/_spec revert -m 1 <merge_commit_hash>
git -C .odin/worktrees/<spec_id>/_spec push origin spec/<spec_id>
```

**Spec worktree out of sync after manual merge:**
```bash
cd .odin/worktrees/<spec_id>/_spec
git reset --hard spec/<spec_id>
```

**Worktree path exists but .git is missing (stale directory):**
```bash
# The create functions handle this automatically (remove + recreate)
# But to fix manually:
git worktree remove --force .odin/worktrees/<spec_id>/<task_id>
rm -rf .odin/worktrees/<spec_id>/<task_id>
# Re-run task — worktree creation will recreate
```
