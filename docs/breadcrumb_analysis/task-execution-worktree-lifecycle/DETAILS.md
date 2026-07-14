# Task Execution + Worktree Lifecycle — Detailed Trace

Line numbers verified against the spec branch. Backend paths under `taskit/taskit-backend/`, CLI/orchestrator paths under `odin/`.

## 1. Dispatch

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `poll_and_execute()` — line ~224
Celery-Beat poll. Concurrency gate at ~247–255 (EXECUTING count vs `DAG_EXECUTOR_MAX_CONCURRENCY`), dependency check via `check_deps` at ~294, atomic `IN_PROGRESS → EXECUTING` block ~310–436, worktree created at ~335, then `execute_single_task.delay(task.id, run_token)` at ~436.

**Function**: `execute_single_task()` — line ~449
Builds `cmd = [cli_path, "exec", str(task.id)]` (~492) and runs it via `_run_subprocess_with_cancellation` (~505; defined ~1099, `subprocess.Popen` in a new session at ~1108, poll loop ~1133).

**File**: `odin/src/odin/cli.py`
**Function**: `exec` — line ~812 → `asyncio.run(orch.exec_task(full_id, mock=mock))` at ~848.

**File**: `odin/src/odin/orchestrator.py`
**Function**: `exec_task()` — line ~1341
The real per-task entrypoint inside the subprocess. Calls `_maybe_sweep_orphaned_sandboxes()` (~1360) at start.

**Dependency completion set** (merge-gate policy — REVIEW does NOT unblock):
`taskit/taskit-backend/tasks/dependencies.py:36` and `odin/src/odin/dependencies.py:35` — `COMPLETED_STATUSES = {DONE, TESTING}` (REVIEW excluded as of fable task 214). TESTING is the post-merge gate; only after the dep branch is folded into the spec branch can a dependent safely fork from it. `check_deps` lives at `dependencies.py:47` (backend) / `:38` (odin).

## 2. Worktree create

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `_create_task_worktree()` — line ~1631 → `_get_worktree_manager()` (~1609) → `create_task_worktree`.

**File**: `odin/src/odin/worktree.py`
**Function**: `WorktreeManager.create_task_worktree()` — line ~622
Branch name `task_branch = f"task/{spec_id}/{task_id}"` (~634); worktree at `.odin/worktrees/<spec>/<id>` (~635); `git worktree add -b <task_branch> <path> <base_ref>` runs under `_git_config_lock()` (~656–667) which serializes the `.git/config` write against parallel dispatch.

**Orchestrator path** (odin exec, when the DAG did not pre-create): `orchestrator.py :: exec_task` ~1493–1528 (`create_spec_branch` → `create_task_worktree`, then `working_dir = str(worktree_path)`).
Spec helpers: `worktree.py :: create_spec_branch` (~474), `create_spec_worktree` (~530).

## 3. Sandbox (microVM) boot + run-scoped cleanup

**File**: `odin/src/odin/harnesses/microsandbox.py`
**Class**: `MicrosandboxHarness` — line ~224; async `execute` — ~251.
**Function**: `_execute_sync()` — line ~693
Builds the `msb` command via `_build_msb_command()` (~616), assigns a per-run name from `_ephemeral_sandbox_name()` (~837) at ~717, then `subprocess.run(run_cmd, …)` (~764).
- **Run-scoped cleanup** — inner `finally` at ~782–793, specifically `_remove_sandbox(sandbox_name)` (~793; def ~1006, only ever removes `odin-msb-*` names, never raises).
- Outer `finally` at ~807–813 → `shutil.rmtree(tmpdir)` removes the per-run MCP-staging temp dir.

**Backstop (not per-run)**: `odin/src/odin/orchestrator.py :: _maybe_sweep_orphaned_sandboxes` (~209, called at ~1360) → `MicrosandboxHarness.sweep_startup_orphans` (~1035). Also `odin gc [--prune]` (`odin/src/odin/cli.py` ~2458).

Config fields: `odin/src/odin/models.py` (`microsandbox_*`), `odin/src/odin/config.py`.

## 4. Auto-commit

**File**: `odin/src/odin/worktree.py`
**Function**: `_auto_commit_worktree()` — line ~713
`git add -A` (~754), then Layer 1 generated/runtime reset (~759–765), Layer 2 pollution blocklist `venv`/`node_modules`/`site-packages`/`__pycache__` (~773–780), Layer 3 bulk-add guard `_AUTO_COMMIT_MAX_NEW_FILES = 200` (~782–800), commit at ~821. Returns `AutoCommitResult` (~154). Invoked as a safety net from `merge_task_into_spec` at ~859.

## 5. Merge

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `_merge_task_on_reflection_pass()` — line ~327 → `merge_task_on_reflection.delay()` (~348). Fires on REVIEW → TESTING (reflection passed).

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `merge_task_on_reflection()` — line ~1288 → `_merge_task_branch()` (~1651) → `WorktreeManager.merge_task_into_spec`.

**File**: `odin/src/odin/worktree.py`
**Function**: `merge_task_into_spec(spec_id, task_id, task_title="", attempt_resolution=False)` — line ~836
Auto-commits (~859), then acquires `_spec_lock()` (~342, a `filelock` on `.odin/locks/merge-<spec>.lock`, 120s) at ~861–863 → `_do_merge()` (~873) → `_merge_in_worktree()` (~921): `git merge --no-ff` at ~961, conflict detection via `--porcelain` UU/AA/DD at ~962–966.

**Merge-agent per-file conflict report**:
`odin/src/odin/merge_agent.py :: resolve_conflicts_in_worktree()` (~101) captures per-file `conflict_hunks` (~134–151) before abort; `format_merge_question()` (~473) renders the report, posted as a QUESTION comment (author `merge-agent@odin`) from `dag_executor.merge_task_on_reflection` at ~1384–1404. Carrier fields `MergeResult.conflict_hunks` / `file_resolutions` declared at `worktree.py` ~147 / ~151. `MergeResult` (dataclass ~131–151) also carries `conflicting_files`, `resolved_files`, `ambiguous_files`, `needs_human`.

Post-merge: `_advance_task_to_testing()` (`dag_executor.py` ~1478, sets TESTING at ~1493).

## 6. Cleanup

**File**: `odin/src/odin/worktree.py`
- `cleanup_task_worktree()` — line ~1046: `git worktree remove --force` + `git branch -D` (merged → branch safe to drop).
- `remove_task_worktree()` — line ~1061: removes worktree, **preserves** branch (task failure → recovery).
- `finalize_spec()` — line ~1076: removes all worktrees for a spec, keeps branches for the PR.
- `cleanup_all()` — line ~1091 (`odin worktree clean --all` → `cli.py` ~1954).

## 7. The shadow `.odin/config` guard

**Primary**: `taskit/taskit-backend/tasks/dag_executor.py :: _get_worktree_manager()` — line ~1609–1628. Roots the manager at `board.working_dir`, explicitly not at `resolve_working_dir(task)` (which "may return a task-level worktree path"). Rooting inside a worktree would load the worktree's copied `.odin/config.yaml` (the shadow) and nest `.odin/worktrees`.

**Companion**: `odin/src/odin/orchestrator.py :: exec_task()` — line ~1493–1499. The `dag_already_created` check skips a second worktree creation "to avoid nested worktree inside the cwd that the DAG executor set to the worktree path."

**Why the shadow exists / stays out of commits** (`worktree.py`): configs copied into each worktree via `_copy_agent_configs()` (~364, using `AGENT_CONFIG_PATHS` ~26–30); then `_WORKTREE_LOCAL_PATHS = (".env", ".odin")` (~35), `_write_worktree_gitignore()` (~407), and the auto-commit Layer-1 reset (~759–765) keep the worktree's own `.odin` off the task branch.

## Dispatch guardrails (why a worktree failure ≠ silent root run)

`dag_executor.py`: Default-First auto-assign `_auto_assign_from_suggested` (~98), `dispatch_blocked_reason` stamping, and the `no_worktree_no_optin` → FAILED gate (~355–406). A task with no worktree and no `board.allow_project_root_execution` is FAILED, not run in the root. Infra-class auto-redispatch: `_maybe_auto_redispatch_infra_failure` (~912). Stale-execution recovery / orphan pid adoption: `_recover_stale_executions` (~763).

## Cross-references
- Branch model, merge timing, lock semantics, config keys: `../git-worktree-isolation/`
- Dispatch gating, dependency waves, subprocess cancellation: `../spec-task-lifecycle/02-execute-and-dispatch/`
- State machine (all 8 statuses, merge-gated TESTING): `../task-state-machine-celery-automation/`
