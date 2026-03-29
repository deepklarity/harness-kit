# Git Worktree Isolation

Trigger: `odin plan <spec>` (spec branch + spec worktree) / `odin exec <task_id>` or DAG executor (task worktree creation + merge on reflection pass)
End state: Each spec = one PR to main. Each task = branch forked from spec branch, auto-merged after reflection passes.

## Branch Model

```
main
 └── spec/<spec_id>                     ← created at plan time, forked from main (or configured base_branch)
      ├── task/<spec_id>/<task_id_1>    ← created when task execution starts, forked from spec branch
      ├── task/<spec_id>/<task_id_2>    ← parallel tasks get independent branches
      └── task/<spec_id>/<task_id_3>    ← depends on task_1; forked AFTER task_1 merges into spec branch
```

Key invariant: a task branch is always forked from the spec branch AFTER all its dependencies have merged. The task worktree includes the work of all upstream tasks.

## Worktree Layout

```
<project_root>/
.odin/worktrees/
├── <spec_id>/
│   ├── _spec/                          ← spec-level worktree (checked out on spec branch)
│   ├── <task_id_1>/                    ← task worktree
│   ├── <task_id_2>/                    ← parallel task worktree
│   └── <task_id_3>/                    ← downstream task (after task_1 merged)
.odin/locks/
└── merge-<spec_id>.lock                ← file lock serializing merges within a spec
```

The `_spec` worktree serves two purposes: (1) "Open in editor" link for the spec branch code, and (2) reused as the merge workspace (spec branch is already checked out there).

## Flow 1: Spec Planning (branch + spec worktree setup)

```
CLI :: odin plan <spec_file>
  → orchestrator.plan_spec()
  → creates spec archive in .odin/specs/<spec_id>.json

  worktree.WorktreeManager :: create_spec_branch(spec_id, base_branch)
    → idempotent: returns early if spec/<spec_id> already exists
    → fetch origin/<base_branch> (best-effort)
    → fallback chain for base ref: origin/<base_branch> → local <base_branch> → HEAD
    → handles empty repo: creates initial empty commit if needed
    → git branch spec/<spec_id> <base_ref>
    → push to remote (best-effort, non-fatal on failure)
    → stores branch in spec_archive.metadata["branch"]

  worktree.WorktreeManager :: create_spec_worktree(spec_id, post_hooks, symlinks)
    → creates .odin/worktrees/<spec_id>/_spec on the spec branch
    → runs post-hooks and creates symlinks (best-effort)
    → stores path in spec_archive.metadata["worktree_path"]

  → decomposes spec into tasks (existing DAG creation)
  → creates tasks on board with spec_id
```

Both branch and spec worktree creation are best-effort. Failures are logged but never block spec planning.

## Flow 2: Task Execution (worktree creation)

Two possible entry points: DAG executor (Celery) or orchestrator (odin exec). Both create the task worktree, but the orchestrator checks if the DAG executor already did it (prevents double creation).

```
[Path A: DAG executor]
dag_executor.py :: poll_and_execute()
  → finds ready task (deps satisfied, agent assigned)
  → transitions task to EXECUTING
  → checks spec.metadata["branch"] exists
  → _create_task_worktree(task)
    → WorktreeManager(board.working_dir).create_task_worktree(spec_id, task_id)
  → stores in task.metadata: working_dir, worktree_path, branch, merge_status="pending"
  → subprocess: odin exec <task_id>

[Path B: orchestrator (odin exec)]
orchestrator.py :: exec_task()
  → checks dag_already_created: skips if task.metadata has worktree_path or branch
  → if no worktree yet and worktree_enabled:
    → _ensure_git_repo() — lazy auto-init if .git doesn't exist
    → create_spec_branch() (idempotent)
    → create_task_worktree(spec_id, task_id, post_hooks, symlinks)
  → sets working_dir to worktree path
  → agent runs in worktree; commits go to task/<spec_id>/<task_id>
```

On worktree creation failure in either path: task continues in project root with `worktree_status=failed` in metadata. A task comment explains the fallback.

## Flow 3: Task Completion (merge deferred to reflection pass)

```
agent execution completes
  → task transitions to REVIEW (existing)
  → reflection triggered automatically

  [reflection passes — REVIEW → TESTING]
  views.py :: _merge_task_on_reflection_pass(task)
    → skips if no branch in metadata
    → skips if already merged (merge_status == "merged")
    → _merge_task_branch(task)
      → WorktreeManager.merge_task_into_spec(spec_id, task_id, task_title)

  [Inside merge_task_into_spec]
    → acquires file lock (.odin/locks/merge-<spec_id>.lock, 120s timeout)
    → determines merge workspace:
      [if _spec worktree exists] → reuse it (spec branch already checked out)
      [else] → create temp worktree at .odin/worktrees/_merge/spec_<spec_id>
    → fetch + pull latest spec branch
    → git merge --no-ff task/<spec_id>/<task_id> -m "Merge task <task_id>: <title>"
    → push updated spec branch to remote (best-effort)
    → returns MergeResult(success, conflict, error)

  [on merge success]
    → task.metadata["merge_status"] = "merged"
    → update_spec_worktree() — git reset --hard to sync _spec worktree
    → task comment: "Merged branch `task/...` into `spec/...`"
    → DAG executor re-evaluates: downstream tasks fork from UPDATED spec branch

  [on merge conflict]
    → task.metadata["merge_status"] = "conflict"
    → git merge --abort (automatic, no corrupt state)
    → task branch preserved for manual resolution
    → task comment: "Merge conflict — branch preserved for manual resolution"

  [on merge error]
    → task.metadata["merge_status"] = "error"
    → task comment with error details

  [reflection fails — 3 strikes]
    → REVIEW → FAILED, no merge attempted
    → worktree and branch preserved for inspection

  [execution fails — no reflection]
    → task.metadata["merge_status"] stays "pending"
    → worktree preserved, branch preserved
    → downstream tasks blocked (existing DAG behavior)
```

Key nuance: merge happens on REVIEW → TESTING (after reflection passes), NOT immediately after execution. This ensures reflection-driven re-executions are included in the merged work.

## Flow 4: Spec Finalization (PR to main)

```
All tasks in spec reach terminal state
  → manual: odin spec finalize <spec_id>
  → or: auto_finalize config (if enabled)

orchestrator.finalize_spec(spec_id)
  → worktree.finalize_spec(spec_id) — removes all worktrees for the spec
  → worktree.create_spec_pr(spec_id, title, task_summaries)
    → gh pr create --base main --head spec/<spec_id> --title ... --body ...
    → returns PR URL (or None if gh not installed/authenticated)
  → stores pr_url and finalized_at in spec metadata

CLI: odin spec finalize <spec_id>
  → wraps orchestrator.finalize_spec() with UX (progress spinner, colored output)
```

## Flow 5: Parallel Task Execution (same wave)

```
DAG executor identifies wave: task_2, task_3, task_4 all ready

  For each task in wave (concurrent):
    create_task_worktree(task_N)
      → each gets independent worktree from spec/<spec_id>
      → each gets independent branch: task/<spec_id>/<task_N_id>

  Tasks execute in parallel in separate worktrees

  On completion (sequential merges via file lock):
    → first-finished acquires lock, merges, releases
    → second-finished acquires lock, pulls updated spec, merges, releases
    → if merge conflict: merge_status="conflict", branch preserved
```

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| Worktree creation fails | Task runs in project root, metadata records failure, task comment explains fallback |
| Task execution fails | Worktree + branch preserved for inspection, merge never attempted, downstream blocked |
| Merge conflict on reflection pass | `merge --abort` called, branch preserved, merge_status="conflict", manual resolution needed |
| Merge error (non-conflict) | Branch preserved, merge_status="error", error details in metadata + comment |
| Spec abandoned | `odin worktree clean <spec_id>` removes worktrees, branches preserved |
| Agent crashes mid-task | Worktree + branch intact, any committed work survives for re-execution |
| Concurrent merge race | File lock serializes merges per-spec (120s timeout), prevents push races |
| Lock timeout | Returns MergeResult(success=False, error="lock timeout"), no corrupt state |
| Task branch exists from previous run | Worktree reattaches to existing branch, preserving previous commits |
| No git repo in project | Lazy auto-init: `_ensure_git_repo()` creates repo + .gitignore + initial commit |
| gh CLI not installed | PR creation returns None gracefully, spec still finalized |
| Remote unavailable | All operations work local-only, push failures are non-fatal warnings |
