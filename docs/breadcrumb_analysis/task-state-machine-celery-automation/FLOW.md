# Task State Machine + Celery Automation

Trigger: an operator/agent moves a board task to IN_PROGRESS (UI drag, API PATCH, wave dispatch script, `auto_start_planned_tasks`, or a celery retry loop)
End state: task lands in DONE / FAILED / TESTING with a reflection verdict, and every automatic hop in between is attributable to a specific celery task or view

Why this breadcrumb exists (decision rationale): `spec-task-lifecycle/` already traces dispatch (02) and reflection (03) hop-by-hop, and this flow overlaps it. A new breadcrumb was created instead of extending it because the center of gravity is different: this doc is the *whole* state machine in one place (all 8 statuses, who is allowed to drive each edge, what celery does behind your back) plus a fast-first-checks operator runbook — the past failure mode was operators violating system rules, not missing per-hop detail. Per-hop detail stays in `spec-task-lifecycle/`; corrections to it (concurrency default, merge-before-TESTING) are noted here rather than duplicated. See also: `spec-task-lifecycle/`, `git-worktree-isolation/`.

## The state machine

```
BACKLOG → TODO → IN_PROGRESS → EXECUTING → REVIEW → TESTING → DONE
 (operator) (operator/auto)  (celery)   (odin/celery) (celery, post-merge) (finalize)
                    ↑                        │
                    │  NEEDS_WORK/FAIL, <3 completed reflections (celery)
                    └────────────────────────┘
                                             │  NEEDS_WORK/FAIL, >=3 (celery)
                                             ↓
                                          FAILED   (also: exit!=0, stale recovery, timeout)
```

Statuses: `tasks/models.py:126` — BACKLOG, TODO, IN_PROGRESS, EXECUTING, REVIEW, TESTING, DONE, FAILED. There is no transition-matrix validator in the model; the rules are enforced by *which code path* performs each edge (see DETAILS.md §1 and §12).

## Who drives which edge

| Edge | Driver | Code |
|------|--------|------|
| BACKLOG/TODO → IN_PROGRESS | Operator (UI/API), wave script, or `auto_start_planned_tasks` | `views.py:2397` (update), `consumers.py:307` |
| IN_PROGRESS → EXECUTING | Celery Beat `poll_and_execute` (celery_dag) or odin itself (local) | `dag_executor.py:119`, `orchestrator.py:3243` |
| EXECUTING → REVIEW | odin via `execution_result` POST, or dag_executor fallback on exit 0 | `views.py:2900`, `dag_executor.py:248` |
| EXECUTING → FAILED | dag_executor fallback (exit != 0, timeout, stale recovery) or odin | `dag_executor.py:253,411` |
| EXECUTING → (stop target) | Operator via `stop_execution`; dag_executor honors the guard | `views.py:2539,1990`, `dag_executor.py:239` |
| REVIEW → TESTING | Celery `merge_task_on_reflection` AFTER merge succeeds — never direct | `dag_executor.py:624,739` |
| REVIEW → IN_PROGRESS | Celery, on NEEDS_WORK/FAIL verdict with <3 completed reflections | `views.py:3319` |
| REVIEW → FAILED | Celery, 3 completed reflections without PASS | `views.py:3289` |
| FAILED → IN_PROGRESS | Automatic model escalation (board setting) | `views.py:3011,563` |
| TESTING → DONE (auto) | W4.2: `_advance_task_to_testing` dispatches `auto_promote_testing_task` which runs `odin promote-check` and, on RECOMMEND PROMOTE, applies the transition with `changed_by=system+auto-promote@taskit`. Board opt-out via `board.metadata["auto_promote_enabled"]=false`. | `dag_executor.py:1667-1692`, `auto_promote.py` |
| TESTING → DONE (manual) | Operator via PATCH `/tasks/:id/ {status: DONE}` (the pre-wave-4 path; the auto path runs in parallel) | `views.py:3131-3143` |
| TESTING → DONE (finalize) | `odin spec finalize` (PR created) → bulk transition of all spec tasks | `views.py:3915`, `dag_executor.py:911` |

## Flow (celery_dag strategy, the autonomous path)

```
[operator] PATCH /tasks/:id/ {status: IN_PROGRESS}
views.py :: TaskViewSet.update()  (views.py:2397)
  → clears stale stop guards (views.py:2490)
  → execution/registry.py :: get_strategy() (registry.py:18)
  → celery_dag.py :: trigger() — NO-OP, waits for poll (celery_dag.py:24)

[every 5s, Celery Beat]  (settings.py:186)
dag_executor.py :: poll_and_execute()  (dag_executor.py:55)
  → _recover_stale_executions() — dead-PID/timeout EXECUTING tasks → FAILED (dag_executor.py:411)
  → slots = DAG_EXECUTOR_MAX_CONCURRENCY(10) - count(EXECUTING) (settings.py:195)
  → dependencies.py :: check_deps() — READY only if all deps in {DONE, TESTING} (dependencies.py:21)
  → SELECT FOR UPDATE, status=EXECUTING, metadata.active_execution={run_token,...} (dag_executor.py:104-127)
  → creates task worktree if spec has a branch (dag_executor.py:133-146)
  → execute_single_task.delay(task_id, run_token)

dag_executor.py :: execute_single_task()  (dag_executor.py:161)
  → subprocess: odin exec <task_id>, cwd=resolve_working_dir(task),
    log → taskit-backend/logs/spec_<sid>_task_<tid>.log (dag_executor.py:210)
  → polls 1s for cancel_requested / run_token mismatch / 1800s timeout (dag_executor.py:467)

odin :: orchestrator.py :: _execute_task()
  → update_status EXECUTING (idempotent) (orchestrator.py:3243)
  → live trace: .odin/logs/task_<id>.trace.jsonl + task_<id>.out (orchestrator.py:3269)
    [microsandbox] trace tee'd from inside the VM via bind mount (harnesses/microsandbox.py:44)
  → harness executes in tmux session odin-<task_id> (or direct)
  → record_execution_result → POST /tasks/:id/execution_result/ status=REVIEW|FAILED
    (orchestrator.py:3523,3553 → backends/taskit.py:522)

views.py :: execution_result()  (views.py:2900)
  → discards stale results if metadata.ignore_execution_results (views.py:2920)
  → parses ODIN-STATUS envelope, posts metrics comment, stores last_failure_* metadata
  → [FAILED + escalation enabled] _maybe_escalate_model → back to IN_PROGRESS (views.py:3011)
  → [REVIEW] _trigger_auto_reflection(task) (views.py:3050)

  (if odin died without reporting: dag_executor fallback sets REVIEW/FAILED itself,
   classifies failure, posts log-tail comment — dag_executor.py:227-345)

views.py :: _trigger_auto_reflection()  (views.py:269)
  → skip_reflection (task or board) → straight to merge+advance (views.py:276)
  → creates ReflectionReport(PENDING) → execute_reflection.delay (views.py:315)

dag_executor.py :: execute_reflection()  (dag_executor.py:547)
  → subprocess: odin reflect <task_id> --report-id ... (timeout 1800s)
  → odin PATCHes /reflections/:id/ with verdict

views.py :: ReflectionReportViewSet.partial_update()  (views.py:3219)
  → posts "Reflection: <VERDICT>" comment

  [PASS]  → _merge_task_on_reflection_pass → merge_task_on_reflection.delay (views.py:3271,319)
    dag_executor.py :: merge_task_on_reflection (dag_executor.py:624)
      → merges task/<spec>/<id> into spec/<spec> branch
      → [merge ok/noop] _advance_task_to_testing: REVIEW → TESTING (dag_executor.py:739)
      → [conflict/error] stays REVIEW, merge_status=conflict|error, operator resolves

  [NEEDS_WORK|FAIL, <3 completed reports]
    → _maybe_reassign_on_quota_failure (quota → new agent) (views.py:489)
    → REVIEW → IN_PROGRESS, strategy.trigger → next poll re-executes (views.py:3319)

  [NEEDS_WORK|FAIL, >=3] → REVIEW → FAILED, "3 reflection attempts" comment (views.py:3289)

[operator/UI] odin spec finalize → PR created
views.py :: SpecViewSet.finalize (views.py:3754)
  → _transition_spec_tasks_to_done: all TESTING tasks in spec → DONE (dag_executor.py:911)
```

## Local strategy variant

`ODIN_EXECUTION_STRATEGY=local`: `execution/local.py::trigger()` (local.py:22) spawns `odin exec` immediately on the IN_PROGRESS edge — no Beat, no dep gating by the poller (odin's own dep check still applies). Empty strategy = nothing happens on IN_PROGRESS (manual `odin exec` only).

## Sub-flows / see also

- Dispatch internals, run_token/cancellation detail: `spec-task-lifecycle/02-execute-and-dispatch/`
- Reflection prompt assembly, verdict parsing, quota keywords: `spec-task-lifecycle/03-reflection-loop/`
- Worktree/branch mechanics and merge conflict handling: `git-worktree-isolation/`
- Corrections to spec-task-lifecycle: max concurrency default is **10** (settings.py:195; the `getattr` fallback of 3 in dag_executor.py:66 only applies if the setting were missing), and PASS no longer advances REVIEW → TESTING directly — the merge celery task does, after the merge lands (dag_executor.py:624).
