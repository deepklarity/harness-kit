# Git Worktree Isolation — Detailed Trace

Maps the worktree lifecycle to exact files, functions, and integration points.

## 1. WorktreeManager Module

**File**: `odin/src/odin/worktree.py`
**Class**: `WorktreeManager`

Constructor:
```python
WorktreeManager(project_root: Path, worktree_dir: str = ".odin/worktrees")
```
- Validates `.git` exists; raises `ValueError` if not
- Sets `self.worktree_base` (base dir for all worktrees) and `self.lock_dir` (`.odin/locks`)

Internal helpers:
- `_git(*args, cwd)` — wraps `subprocess.run`, 120s timeout, debug logging
- `_branch_exists(branch)` — `git rev-parse --verify refs/heads/{branch}`
- `_spec_lock(spec_id)` — returns `FileLock(.odin/locks/merge-{spec_id}.lock, timeout=120)`

Data class:
```python
@dataclass
class MergeResult:
    success: bool
    conflict: bool = False
    noop: bool = False
    error: Optional[str] = None
    diff_stat: Optional[str] = None
```

Public API:
```python
create_spec_branch(spec_id, base_branch="main") -> str
create_spec_worktree(spec_id, post_hooks=None, symlinks=None) -> Path
update_spec_worktree(spec_id) -> None
create_task_worktree(spec_id, task_id, post_hooks=None, symlinks=None) -> Path
get_worktree_path(spec_id, task_id) -> Path
merge_task_into_spec(spec_id, task_id, task_title="") -> MergeResult
cleanup_task_worktree(spec_id, task_id) -> None      # removes worktree + deletes branch
remove_task_worktree(spec_id, task_id) -> None        # removes worktree, preserves branch
finalize_spec(spec_id) -> None                        # removes all worktrees for a spec
cleanup_all() -> None                                 # removes everything + git worktree prune
create_spec_pr(spec_id, title, task_summaries=None) -> Optional[str]
```

---

## 2. Configuration

**File**: `odin/src/odin/models.py` :: `OdinConfig`

```python
worktree_enabled: bool = True                          # default ON (Default First)
base_branch: str = "main"
worktree_dir: str = ".odin/worktrees"
worktree_post_hooks: List[str] = []                    # e.g. ["npm install", "pip install -e ."]
worktree_symlinks: List[str] = []                      # e.g. ["node_modules", ".env", "venv"]
auto_finalize: bool = True
```

**File**: `odin/src/odin/config.py` :: config loading

Supports two YAML formats (legacy top-level keys and new nested section):
```yaml
# New format (preferred)
worktree:
  enabled: true
  base_branch: main
  dir: .odin/worktrees
  post_hooks: ["npm install"]
  symlinks: [node_modules, .env]
  auto_finalize: true

# Legacy format (still supported)
worktree_enabled: true
base_branch: main
worktree_dir: .odin/worktrees
worktree_post_hooks: ["npm install"]
worktree_symlinks: [node_modules, .env]
auto_finalize: true
```

Fallback chain: nested key → legacy top-level key → default value.

---

## 3. Orchestrator Initialization

**File**: `odin/src/odin/orchestrator.py` :: `Orchestrator.__init__()`
**Called by**: Any `odin` CLI command that instantiates Orchestrator

Key logic:
- Computes `project_root = Path(task_storage).resolve().parent.parent`
- Creates `WorktreeManager(project_root, worktree_dir=config.worktree_dir)`
- If init fails (not a git repo): stores `_worktree = None`, `_worktree_disabled_reason = str(exc)`
- Failure never blocks orchestrator creation

**File**: `odin/src/odin/orchestrator.py` :: `_ensure_git_repo()`
**Called by**: `exec_task()` when `_worktree is None` but `worktree_enabled is True`

Lazy auto-init flow:
1. If `.git` appeared since init (another process created it) → retry `WorktreeManager` creation
2. Else → `git init -b main`, create `.gitignore` (worktrees, locks, logs, costs, .env), `git add -A`, `git commit -m "Initial commit (odin auto-init)"`
3. Create `WorktreeManager` from newly-init'd repo
4. On failure → sets `_worktree_disabled_reason`, returns False

---

## 4. Spec Branch + Spec Worktree Creation

**File**: `odin/src/odin/orchestrator.py` :: `plan_spec()` (approx line 411)
**Called by**: `odin plan` → `_plan_decompose_impl()`

```python
if self._worktree:
    branch = self._worktree.create_spec_branch(sid, base_branch=self.config.base_branch)
    spec_archive.metadata["branch"] = branch
    self._save_spec(spec_archive)

    spec_wt_path = self._worktree.create_spec_worktree(
        sid, post_hooks=self.config.worktree_post_hooks,
        symlinks=self.config.worktree_symlinks,
    )
    spec_archive.metadata["worktree_path"] = str(spec_wt_path)
    self._save_spec(spec_archive)
```

`create_spec_branch()` internals:
1. Return early if `spec/<spec_id>` already exists (idempotent)
2. `git fetch origin/<base_branch>` (best-effort)
3. Handle empty repo: create initial empty commit
4. Find base ref: `origin/{base}` → local `{base}` → `HEAD`
5. `git branch spec/<spec_id> <base_ref>`
6. `git push origin spec/<spec_id>` (best-effort)

`create_spec_worktree()` internals:
1. Path: `.odin/worktrees/<spec_id>/_spec` (underscore prevents collision with numeric task IDs)
2. Return early if path exists with `.git` (idempotent)
3. Recover from stale state: if path exists without `.git` → `git worktree remove --force` + `shutil.rmtree`
4. `git worktree add <path> spec/<spec_id>`
5. Create symlinks from project root (best-effort)
6. Run post-hooks (best-effort)

Both operations wrapped in try/except. Failures logged, never block spec planning.

---

## 5. Task Worktree Creation (two entry points)

### Entry A: DAG Executor

**File**: `taskit/taskit-backend/tasks/dag_executor.py` :: `poll_and_execute()`
**Called by**: Celery polling loop

Condition: `task.spec and task.spec.metadata.get("branch")`

```python
_create_task_worktree(task)
  → _get_worktree_manager(task)  # WorktreeManager(board.working_dir)
  → wt.create_task_worktree(spec_odin_id, task_id)
  → stores: working_dir, worktree_path, branch="task/{spec}/{task}", merge_status="pending"
```

On failure: exception logged, task still transitions to EXECUTING, continues without worktree.

### Entry B: Orchestrator

**File**: `odin/src/odin/orchestrator.py` :: `exec_task()` (approx line 1003)
**Called by**: `odin exec <task_id>` or DAG executor subprocess

Guard against double creation:
```python
dag_already_created = task.metadata.get("worktree_path") or task.metadata.get("branch")
if task.spec_id and not mock and not dag_already_created and (self._worktree or self.config.worktree_enabled):
```

If guard passes:
1. `_ensure_git_repo()` if `_worktree is None`
2. `create_spec_branch()` (idempotent)
3. `create_task_worktree(spec_id, task_id, post_hooks, symlinks)`
4. Set `working_dir = str(worktree_path)`
5. Store `branch` and `worktree_path` in task metadata
6. Post task comment: "Git isolation active — working in branch `task/...`"

On failure: task runs in project root, metadata records `worktree_status=failed`, `worktree_error=str(exc)`, comment explains fallback.

When worktree disabled entirely: comment "Git isolation unavailable: {reason}" with recovery instructions.

### `create_task_worktree()` internals

1. Path: `.odin/worktrees/<spec_id>/<task_id>`
2. Return early if path exists with `.git` (idempotent)
3. `git fetch origin/spec/<spec_id>` (best-effort, to get latest)
4. Prefer `origin/spec/<spec_id>` as base ref; fallback to local `spec/<spec_id>`
5. If task branch already exists → `git worktree add <path> task/<spec_id>/<task_id>` (reattach, no `-b`)
6. If task branch is new → `git worktree add -b task/<spec_id>/<task_id> <path> <base_ref>`
7. Create symlinks (best-effort)
8. Run post-hooks (best-effort)

Branch reattachment: if task branch survives from a previous run (worktree removed but branch preserved), new worktree picks up all previous commits.

---

## 6. Merge on Reflection Pass

**Dispatch**: `taskit/taskit-backend/tasks/views.py` :: `_merge_task_on_reflection_pass(task)`
**Execution**: `taskit/taskit-backend/tasks/dag_executor.py` :: `merge_task_on_reflection(task_id)` (Celery task)
**Called by**: Task status transition REVIEW → TESTING (reflection passed)

NOT called when: REVIEW → FAILED (3 strikes, no merge), or execution fails (no reflection).

**Why Celery?** The merge runs in the Celery worker, not the Django web process. The Django process cannot import `odin.worktree` (different Python environment). The Celery worker has odin available because it runs task execution.

```python
# views.py — thin dispatcher (runs in Django web process)
def _merge_task_on_reflection_pass(task):
    if not task.metadata.get("branch"):          # no worktree → skip
        return
    if task.metadata.get("merge_status") == "merged":  # idempotent
        return
    merge_task_on_reflection.delay(task.id)       # dispatch to Celery

# dag_executor.py — actual merge (runs in Celery worker)
@shared_task
def merge_task_on_reflection(task_id):
    result = _merge_task_branch(task)             # calls WorktreeManager
    task.metadata["merge_status"] = "merged" | "noop" | "conflict" | "error"
    if result.diff_stat:
        task.metadata["diff_stat"] = ...

    # Post a TaskComment with merge outcome (visible in task timeline)
    # - success: "Merged `task/...` into `spec/...`" + diff_stat
    # - noop: "No changes to merge"
    # - conflict/error: error details
    TaskComment.objects.create(task=task, comment_type=STATUS_UPDATE, ...)

    if result.success:
        wt.update_spec_worktree(spec.odin_id)    # sync _spec worktree
```

### `merge_task_into_spec()` internals

1. Verify task branch exists; early return if not
2. Acquire file lock (`.odin/locks/merge-<spec_id>.lock`, 120s timeout)
3. Determine merge workspace:
   - **If `_spec` worktree exists**: reuse it (spec branch already checked out)
   - **Else**: create temp worktree at `.odin/worktrees/_merge/spec_<spec_id>` (cleaned up in finally block)
4. In the workspace:
   - `git fetch origin spec/<spec_id>` + `git pull`
   - `git merge --no-ff task/<spec_id>/<task_id> -m "Merge task <task_id>: <title>"`
   - On conflict: check `git status --porcelain` for UU/AA/DD markers → `git merge --abort` → return `MergeResult(conflict=True)`
   - On success: `git push origin spec/<spec_id>` (best-effort)
5. Release lock

`update_spec_worktree()`: runs `git reset --hard spec/<spec_id>` in the `_spec` worktree. Best-effort (logs warning, never raises). Ensures subsequent tasks see merged work when they fork from the spec branch.

---

## 7. Post-Execution Auto-Commit & Deferred Merge (Orchestrator Path)

**File**: `odin/src/odin/orchestrator.py` :: `exec_task()` (approx line 1074)
**Called by**: After `_execute_task()` returns

Only if `worktree_path` was created AND `task.spec_id` exists:

```python
if result.get("success"):
    # Auto-commit uncommitted work to the task branch
    self._worktree._auto_commit_worktree(spec_id, task_id, task_title)
    task.metadata["merge_status"] = "deferred"
    # comment: "Work committed — merge deferred until reflection passes"
else:
    task.metadata["merge_status"] = "pending"
    # comment: "Branch preserved (not merged)"
```

The orchestrator does NOT merge. Merge is deferred to `views.py:_merge_task_on_reflection_pass()` which fires on the REVIEW → TESTING transition (reflection passed). This ensures that if reflection loops back with NEEDS_WORK and the agent re-executes, the merge captures the final reflection-approved code, not the first attempt.

---

## 8. Spec Finalization

**File**: `odin/src/odin/orchestrator.py` :: `finalize_spec(spec_id)` (approx line 308)
**Called by**: `odin spec finalize <spec_id>` or auto-finalize

1. `self._worktree.finalize_spec(spec_id)` — removes all worktrees for the spec
2. Gather task summaries: `"#{id} — {title} ({status})"` for each task
3. `self._worktree.create_spec_pr(spec_id, title, task_summaries)`
   - Uses `gh pr create --base main --head spec/<spec_id>`
   - Returns PR URL or None
4. If PR created: stores `finalized_at` and `pr_url` in spec metadata

**File**: `odin/src/odin/cli.py` :: `_spec_finalize()`
- Wraps orchestrator with Rich UX (progress spinner, colored output, link formatting)
- Exits with code 1 if PR creation fails

---

## 9. Cleanup Operations

Two cleanup strategies:
- `cleanup_task_worktree(spec_id, task_id)` — removes worktree AND deletes branch (complete cleanup)
- `remove_task_worktree(spec_id, task_id)` — removes worktree, preserves branch (failure recovery)

When to use which:
- Task succeeded + merged → `cleanup_task_worktree` (branch merged, safe to delete)
- Task failed → `remove_task_worktree` (branch has committed work, may be recovered)
- Spec finalized → `finalize_spec` (removes all worktrees for spec)

`finalize_spec()` internals:
- Iterates spec directory, calls `git worktree remove --force` for each entry
- Removes spec directory if empty
- Does NOT delete branches (they're needed for the PR)

`cleanup_all()`:
- Delegates to `finalize_spec` per spec directory
- Runs `git worktree prune` to clean stale refs

---

## 10. CLI Commands

**File**: `odin/src/odin/cli.py`

`odin init` (line ~261):
- Creates git repo if `.git` doesn't exist
- Creates `.gitignore` with odin internals (worktrees, locks, logs, costs, .env)
- Initial commit

`odin worktree list`:
- Runs `git worktree list` and prints output

`odin worktree status <spec_id>`:
- Shows table: Task | Title | Branch | Merge Status
- Merge status colored: green (merged), red (conflict/error), yellow (pending)

`odin worktree clean <spec_id>`:
- Calls `wt.finalize_spec(resolved)` — removes worktrees

`odin worktree clean --all`:
- Calls `wt.cleanup_all()` — removes everything

`odin spec finalize <spec_id>`:
- Creates PR via orchestrator, shows PR link

---

## 11. Task/Spec Metadata Fields

Task metadata (stored in `task.metadata`):
```
branch          = "task/<spec_id>/<task_id>"
worktree_path   = "/abs/path/to/.odin/worktrees/<spec_id>/<task_id>"
working_dir     = same as worktree_path (used by DAG executor)
worktree_status = "failed"              (only if creation failed)
worktree_error  = "error message"       (only if creation failed)
merge_status    = "pending" | "merged" | "conflict" | "error"
merge_error     = "error message"       (only if merge failed)
```

Spec metadata (stored in `spec_archive.metadata`):
```
branch          = "spec/<spec_id>"
worktree_path   = "/abs/path/to/.odin/worktrees/<spec_id>/_spec"
pr_url          = "https://github.com/org/repo/pull/123"
finalized_at    = "2026-03-18T10:00:00Z"
```

---

## 12. Design Patterns

**Idempotency**: Every create operation checks existence first. Safe to retry any operation.

**Best-effort secondary ops**: Remote push, symlinks, post-hooks — all wrapped in try/except, logged, never fatal. Primary operation (branch/worktree creation) succeeds even if secondary ops fail.

**Graceful degradation**: Worktree failure never blocks task execution. The system falls back to running in the project root with metadata recording the failure.

**Serialized merges**: File lock per spec (120s timeout) prevents concurrent push races from parallel tasks. Lock is acquired before any merge operation and released after push.

**Branch reattachment**: If a task branch exists from a previous run (worktree was removed but branch preserved), the new worktree reattaches to the existing branch with all its commits.

**Merge workspace selection**: Prefers reusing the `_spec` worktree (already on spec branch). Falls back to temporary merge worktree for older specs without `_spec`. Temp worktree always cleaned up in finally block.

**Deferred merge**: Merge happens after reflection passes (REVIEW → TESTING), not immediately after execution. This ensures reflection-driven re-executions are captured in the merged work before it reaches the spec branch.
