# Task Liveness & Retry

Trigger: an `IN_PROGRESS` task is picked by the Celery Beat poller (every 5s) and dispatched into a sandboxed `odin exec` run; a separate 20s reconciler watches every live run and decides whether it is alive, dead, or looping.
End state: the task reaches REVIEW (success) or FAILED; FAILED tasks of an auto-requeue class are retried up to a cap, then held for a human.

This traces the **dispatch → liveness → death & retry** spine. It is the operator's answer to "why did my run die / get killed / loop forever?" For the surrounding status machine (which edges are operator vs celery) see `task-state-machine-celery-automation/`; for worktree/sandbox boot detail see `task-execution-worktree-lifecycle/`.

## Flow

```
config/settings.py :: CELERY_BEAT_SCHEDULE["poll-and-execute"]  (every 5s)
  → tasks/dag_executor.py :: poll_and_execute()
    → reconcile_task_runs() first (inline pre-dispatch sweep)
    → gate 1 — concurrency: EXECUTING count < DAG_EXECUTOR_MAX_CONCURRENCY
    → gate 2 — assignee: _auto_assign_from_suggested() (Default-First) else skip("no_assignee")
    → gate 3 — deps: check_deps() == READY else skip("deps_*")
    → gate 4 — memory budget: sandbox_budget.spawn_fits() else skip("memory_budget_full")
    → gate 5 — worktree: _create_task_worktree_with_retry(); no worktree + no opt-in → FAILED("missing_worktree")
    → mint run_token (uuid4); stamp metadata.active_execution{run_token, queued_at, mem_mib}
    → Task IN_PROGRESS → EXECUTING
    → W12.7 rotation: _rotate_leftover_trace_files_for_task() — moves any predecessor's
        task_<id>.trace.jsonl + .out aside with .<epoch>.bak suffix (structural fix for
        stale-trace reap; see DEBUG.md)
    → task_runs.start_run() (expires any prior open run)
    → execute_single_task.delay(task_id, run_token)

tasks/dag_executor.py :: execute_single_task(task_id, run_token)            [Celery worker]
  → run_token fence: mismatch with active_execution.run_token → skip
  → _run_subprocess_with_cancellation() — `odin exec <task_id>` in a new session
    → record active_execution.pid + started_at; task_runs.heartbeat_run(pid)
    → loop (poll proc every 1s):
        piggyback heartbeat every 10s (task_runs.heartbeat_run)
        run_token superseded?  → SIGKILL process group, return run_token_mismatch
        cancel_requested?      → SIGTERM, return cancelled
        SleepAwareDeadline expired + proc still alive? → SIGTERM, return timeout
    → return (exit_code, failure_stage)

config/settings.py :: CELERY_BEAT_SCHEDULE["task-run-reconciler"]  (every 20s)
  + config/celery.py :: worker_ready signal (boot sweep)
  → tasks/dag_executor.py :: reconcile_task_runs()  — three liveness checks, newest-signal-wins:
    1. _reap_expired_task_run_leases()   — heartbeat stale past TASK_RUN_LEASE_SECONDS (~180s): dead supervisor
    2. _reap_stalled_progress_runs()     — trace mtime idle past TASK_RUN_PROGRESS_WINDOW_SECONDS (~600s): live supervisor, dead agent
                                           (W12.7 hardened: _run_progress_mtime returns None when
                                           trace mtime predates run.started_at by > 2s, so a fresh
                                           retry cannot be judged by a predecessor's file)
    3. _reap_error_loop_runs()           — trace tail dominated by one error signature (>= threshold): live agent writing only errors
    4. _recover_stale_executions()       — legacy metadata checks: dead/recycled pid, queued-past-deadline, overall timeout (adopts verified-live orphans)

    [any detector fires]
    → _reap_task_run(run): verify+kill pid (_pid_cmdline_matches), sweep orphan sandboxes, task_runs.finish_run(KILLED|EXPIRED)
    → _fail_stale_execution(task): EXECUTING→FAILED, stamp last_failure_*, tag_failure_class, record_execution_mistake
    → failure_policy.apply_failure_policy(task): dispatch on failure_class
        AUTO_REQUEUE  → _maybe_auto_redispatch_infra_failure() (counter++ ; cap policy.max_retries)
        REASSIGN      → audit comment (real reassign runs at reflection time)
        HUMAN         → audit comment, leave FAILED

[mock] _maybe_auto_redispatch_infra_failure under cap:
  → FAILED → IN_PROGRESS (one transaction, select_for_update); bump auto_redispatch_count; append history
  → _record_rework_continuity() (preserve agent+model); status_update comment naming the class + attempt
  → strategy.trigger() → re-dispatch (same assignee, same model)
  → back to poll_and_execute top → rotation step moves the (now-stale) prior trace aside
  at cap → stamp policy_cap_reached, leave FAILED for human
  peer_fallback → _reassign_to_routing_peer() (one same-tier hop, then human)
```

## What survives a retry

| Artifact | Survives? | Why |
|----------|-----------|-----|
| Task row (status, metadata, history) | yes | the unit of work |
| Worktree (`.odin/worktrees/<spec>/<task>`) | depends on dispatch path | Executor retries (this flow) **rebuild** the worktree fresh from the spec branch each attempt — committed work survives on the task branch, uncommitted work is lost. See `taskit/taskit-backend/tasks/dag_executor.py::_create_task_worktree` (removes any stale worktree with no RUNNING TaskRun, then creates fresh). Direct `odin exec` runs (outside this executor) **reuse** an existing worktree instead — see `odin/src/odin/orchestrator.py:1897`. Merged only on REVIEW→TESTING either way. |
| `active_execution` metadata blob | replaced | new run_token supersedes; old fields popped |
| TaskRun row | one per attempt | prior open run expired-by-supersession at `start_run` |
| Trace file `.odin/logs/task_<id>.trace.jsonl` | replaced each attempt | keyed by **task id**, not by run — W12.7 rotates the predecessor's file to `.<epoch>.bak` at each dispatch so the new run starts with an empty slot. Without rotation, the new run would inherit the old mtime and the progress check would reap it at birth (see DEBUG.md worked example). |

## Sub-flows

- See `task-execution-worktree-lifecycle/` for worktree creation, microVM boot, and the shadow-`.odin/config` trap.
- See `task-state-machine-celery-automation/` for the full 8-status machine, edge ownership, and the <60s fast-first-checks runbook.
- See `spec-task-lifecycle/02-execute-and-dispatch/` for per-hop dispatch detail.