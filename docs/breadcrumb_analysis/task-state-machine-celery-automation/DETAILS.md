# Task State Machine + Celery Automation — Detailed Trace

All paths relative to repo root. Backend paths under `taskit/taskit-backend/`.

## 1. Status field — no declarative transition matrix

**File**: `taskit/taskit-backend/tasks/models.py:126`
`TaskStatus(models.TextChoices)`: BACKLOG, TODO, IN_PROGRESS, EXECUTING, REVIEW, TESTING, DONE, FAILED. Default TODO (`models.py:181`).

There is NO `ALLOWED_TRANSITIONS` table. `Task.status` is a plain CharField — any ORM write can set any status. The lifecycle is enforced by side effects attached to specific code paths (views + celery tasks), which is exactly why raw-ORM lifecycle writes are banned (§12): an ORM write "succeeds" but silently skips history rows, reflection dispatch, merges, kanban repositioning, and schedule finalization.

Guards that DO exist:
- `_check_executing_mutation_lock` (`views.py:752`, fields at `views.py:99`): while EXECUTING, the update API rejects changes to `status`, `assignee_id`, `model_name` with 409 `task_executing_locked`. Stop first (`stop_execution`).
- Schedule guard (`views.py:2403`): schedule-controlled tasks can't be started before due time (409).
- Dependency add validation (`views.py:891`): new deps must be TODO or IN_PROGRESS.
- `execute_single_task` refuses to run unless status == EXECUTING and run_token matches (`dag_executor.py:176-186`).
- `_advance_task_to_testing` is a no-op unless status is still REVIEW (`dag_executor.py:745`).

## 2. Entry: how tasks reach IN_PROGRESS

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `TaskViewSet.update()` (`views.py:2397`)

- Validates via `UpdateTaskSerializer`; every changed field gets a `TaskHistory` row (`_record_change`, `views.py:806`).
- On →IN_PROGRESS: clears stop guards (`_clear_stop_guards`, `views.py:260`, called at `views.py:2490`), then fires the execution strategy if there's an assignee (`views.py:2506-2526`). No assignee = warning log, task sits in IN_PROGRESS forever.
- On →REVIEW (manual drag counts): `_trigger_auto_reflection` (`views.py:2531`).

Other producers of IN_PROGRESS:
- `consumers.py:307` `_auto_start_planned_tasks` — after planning completes, if `board.auto_start_planned_tasks` (`models.py:91`), every TODO task of the spec → IN_PROGRESS + strategy trigger (called from `consumers.py:413`).
- Reflection retry: `views.py:3319` (REVIEW → IN_PROGRESS, changed_by=`system@taskit`).
- Model escalation: `views.py:3011-3021` (would-be FAILED → IN_PROGRESS with upgraded model).
- Wave scripts (`docs/fable_roadmap/bootstrap/create_wave1.py`) seed tasks via ORM; dispatch itself must still be a status flip that the poller sees (ORM status flip + TaskHistory row is the documented minimum — see §12).

Strategy resolution: `tasks/execution/registry.py:18` `get_strategy()` reads `ODIN_EXECUTION_STRATEGY` — `local` → `LocalOdinStrategy`, `celery_dag` → `CeleryDAGStrategy` (no-op trigger, `celery_dag.py:24`), empty → None (nothing runs).

## 3. Celery Beat schedule

**File**: `taskit/taskit-backend/config/settings.py:184-199`, app at `config/celery.py`

| Beat entry | Task | Interval | Gated by |
|---|---|---|---|
| `dag-executor-poll` | `tasks.dag_executor.poll_and_execute` | `DAG_EXECUTOR_POLL_INTERVAL` (5s) | only registered when `ODIN_EXECUTION_STRATEGY=celery_dag` |
| `schedule-release-poll` | `tasks.schedule_executor.release_due_schedules` | `SCHEDULE_RELEASE_POLL_INTERVAL` (30s) | always |

Broker: filesystem broker by default (`USE_FILESYSTEM_BROKER=True`, dirs under `taskit-backend/.celery/`), Redis when disabled (`settings.py:162-182`). `DAG_EXECUTOR_MAX_CONCURRENCY` default **10** (`settings.py:195`).

Celery task inventory (`tasks/dag_executor.py`): `poll_and_execute` (:55), `execute_single_task` (:161), `summarize_single_task` (:349, no status change), `execute_reflection` (:547), `merge_task_on_reflection` (:624).

## 4. poll_and_execute

**File**: `tasks/dag_executor.py:55`
**Called by**: Celery Beat every 5s. **Calls**: `execute_single_task.delay`.

Key logic:
- `_recover_stale_executions()` first (`dag_executor.py:67`, body :411): EXECUTING tasks are FAILED when (a) recorded PID is dead, (b) no PID recorded and queued > `DAG_EXECUTOR_QUEUED_STALE_SECONDS` (120s), or (c) running > `DAG_EXECUTOR_TASK_TIMEOUT_SECONDS` (1800s) → failure_type `stale_execution` or `timeout`, comment posted. This is why a crashed worker's task flips to FAILED within ~2 min — and why an EXECUTING row you set via raw ORM (no active_execution metadata) gets auto-FAILED 120s later.
- Concurrency: slots = 10 − count(EXECUTING); IN_PROGRESS candidates FIFO by created_at; skipped without assignee (`dag_executor.py:88-91`).
- Dep gate: `check_deps` (`tasks/dependencies.py:29`) — BLOCKED if any dep FAILED, READY only when all deps in `COMPLETED_STATUSES = {DONE, TESTING}` (`dependencies.py:21`; odin's mirror copy `odin/src/odin/dependencies.py` must agree). REVIEW does NOT unblock dependents.
- Atomic claim (`dag_executor.py:104-127`): SELECT FOR UPDATE, re-check IN_PROGRESS, purge stop guards, write `metadata.active_execution = {strategy, run_token, queued_at, cancel_requested:false}`, status=EXECUTING, TaskHistory by `odin+dag-executor@system`.
- Worktree (`dag_executor.py:133-146`): if `task.spec.metadata.branch` exists, creates `.odin/worktrees/<spec>/<task_id>` via odin's `WorktreeManager` (imported in the worker, `dag_executor.py:846`), sets `metadata.working_dir/worktree_path/branch` and `merge_status=pending`.
- Post-dispatch, stores `celery_task_id` in active_execution (`dag_executor.py:150-160`).

## 5. execute_single_task

**File**: `tasks/dag_executor.py:161`
**Called by**: poll_and_execute. **Calls**: subprocess `odin exec <task_id>`; on REVIEW, `_trigger_auto_reflection`.

Key logic:
- Preconditions: task exists, status==EXECUTING, run_token matches (`:176-186`) — stale celery retries are dropped.
- Working dir: `resolve_working_dir` (`tasks/execution/utils.py:10`): task.metadata.working_dir > board.working_dir > spec.metadata (deprecated) > ODIN_WORKING_DIR (deprecated).
- Log: `taskit-backend/logs/spec_<spec_id>_task_<task_id>.log` (`:210`).
- `_run_subprocess_with_cancellation` (`:467`): Popen with `start_new_session=True`, env `ODIN_TASK_RUN_TOKEN`, records `active_execution.pid/started_at`; 1s poll loop checks deadline (1800s → SIGTERM), run_token mismatch (SIGKILL), `cancel_requested` (SIGTERM→5s→SIGKILL).
- After exit, re-reads task: if odin already moved status off EXECUTING, respect it and return (`:227-231`).
- Stop guard honored (`:239`): `ignore_execution_results` + `pending_stop_target` → route to the user's target status instead of FAILED, bump `stop_generation`.
- Fallback: exit 0 → REVIEW + `_trigger_auto_reflection` (`:248,319`); exit != 0 → FAILED with `_classify_failure` (`:944`) writing `last_failure_type/reason/origin` (origin=`taskit_dag_executor`) and a "Failed: … Debug: <log tail>" comment (`:328-345`). Failure types: `cancelled`, `timeout`, `internal_error`, `backend_auth_failure`, `agent_execution_failure`, plus `stale_execution` from recovery.
- Merge is intentionally NOT here — deferred to reflection pass (`:305` comment).

## 6. Inside odin exec (how odin reports back)

**File**: `odin/src/odin/orchestrator.py`

- Sets EXECUTING idempotently (`orchestrator.py:3243`), writes `metadata.started_at`.
- Runs its own dep check (`odin/src/odin/dependencies.py`) — divergence from taskit's copy causes silent exit-0 skips (documented in `spec-task-lifecycle/_INDEX.md`).
- Live observability: `output_file=.odin/logs/task_<id>.out`, `trace_file=.odin/logs/task_<id>.trace.jsonl` (`orchestrator.py:3268-3270`). tmux session `odin-<task_id>`; session name stored in `task.metadata.tmux_session` (`orchestrator.py:2913`, per `views.py:2034` docstring).
- Microsandbox mode (`odin/src/odin/harnesses/microsandbox.py:44`): the guest tees its trace to `/odin-trace.jsonl`, bind-mounted to the host trace path — the only channel that survives a VM kill (msb buffers and discards stdout on timeout-kill). Linked worktrees mount the worktree AND the main repo `.git` at identical host paths (F30 fix).
- Result: `task_mgr.record_execution_result` (`odin/src/odin/taskit/manager.py:218`) → `backends/taskit.py:509` → `POST /tasks/:id/execution_result/` (`taskit.py:522`) with status REVIEW on success / FAILED on failure (`orchestrator.py:3523`; early-failure path `:3470-3473`).

## 7. execution_result endpoint

**File**: `tasks/views.py:2900` `TaskViewSet.execution_result()`

Key logic:
- Stale-result guard (`views.py:2919-2927`): if `metadata.ignore_execution_results` and the incoming `taskit_run_token` doesn't prove it's a NEWER run, the result is discarded (200, no changes).
- `extract_agent_text` + `parse_envelope` (`tasks/execution_processing.py`) — envelope success overrides reported success.
- Writes metadata: `last_duration_ms`, `selected_model`, `last_failure_type/reason/origin`, accumulates `total_estimated_cost_usd`, stores `effective_input` (5k cap) and `full_output`; clears `active_execution` + stop guards when leaving EXECUTING (`views.py:3003-3006`).
- Model escalation (`views.py:3011`, helper `:563`): on failure→FAILED, if `board.escalation_enabled` and `board.model_escalation_priority` has a higher entry than the current model and `escalation_count < board.failure_max_retries` → reassign agent+model, rewrite status to IN_PROGRESS, re-trigger strategy (`views.py:3045-3048`). Skip reason recorded in `metadata.escalation_skip_reason`.
- → REVIEW triggers `_trigger_auto_reflection` (`views.py:3050`).

## 8. Auto-reflection

**File**: `tasks/views.py:269` `_trigger_auto_reflection`

- `task.skip_reflection or board.skip_reflection` → skips the audit entirely and dispatches merge+advance directly (`views.py:276-279`) — TESTING without any verdict.
- Duplicate guard: existing PENDING/RUNNING report → no-op (`views.py:281-290`).
- Reviewer defaults from board agents (`_reflection_reviewer_defaults`, `views.py:123`; preferred order gemini→codex→claude, `views.py:107`); no reviewer available → merge+advance directly (`views.py:293-299`).
- Creates ReflectionReport(PENDING, requested_by=`system@taskit`) → `execute_reflection.delay` (`views.py:301-315`).

`execute_reflection` (`dag_executor.py:547`): PENDING→RUNNING, subprocess `odin reflect <task_id> --report-id --model --agent` (timeout 1800s, log `logs/reflect_<task>_<report>.log`). Odin PATCHes the report itself; if the report is still RUNNING after the subprocess exits, fallback marks it COMPLETED (exit 0) or FAILED (`dag_executor.py:613-620`). A FAILED report does NOT advance the task — it stays in REVIEW (a stuck-in-REVIEW cause).

## 9. Verdict → status (the reflection loop)

**File**: `tasks/views.py:3219` `ReflectionReportViewSet.partial_update` (odin submits results here)

- COMPLETED + verdict_summary → posts `**Reflection: <VERDICT>**` comment with report attachment (`views.py:3242-3257`).
- PASS (`views.py:3259-3271`): only if task still REVIEW → `_merge_task_on_reflection_pass` (`views.py:319`) → no branch: `_advance_task_to_testing` directly; branch: `merge_task_on_reflection.delay`.
- NEEDS_WORK / FAIL (`views.py:3273-3338`): count = COMPLETED reports for the task (DB count, no metadata counter).
  - count >= 3 → FAILED + "Task failed after 3 reflection attempts" comment (`views.py:3287-3307`). Branch/worktree preserved for inspection.
  - count < 3 → `_maybe_reassign_on_quota_failure` (`views.py:489`; quota detection `:370`, keywords `:345`) then REVIEW → IN_PROGRESS + strategy trigger (`views.py:3317-3338`). Next execution injects the NEEDS_WORK feedback as prompt context (orchestrator side).
- Manual `POST /tasks/:id/reflect/` (`views.py:3154`) allows REVIEW/DONE/FAILED and takes `reviewer_agent`/`reviewer_model`; the resulting verdict flows through this same partial_update, so a manual reflect on a REVIEW task DOES drive the machine.

## 10. Merge gate and TESTING promotion

**File**: `tasks/dag_executor.py:624` `merge_task_on_reflection`

- No branch in metadata → `_advance_task_to_testing` straight away (`:640-643`). Already `merge_status=merged` → advance only (idempotent, `:645-648`).
- Merges `task/<spec>/<id>` into `spec/<spec_odin_id>` via odin `WorktreeManager.merge_task_into_spec` (`:875`; file-locked serialization — see `git-worktree-isolation/`).
- Outcome → `metadata.merge_status`: `merged` | `noop` | `conflict` | `error` (+ `merge_error`, `diff_stat`); comment posted either way (`:651-704`).
- ONLY on success/noop: `_advance_task_to_testing` (`:739`) — REVIEW → TESTING, changed_by `system@taskit`. On conflict/error the task STAYS in REVIEW for the operator; nothing retries the merge automatically.
- TESTING is what unblocks dependents (`dependencies.py:21`) — so a merge conflict also blocks the whole downstream DAG.

TESTING → DONE: three paths reach the terminal DONE state from TESTING, in order of operator involvement:

1. **Auto-promote (W4.2 — task #186).**  After `_advance_task_to_testing`
   succeeds, `dag_executor.py:1667-1692` dispatches the celery task
   `tasks.auto_promote.auto_promote_testing_task` (when
   `board.metadata["auto_promote_enabled"]` is truthy — the default).
   The body runs `odin promote-check <task_id>` (reusing the
   operator's gate CLI so the contract stays single-sourced) and, on
   RECOMMEND PROMOTE, applies the transition via
   `_apply_auto_promotion` (`auto_promote.py`): status update +
   `TaskHistory(changed_by="system+auto-promote@taskit")` + status
   comment linking the gate's `promotion_report` comment id +
   `_cleanup_done_task_worktree`.  HOLD leaves the task in TESTING
   (the gate's own report is the operator signal); a gate crash posts
   a single error comment and leaves the task alone.  The
   `system+auto-promote@taskit` identity is what makes the auto-flip
   distinguishable in `TaskHistory` from the operator's manual flip.
2. **Manual API flip (operator).**  `PATCH /tasks/:id/ {status: DONE}`
   is the pre-wave-4 path.  `views.py:3131-3143` fires
   `_cleanup_done_task_worktree` so API promotions also reclaim the
   worktree (the regression gap that W3.3 closed).
3. **Spec finalize (bulk).**  `SpecViewSet.finalize` (`views.py:3754`)
   runs `odin spec finalize` (PR creation, `views.py:3821`), then
   `_transition_spec_tasks_to_done(spec, pr_url)` (`views.py:3915` →
   `dag_executor.py:911`) moves every TESTING task of the spec to DONE
   with a "Finalized: PR <url>" comment.  Used after the operator
   merges the spec branch by hand.

## 11. Stop flow + signals

- `POST /tasks/:id/stop_execution/` (`views.py:2539`) — 409 unless EXECUTING. `_perform_stop_flow` (`views.py:1990`): (1) writes guard metadata FIRST (`ignore_execution_results`, `pending_stop_target`, `stopped_run_token`), (2) attempts kill — including `tmux kill-session -t odin-<task_id>` (`views.py:2034`) because the agent lives in a detached tmux session outside odin's process group, (3) applies or confirms the transition. The dag_executor honors the guard even if the kill raced (`dag_executor.py:239`). Consequence: after a stop, poll until the target status lands before redispatching (race documented as F16/task-101 in OPERATIONS.md).
- Signal: `tasks/signals.py:23` `auto_comment_on_status_transition` — post_save on TaskHistory; comments FAILED/DONE transitions unless the actor was `@odin.agent` or a rich failure comment already exists. This is the ONLY Django signal in the lifecycle; everything else is explicit view/celery code (stated design, `views.py:273`).

## 12. Rules of the system (operator/agent contract)

Enforced in code or learned the hard way (findings F11/F14/F16/F18 in `docs/fable_roadmap/OPERATIONS.md`):

1. **Never perform lifecycle transitions via raw ORM.** History rows, reflection dispatch, merge dispatch, kanban position, stop-guard clearing, and schedule finalization all hang off the API/celery paths. Raw `task.status = X; task.save()` skips all of them (F14: an ORM REVIEW→TESTING left `merge_status=pending` with no merge). ORM is for *seeding* only; the documented exception is the dispatch flip TODO/BACKLOG→IN_PROGRESS with a matching TaskHistory row (what the poller needs); everything downstream must be API/celery-driven. Never raw `curl` either — use `testing_tools/` scripts for reads (root `CLAUDE.md`).
2. **Never mutate `.odin/worktrees/*` by hand.** The reflection reviewer audits evidence integrity and will fail the task (F16). Fix the task *description* and retry instead. On FAILED, the worktree/branch is deliberately preserved for inspection — read-only.
3. **Never skip a state.** TODO → IN_PROGRESS is the only operator dispatch edge; IN_PROGRESS → EXECUTING belongs to the poller/odin; REVIEW → TESTING belongs to the merge task. Docstring contract: "Status lifecycle (never skip a step)" (`dag_executor.py:10-15`).
4. **A task without an assignee never executes.** The poller skips it silently (`dag_executor.py:88`).
5. **Dependents unblock only on DONE/TESTING** — REVIEW is not done; FAILED deps BLOCK (not skip) dependents (`dependencies.py:21-27`).
6. **While EXECUTING, don't edit status/assignee/model** — API 409s (`views.py:99,752`); use `stop_execution` first, then wait for the target status to land before redispatching.
7. **Don't fight the automation on REVIEW tasks**: the verdict drives merge+advance. To re-review, use `POST /tasks/:id/reflect/`, not a status write.
8. **Commit wave/brief files to the spec branch before dispatch** — task worktrees fork from the spec branch; uncommitted docs are invisible to agents (F4, bootstrap README).
9. **Don't tell agents to switch branches.** odin already isolates each task on `task/<spec>/<id>`; a manual branch switch strands the auto-commit where the merge never looks (F18).
10. **`ODIN_TASK_RUN_TOKEN` is the identity of a run.** Anything that reports without the current token is discarded (`views.py:2919`, `dag_executor.py:181`). Don't reuse or fake it.
