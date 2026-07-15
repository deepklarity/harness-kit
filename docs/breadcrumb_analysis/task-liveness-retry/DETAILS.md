# Task Liveness & Retry — Detailed Trace

## 1. Dispatch gates

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `poll_and_execute()` (line 292)
**Called by**: Celery Beat every `DAG_EXECUTOR_POLL_INTERVAL` (5s, `config/settings.py:207`)
**Calls**: `reconcile_task_runs()`, `_auto_assign_from_suggested()`, `check_deps()`, `_create_task_worktree_with_retry()`, `_rotate_leftover_trace_files_for_task()` (W12.7), `task_runs.start_run()`, `execute_single_task.delay()`

Five gates, each in order. A task that fails a gate is stamped `metadata.dispatch_blocked_reason` and skipped (`continue`); the reason is operator-visible so a stuck poll is never silent.

- **Concurrency** (line 323): `EXECUTING` count vs `DAG_EXECUTOR_MAX_CONCURRENCY`. No slot → return for this cycle.
- **Assignee** (line 369, F43 Default-First): no assignee but `metadata.suggested_agent` set → auto-assign from the active lineup. Still none → `no_assignee`.
- **Deps** (line 380): `check_deps()` must return `READY`. `WAITING`→`deps_not_complete`, `BLOCKED`→`deps_blocked_failed`.
- **Memory budget** (line 397): `sandbox_budget.spawn_fits(vm_mem, budget_remaining)`. Over budget → `memory_budget_full` and `continue` (not `break` — a later smaller spawn could fit). Budget from `SANDBOX_MEMORY_BUDGET_MIB` env; unset/undetectable → unbounded (gate skipped).
- **Worktree** (line 438): `_create_task_worktree_with_retry()` needs a spec branch. No worktree + `board.allow_project_root_execution=False` → task flipped **FAILED** with `last_failure_type=missing_worktree` (line 465). Opt-in → runs in project root, stamped `project_root_execution_used`.

After all five gates pass, between stamping `EXECUTING` and `task_runs.start_run`, **dispatch-time trace rotation** runs (W12.7, line 537-549). See § 1a below.

Data out: `run_token = uuid4().hex`; `metadata.active_execution = {strategy, run_token, queued_at, cancel_requested, mem_mib}` (line 424). `metadata.stopped_run_token`/`ignore_execution_results` are popped so a fresh attempt isn't poisoned by a prior stop.

---

## 1a. Dispatch-time trace rotation (W12.7, the structural half)

**File**: `taskit/taskit-backend/tasks/dag_executor.py` (caller, line 539) and `taskit/taskit-backend/tasks/session_resolver.py` (helper, line 76)
**Function**: `_rotate_leftover_trace_files_for_task(task)` (session_resolver.py:76)
**Called by**: `poll_and_execute` immediately after stamping `EXECUTING` and BEFORE `task_runs.start_run` (line 537-549)
**Returns**: `List[Path]` of backup paths created (also logged as `[task:N] Rotated N leftover trace file(s) before run: [...]`).

Each attempt of a task writes to the same on-disk file (`{working_dir}/.odin/logs/task_<id>.trace.jsonl` or its `.out` fallback — § 5). A retry that does not rotate the predecessor's file inherits the predecessor's mtime; the progress scanner (§ 4b) then judges the new run by an hours-old file and reaps it at birth. Rotation moves any leftover `task_<id>.trace.jsonl` and `task_<id>.out` aside with a `.<epoch>.bak` suffix (preserving mtime + content for forensics), so the new `odin exec` writes its first line into an empty slot.

Filename list: `_TASK_TRACE_FILENAMES` (session_resolver.py:42) — covers both the JSONL trace and the `.out` fallback. Worktree-local files are not rotated: the worktree is recreated at every dispatch and any prior file vanishes with it. Collision logic at session_resolver.py:124-129 — if two dispatches land in the same second, a `.1`, `.2`, … counter is appended (bounded at 10) so neither backup clobbers the other.

The call site (dag_executor.py:537-549) wraps the rotation in `try/except` — a filesystem fault downgrades to `Leftover-trace rotation failed; continuing dispatch` and the run still proceeds. The age-vs-run-start guard in `_run_progress_mtime` (§ 4b) catches a poisoned file defensively if rotation ever fails. **Bookkeeping never kills the run** (`docs/patterns/bookkeeping-never-kills-the-run.md`).

---

## 2. The run-token fence

**File**: `tasks/dag_executor.py`
**Functions**: `poll_and_execute` mints it (line 414); `task_runs.start_run` (line 555); `execute_single_task` checks it (line 584); `_run_subprocess_with_cancellation` env-injects it and supersession-kills on it.

`run_token` is a per-attempt uuid. It is the **fencing anchor**: exactly one RUNNING `TaskRun` row exists per task at a time (`start_run` expires every prior open run, `task_runs.py:36`). A write whose token doesn't match is rejected at the API boundary (`views.execution_result`). It flows to the subprocess via `ODIN_TASK_RUN_TOKEN` env, so the agent's own execution_result post is bound to the attempt that spawned it.

Supersession contract: when a redispatch mints a new token, the old run's `execute_single_task` loop sees `active_execution.run_token != run_token` on its next 1s poll and SIGKILLs its own process group (`run_token_mismatch`). This is why F48 put the cancellation/token/liveness checks **before** the deadline check — old ordering mislabelled superseded runs as "timeout".

---

## 3. The subprocess + heartbeat loop

**File**: `tasks/dag_executor.py`
**Function**: `_run_subprocess_with_cancellation()` (line 1877)
**Called by**: `execute_single_task` (call site line 639, function starts line 584)
**Calls**: `task_runs.heartbeat_run`, `_terminate_process`, `SleepAwareDeadline`

One poll loop does three jobs (one mechanism, not two — `task_runs.py` module docstring):

1. **Reap exit** — `proc.wait(timeout=1.0)` returns the real exit code.
2. **Heartbeat** — every `TASK_RUN_HEARTBEAT_INTERVAL_SECONDS` (10s, dag_executor.py:70), touch `TaskRun.last_heartbeat` (`heartbeat_run`, `task_runs.py:51`). Throttled so a 30-min run doesn't write every second.
3. **Control** — run_token supersession → SIGKILL; `cancel_requested` → SIGTERM; `SleepAwareDeadline.expired()` → SIGTERM (`timeout`).

`SleepAwareDeadline` excludes host-sleep time from the budget. `caffeinate_assertion()` holds the host awake on macOS. Before any deadline kill, `proc.poll()` re-verifies liveness — a process that exited at the boundary is reaped with its real exit code, never mislabelled "timeout".

Data in: `cmd=[odin, exec, task_id]`, `working_dir`, `run_token`. Data out: `(exit_code, failure_stage)` where failure_stage ∈ {none, odin_non_zero_exit, run_token_mismatch, cancelled, timeout, spawn_exception}.

---

## 4. Liveness — the three reconciler checks

**File**: `tasks/dag_executor.py`
**Function**: `reconcile_task_runs()` (line 1385)
**Called by**: Beat every `TASK_RUN_RECONCILE_INTERVAL_SECONDS` (20s, `config/settings.py:220`) **and** `worker_ready` boot hook (`config/celery.py:15`). Also called inline at the top of every `poll_and_execute` so a dispatch cycle never starts while an orphan lingers.

Three detectors, newest-signal-wins, run in this order so a run is reaped **once** by the most specific check:

### 4a. Lease check — dead supervisor
`_reap_expired_task_run_leases()` (line 1075). A RUNNING `TaskRun` whose `last_heartbeat` is older than `TASK_RUN_LEASE_SECONDS` (180s). The heartbeat-writing loop is gone → almost always a celery worker crash/restart. Heartbeat-driven, so it fires within one lease window regardless of execution strategy. This is the **primary crash detector** (task #211).

### 4b. Progress check — live supervisor, dead agent
`_reap_stalled_progress_runs()` (line 1153). A RUNNING run whose **trace file mtime** is older than `TASK_RUN_PROGRESS_WINDOW_SECONDS` (600s). The supervising process is up and heartbeating (so 4a passes) but the agent inside the sandbox stopped producing output — host sleep / hung agent. Trace mtime comes from `_run_progress_mtime` (line 1098) via `session_resolver._task_trace_path` (line 145). Only fires when a trace file exists; a traceless run falls back to 4a. This is the **zombie detector** (task #235).

**W12.7 hardening of 4b — two halves.** Without protection, the progress check could reap a fresh retry whose trace belongs to a previous attempt (see DEBUG.md worked example). Two halves, both tested in `tests/test_dag_executor_zombie_inherit_f354.py`:

- **Dispatch-time rotation (§ 1a)** moves the predecessor's trace aside before `task_runs.start_run` so the new run starts with an empty slot at the canonical path. The new `odin exec` writes its first line there; the progress check sees only this run's file. Structural fix — eliminates the poisoned-file class by construction.
- **Age-vs-run-start guard in `_run_progress_mtime`** (line 1147-1149) returns `None` when `trace_mtime < run.started_at - 2.0`. The 2-second grace absorbs the dispatch→first-write race. Defensive fix — catches a poisoned file if rotation fails (filesystem fault) or a legacy path writes one. 2s vs the 600s progress window leaves real zombie detection untouched; the guard only catches the poisoned-retry signature (mtime older than the run).

Either half alone leaves a hole. Together they make stale-trace reap unreachable in the normal flow, and the guard catches the abnormal flow.

### 4c. Error-loop check — live agent writing only errors
`_reap_error_loop_runs()` (line 1324). Only examines runs with a **fresh** trace (stale-trace runs were already reaped by 4b). Reads the trace tail; if `>= TASK_RUN_ERROR_LOOP_THRESHOLD` (0.8) of the last `TASK_RUN_ERROR_LOOP_WINDOW` (50) lines share one error signature → looping. Catches a CLI retrying a failing provider call forever (the trace keeps growing, so 4b sees fresh writes and passes). Stamps a provider-specific `next_retry_after`. This is the **error-loop detector** (task #262).

### 4d. Legacy metadata checks — adopt-or-fail
`_recover_stale_executions()` (line 1423). Keyed to `Task.metadata.active_execution` (not TaskRun) — still needed for tasks whose lease hasn't expired (supervisor alive) but are stuck past their own deadline. The **adopt-if-alive** rule: a recorded pid that is alive **and** whose cmdline is `odin exec <task_id>` (`_pid_cmdline_matches`, line 894) is a surviving orphan from a worker restart — **adopted**, kept EXECUTING, never re-fired (avoids two `odin exec` sharing one worktree). A dead or recycled pid → FAILED+requeue.

| Check | Signal | Catches | Env var (default) |
|-------|--------|---------|-------------------|
| 4a lease | `TaskRun.last_heartbeat` | dead supervisor (worker crash) | `TASK_RUN_LEASE_SECONDS` (180) |
| 4b progress | trace file mtime | live supervisor, dead agent (sleep/hang) | `TASK_RUN_PROGRESS_WINDOW_SECONDS` (600) |
| 4c error-loop | trace tail signatures | CLI in a provider retry loop | `TASK_RUN_ERROR_LOOP_THRESHOLD` (0.8), `_WINDOW` (50), `_MIN_LINES` (10) |
| 4d legacy | pid liveness + cmdline + queued_at/started_at | dead/recycled pid, queue stall, overall timeout | `DAG_EXECUTOR_QUEUED_STALE_SECONDS` (3000), `DAG_EXECUTOR_TASK_TIMEOUT_SECONDS` (3000) |

---

## 5. Trace-path resolution (the shared definition of "the trace")

**File**: `tasks/session_resolver.py`
**Function**: `_task_trace_path()` (line 145)
**Called by**: `_run_progress_mtime` (dag_executor.py:1129), `_resolve_run_trace_path` (dag_executor.py:1195), `resolve_session` (line 184)

Resolution order: (1) `metadata["trace_file"]` (odin records the absolute path at start), (2) `{working_dir}/.odin/logs/task_<id>.trace.jsonl` then `.out`, (3) the worktree-local `.odin/worktrees/<spec>/<task>/.odin/logs/task_<id>.trace.jsonl`.

**The path is keyed to `task_id`, NOT to the run/attempt.** Every retry of the same task resolves the **same** on-disk filename. The trace is overwritten-in-place by the next run's first write. This is correct for the "what is the current session?" UI question, but it is the root cause of the stale-trace reap bug — a dead run's leftover trace file is the exact path the progress check stats for the next run. W12.7 fix: dispatch-time rotation (§ 1a) moves the predecessor's file aside before the next run starts; the age-vs-run-start guard in `_run_progress_mtime` (§ 4b) is the defensive backstop.

The companion helper `_log_dir_for_task` (session_resolver.py:61) resolves the per-task log dir from `resolve_working_dir(task)`; `_stat` (session_resolver.py:68) is the shared `path.stat()` wrapper used by both `_task_trace_path` and `_run_progress_mtime`.

---

## 6. Reap — the shared kill tail

**File**: `tasks/dag_executor.py`
**Functions**: `_reap_task_run()` (line 1023) → `_fail_stale_execution()` (line 915)

Every detector funnels through `_reap_task_run` (task #211: "fewer loops, one owner"):

1. Verify the pid before killing — `_pid_is_alive` + `_pid_cmdline_matches` (line 894). Don't trust `kill(pid,0)` alone; a recycled pid would adopt a foreign process.
2. `_terminate_pid(force=True)` the verified-live pid.
3. `_sweep_orphaned_sandboxes()` (line 1000) — best-effort microVM removal, never raises (bookkeeping never kills the run).
4. `task_runs.finish_run(run_token, state=KILLED|EXPIRED)` — idempotent; a row already terminal is left alone.
5. If the task already moved on (not EXECUTING) → just close the run row, no requeue.
6. Else `_fail_stale_execution`: EXECUTING→FAILED, pop `active_execution`, stamp `last_failure_type/reason/origin`, `tag_failure_class`, write history + status_update comment, `record_execution_mistake` (ledger, idempotent per run_token).

`_fail_stale_execution` then hands off to the policy layer: `apply_failure_policy(task)`; if that didn't act, the legacy `_maybe_auto_redispatch_infra_failure` fallback.

---

## 7. Failure classes and routing policy

**File**: `tasks/failure_policy.py`
**Functions**: `DEFAULT_POLICY_TABLE` (line 116), `resolve_policy()` (line 258), `apply_failure_policy()` (line 548)

Every FAILED task is tagged `metadata.failure_class` (by `failure_tagger`) and looked up in `DEFAULT_POLICY_TABLE`. Three actions:

| Action | Classes | Behaviour |
|--------|---------|-----------|
| `AUTO_REQUEUE` | stale_execution, truncation, silent_hang, transport_error, lock_race, sandbox_unavailable, error_loop | retry same assignee+model, bounded by `max_retries` (2 for most) |
| `REASSIGN` | quota_exhaustion | audit comment only; real reassign runs at reflection time (`views._maybe_reassign_on_quota_failure`) with the `harness_usage_status` ground-truth check |
| `HUMAN` | env_missing, timeout, worktree_isolation, disk_exhaustion, crash, cancelled, model_unavailable, unknown | audit comment, leave FAILED |

Override layers (Default-First): `settings.DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES` → per-board `routing_policy['failure_actions']` (board wins, live-editable in the web settings UI, no restart). `resolve_policy` only accepts the three sanctioned actions.

Fingerprint advice (W6.3): if the mistake-ledger history overwhelmingly says retry never works, `AUTO_REQUEUE` is downgraded to `HUMAN` for this occurrence (history quoted in the comment). If history says retry always works, the backoff is skipped.

---

## 8. The redispatch

**File**: `tasks/dag_executor.py`
**Function**: `_maybe_auto_redispatch_infra_failure()` (line 1543)
**Called by**: `failure_policy._auto_requeue` (failure_policy.py:891) and the `_fail_stale_execution` legacy fallback
**Calls**: `_record_rework_continuity`, `strategy.trigger` (line 1759)

Two paths converge: policy-aware (passes a `FailurePolicy`) and legacy type-based (`_is_infra_failure`, line 1520). Both increment `metadata.auto_redispatch_count` and append to `auto_redispatch_history` (capped at 10) inside one `select_for_update` transaction, flip FAILED→IN_PROGRESS, then:

- `_record_rework_continuity` (F45) — preserves agent+model so the audit trail shows the retry was intentional.
- A `status_update` comment naming the class, attempt `counter/cap`, and reason.
- `strategy.trigger()` — re-dispatch. If no strategy configured, the task sits IN_PROGRESS for the next `poll_and_execute`.

At cap (`counter >= cap`): stamp `policy_cap_reached` (+ legacy alias `auto_redispatch_cap_reached`) once, leave FAILED for human. For `peer_fallback` classes (stale_execution, truncation, etc.), cap-exhaustion routes to one **same-tier routing peer** (`_reassign_to_routing_peer`, failure_policy.py:679 — never a tier jump), then human. The peer hop is bounded by `routing_peer_reassigned` so a broken peer can't ping-pong.

Returns `True` if redispatched (FAILED→IN_PROGRESS), `False` otherwise. Idempotent: re-checks status and counter inside the lock.

Each new dispatch re-enters `poll_and_execute` from the top, where W12.7 dispatch-time rotation (§ 1a) moves any leftover trace aside before the new run starts — the retry starts clean, not in the predecessor's shadow.