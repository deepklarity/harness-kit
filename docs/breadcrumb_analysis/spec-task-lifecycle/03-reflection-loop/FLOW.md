# Reflection Loop

Trigger: a task enters REVIEW (auto), or a user clicks "Reflect" (manual, on REVIEW/DONE/FAILED).
End state: a `ReflectionReport` with verdict `PASS` / `NEEDS_WORK` / `FAIL`; on the auto path the task then merges + advances to TESTING, reworks, or FAILs after 3 attempts.

## Manual reflection

```
POST /tasks/:id/reflect/ {reviewer_agent, reviewer_model}

views.py :: TaskViewSet.reflect()          (~3501)
  → validates task.status in (REVIEW, DONE, FAILED)
  → creates ReflectionReport(status=PENDING)
  → execute_reflection.delay(report.id)

dag_executor.py :: execute_reflection(report_id)   [Celery]  (~1203)
  → report.status = RUNNING
  → subprocess: odin reflect <task_id> --report-id <id> --model <model> --agent <agent>
    (timeout DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS, default 1800s)

reflection.py :: reflect_task()            (~553)
  → PATCH /reflections/:id/ status=RUNNING, assembled_prompt=<prompt>
  → GET /tasks/:id/detail/ → assemble context (see DETAILS.md §4: comments, proof, execution output, deps)
  → build_reflection_prompt(context) → structured read-only audit prompt
  → harness.execute(prompt) → reviewer runs read-only (read_only_workspace=True)
  → parse_reflection_report(output) → sections + verdict
  → PATCH /reflections/:id/ status=COMPLETED, sections=…, verdict=…

views.py :: ReflectionReportViewSet.partial_update()   (~3566)
  → saves report fields, posts TaskComment(type=REFLECTION) on COMPLETED+summary
```

Manual reflection on a REVIEW task follows the same verdict → merge/rework dispatch as the auto path below (it is the same `partial_update` handler). On DONE/FAILED tasks it is advisory (no eligible transition).

---

## Auto-reflection with retry loop

Trigger: a task transitions to REVIEW.
End state: TESTING (passed, after merge) or FAILED (3 completed attempts without a PASS).

### State machine

```
EXECUTING ──(agent completes, exit 0)──▶ REVIEW
                                           │  _trigger_auto_reflection(task)  (views.py ~277)
                                           ▼  odin reflect runs
                              ┌────────────┴─────────────┐
                       verdict PASS               verdict NEEDS_WORK / FAIL
                              │                            │
        _merge_task_on_reflection_pass(task)        completed_count …
        → merge_task_on_reflection.delay()          ├─ ≥ 3 → REVIEW → FAILED
        → merge task branch into spec branch        │        "failed after 3 reflection attempts"
        → _advance_task_to_testing()                │
        → REVIEW → TESTING                          └─ < 3 → _maybe_reassign_on_quota_failure()
        → downstream unblocked                                → _record_rework_continuity()
                                                              → REVIEW → IN_PROGRESS
                                                              → re-trigger execution (gated on
                                                                DAG_EXECUTOR_MAX_CONCURRENCY)
```

Key change from older docs: **PASS no longer transitions REVIEW→TESTING directly** — it first merges the task branch (`_merge_task_on_reflection_pass` → Celery `merge_task_on_reflection`), then `_advance_task_to_testing` sets TESTING. And **`FAIL` is not advisory-only** — it shares the NEEDS_WORK retry/fail path (`verdict in ("NEEDS_WORK","FAIL")`).

### Auto-trigger and the reviewer default

`_trigger_auto_reflection(task)` (`views.py` ~277) fires from three sites now, not one:
- `dag_executor.py :: execute_single_task()` (~607) after REVIEW is set
- `views.py` TaskViewSet update (~2862) when status → REVIEW
- `views.py` execution_result endpoint (~3397) when the result moves a task to REVIEW

**There is no hardcoded reviewer default.** `_trigger_auto_reflection` calls `_reflection_reviewer_defaults(board=task.board)` (~300), which resolves via `_find_first_available_reviewer()` (~123): it picks an available board-member agent from `REFLECTION_PREFERRED_AGENTS = ["gemini","codex","claude"]` (randomized among the enabled set, `random.shuffle` at ~194), using that agent's default (or first) `available_models` entry. A board `reflection_model` override or a forced-provider selection wins. Retired providers are filtered out (`is_active`, ~142). Older claims of a fixed `haiku` or `claude-sonnet-4-5-20250929` default are stale.

Two early exits merge directly without a reviewer: `skip_reflection` on task/board (~283), and "no reviewer available" (~301).

### Dependency gating

```
taskit-backend/tasks/dependencies.py :: COMPLETED_STATUSES = {DONE, TESTING}   (line 36) — REVIEW excluded (fable task 214)
odin/src/odin/dependencies.py        :: COMPLETED_STATUSES = {DONE, TESTING}   (line 35) — REVIEW excluded (fable task 214)
```

Both files must agree. Only a dependency in TESTING or DONE unblocks dependents — TESTING is the post-merge gate. A dep in REVIEW is *not* complete (branch pre-merge), so a dependent that started then would build against missing upstream code.

### Quota-failure reassignment

The `< 3` rework branch calls `_maybe_reassign_on_quota_failure(task, report)` (~3676) before requeuing. As merged in **W3.11**, this verifies real usage via `harness_usage_status` before switching providers (429-with-headroom → backoff the same agent; genuinely exhausted → reassign to a same-cost-tier fallback). That flow has its own breadcrumb: **`../../quota-failover-reassignment/`** — see it rather than re-deriving the logic here.

### Loop counting

Loop count = `ReflectionReport.objects.filter(task=task, status=COMPLETED).count()` (~3629). No metadata counter.

| Event | completed_count | Transition |
|-------|-----------------|-----------|
| 1st reflection completes | 1 | PASS → merge → TESTING, or NEEDS_WORK/FAIL → IN_PROGRESS |
| 2nd | 2 | same |
| 3rd | 3 | PASS → merge → TESTING, or NEEDS_WORK/FAIL → FAILED |
