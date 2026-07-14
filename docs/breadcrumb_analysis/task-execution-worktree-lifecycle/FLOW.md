# Task Execution + Worktree Lifecycle

Trigger: a READY task is picked up by the DAG executor poll (or `odin exec <task_id>` is run directly).
End state: the agent's work is committed on `task/<spec>/<id>`, merged into `spec/<spec>` after reflection passes, and the worktree removed.

This breadcrumb is the **end-to-end spine**: dispatch → worktree create → sandbox (microVM) boot → auto-commit → merge → cleanup. For the branch/merge model in depth see `../git-worktree-isolation/`; for dispatch-gating and dependency logic see `../spec-task-lifecycle/02-execute-and-dispatch/`. This doc focuses on the glue and the two pitfalls operators keep re-discovering: the **shadow `.odin/config`** and the **run-scoped sandbox cleanup**.

## The spine

```
dag_executor.poll_and_execute()          [Celery-Beat, every DAG_EXECUTOR_POLL_INTERVAL]
  → concurrency gate (EXECUTING count < DAG_EXECUTOR_MAX_CONCURRENCY, default 10)
  → check_deps(task)  — READY when every dep is in COMPLETED_STATUSES {DONE, TESTING} (REVIEW excluded — fable task 214)
  → atomic IN_PROGRESS → EXECUTING transition (row-locked)
  → _create_task_worktree(task)          [worktree BEFORE dispatch]
  → execute_single_task.delay(task.id, run_token)
        │
        ▼
  execute_single_task()                   [Celery worker]
  → cmd = [odin, exec, <task_id>]
  → _run_subprocess_with_cancellation()   [Popen in new session, pid recorded]
        │
        ▼
  odin exec <task_id>  →  orchestrator.exec_task()
  → _maybe_sweep_orphaned_sandboxes()     [backstop for crashed prior runs]
  → dag_already_created?  yes → reuse worktree (do NOT create a nested one)
  → harness.execute(prompt, context)      [MicrosandboxHarness: boot microVM, run, tear down]
  → agent writes files inside the worktree
  → auto-commit to task/<spec>/<id>
  → task → REVIEW, auto-reflection fires
        │
        ▼  (reflection PASS)
  _merge_task_on_reflection_pass()  →  merge_task_on_reflection.delay()
  → WorktreeManager.merge_task_into_spec()  [file-locked on .odin/locks/merge-<spec>.lock]
  → _advance_task_to_testing()  (REVIEW → TESTING)
        │
        ▼
  cleanup_task_worktree()  (worktree removed, branch dropped once merged)
```

## Worktree layout

```
<project_root>/                          ← real git root (board.working_dir)
.odin/worktrees/<spec>/<task_id>/        ← per-task worktree, on task/<spec>/<task_id>
.odin/worktrees/<spec>/_spec/            ← spec worktree, reused as merge workspace
.odin/locks/merge-<spec>.lock            ← file lock serializing merges within a spec
.git/config                              ← guarded by _git_config_lock during parallel `worktree add`
```

## Sandbox (microVM) execution + run-scoped cleanup

Tasks execute inside a **microsandbox** microVM (libkrun — HVF on macOS, KVM on Linux) via the `msb` CLI, not directly on the host.

```
MicrosandboxHarness.execute()
  → _execute_sync()
      → sandbox_name = _ephemeral_sandbox_name()   [one deterministic name per run]
      → msb command built by _build_msb_command()
      → subprocess.run(run_cmd, …)                  [boot + run the agent]
      finally:                                       ◀── RUN-SCOPED CLEANUP
        → _remove_sandbox(sandbox_name)             [only ever touches odin-msb-* names]
      finally:
        → shutil.rmtree(tmpdir)                      [per-run MCP-staging temp dir]
```

The inner `finally` guarantees the microVM is torn down **per run**, even on failure — this is the run-scoped sandbox cleanup. A separate startup sweep (`_maybe_sweep_orphaned_sandboxes` → `sweep_startup_orphans`, and `odin gc --prune`) is the backstop for VMs orphaned by a crashed process; it is NOT the per-run path.

## The shadow `.odin/config` pitfall

Each worktree gets a **copy** of `.odin/config.yaml` (so agent CLIs inside the worktree find their settings). That copy is a trap: if any manager roots itself *inside* a worktree, it reads the worktree's copied config (the "shadow") and creates nested `.odin/worktrees`.

Two guards prevent it:
- **DAG executor**: `_get_worktree_manager()` roots the `WorktreeManager` at `board.working_dir` (the real git root), explicitly **not** at `resolve_working_dir(task)` (which may be a task worktree path).
- **Orchestrator**: `exec_task()`'s `dag_already_created` check skips a second worktree creation to avoid nesting a worktree inside the cwd the DAG executor already set to the worktree path.

The shadow copy is kept out of commits by three mechanisms in `worktree.py`: `_WORKTREE_LOCAL_PATHS = (".env", ".odin")`, a per-worktree `.gitignore` (`_write_worktree_gitignore`), and the auto-commit Layer-1 reset that unstages `.odin`. So the worktree's own `.odin` (shadow config + its logs/locks) never lands on the task branch.

## Auto-commit defence-in-depth

`_auto_commit_worktree()` does `git add -A` then three guards before committing:
1. **Layer 1** — reset generated/runtime paths (unstages `.odin`, etc.).
2. **Layer 2** — pollution blocklist: `venv`, `node_modules`, `site-packages`, `__pycache__`.
3. **Layer 3** — bulk-add guard `_AUTO_COMMIT_MAX_NEW_FILES = 200` (a huge new-file count aborts rather than committing junk).

## Failure modes

| Scenario | Behavior |
|----------|----------|
| Worktree creation fails, no opt-in | Task marked **FAILED** (`no_worktree_no_optin` gate) — it does **not** silently run in the project root unless `board.allow_project_root_execution` is set |
| microVM boot fails | `_execute_sync` finally still runs `_remove_sandbox`; failure surfaced through the harness result |
| Process crashes mid-run | Per-run cleanup skipped → startup sweep / `odin gc --prune` reaps the orphan sandbox later |
| Merge conflict | `merge --abort`; if `attempt_resolution`, merge-agent posts a per-file conflict QUESTION comment (author `merge-agent@odin`); branch preserved |
| Task fails execution | `remove_task_worktree` keeps the branch for recovery; downstream blocked |
| Worker restart mid-execution | Stale-execution recovery adopts the live pid instead of double-firing (`_recover_stale_executions`) |
