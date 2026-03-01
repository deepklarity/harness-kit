# Reflection Loop

## Manual reflection

Trigger: User clicks "Reflect" on a task in REVIEW, DONE, or FAILED status
End state: ReflectionReport created with verdict (PASS/NEEDS_WORK/FAIL), comment posted on task. No status change.

```
[User action]
POST /tasks/:id/reflect/ {reviewer_agent, reviewer_model}

views.py :: TaskViewSet.reflect()
  → validates task.status in (REVIEW, DONE, FAILED)
  → creates ReflectionReport(status=PENDING)
  → execute_reflection.delay(report.id)

dag_executor.py :: execute_reflection(report_id)  [Celery task]
  → report.status = RUNNING
  → resolves working_dir (task metadata > spec metadata > env)
  → subprocess: odin reflect <task_id> --report-id <id> --model <model> --agent <agent>

cli.py :: reflect(task_id, report_id, model, agent)
  → reflection.py :: reflect_task()
    → PATCH /reflections/:id/ status=RUNNING, assembled_prompt=<prompt>
    → GET /tasks/:id/detail/ → gather context (title, description, execution output, comments, deps)
    → build_reflection_prompt(context) → structured audit prompt
    → harness.execute(prompt) → reviewer agent runs (300s timeout, read-only mode)
    → parse_reflection_report(output) → {quality_assessment, slop_detection, improvements, agent_optimization, verdict, verdict_summary}
    → PATCH /reflections/:id/ status=COMPLETED, sections=..., verdict=...

views.py :: ReflectionReportViewSet.partial_update()
  → saves report fields
  → if COMPLETED + has verdict_summary:
    → creates TaskComment(type=REFLECTION) on task
    → content: "**Reflection: <VERDICT>**\n\n<verdict_summary>"
    → attachments: [{type: "reflection", report_id, verdict}]
```

Manual flow does NOT change task status. Reflection is advisory only.

---

## Auto-reflection with retry loop (implemented)

Trigger: Task transitions from EXECUTING → REVIEW via DAG executor
End state: Task reaches TESTING (reflection passed) or FAILED (3 failed attempts)

### State machine

```
EXECUTING
  ↓ (agent completes work, exit 0)
REVIEW
  ↓ (auto-trigger: _trigger_auto_reflection)
  ↓ (reflection runs via odin reflect)
  │
  ├─ verdict=PASS → TESTING
  │    → downstream tasks can proceed (TESTING is in COMPLETED_STATUSES)
  │
  ├─ verdict=NEEDS_WORK or FAIL, completed_count < 3
  │    → _maybe_reassign_on_quota_failure(task, report)
  │      [quota detected] → find alternative AGENT on board → update assignee + model_name
  │      [not quota]      → no reassignment, keeps current agent
  │    → REVIEW → IN_PROGRESS
  │    → fires execution strategy (DAG picks up on next poll, now with new agent if reassigned)
  │    → agent reworks, pushes for REVIEW again
  │    → cycle repeats
  │
  ├─ verdict=NEEDS_WORK or FAIL, completed_count >= 3 → FAILED
  │    → "Task failed after 3 reflection attempts without passing"
```

### Auto-trigger

```
dag_executor.py :: execute_single_task()
  → exit_code 0 → task.status = REVIEW
  → views.py :: _trigger_auto_reflection(task)
    → checks for existing PENDING/RUNNING reflection (duplicate guard)
    → creates ReflectionReport(
        reviewer_agent="claude",
        reviewer_model="claude-sonnet-4-5-20250929",
        requested_by="system@taskit",
        status=PENDING)
    → execute_reflection.delay(report.id)
```

### Post-reflection status transitions

```
views.py :: ReflectionReportViewSet.partial_update()
  → [existing] saves report, posts reflection comment

  [verdict = PASS]
  → task.status = REVIEW → TESTING
  → TaskHistory(changed_by="system@taskit")
  → downstream tasks can now proceed

  [verdict = NEEDS_WORK, completed_count < 3]
  → task.status = REVIEW → IN_PROGRESS
  → TaskHistory(changed_by="system@taskit")
  → if task has assignee: fires execution strategy (triggers re-execution)
  → on re-execution: orchestrator injects latest NEEDS_WORK reflection as prompt context
    (partial — see DETAILS.md §6 for known gaps in context injection)
  → cycle repeats

  [verdict = NEEDS_WORK, completed_count >= 3]
  → task.status = REVIEW → FAILED
  → TaskHistory + TaskComment: "Task failed after 3 reflection attempts"

  [verdict = FAIL]
  → no status change (same as manual reflection)
```

### Dependency gating

```
taskit-backend/tasks/dependencies.py :: COMPLETED_STATUSES = {DONE, TESTING}
odin/src/odin/dependencies.py        :: COMPLETED_STATUSES = {DONE, TESTING}

Both files must agree. REVIEW is excluded because task may loop back to
IN_PROGRESS via NEEDS_WORK. Only TESTING (reflection passed) and DONE
unblock dependents.
```

### Quota failure reassignment

When an agent fails due to quota/rate-limit exhaustion, the retry loop reassigns to a different agent before re-executing.

```
views.py :: _maybe_reassign_on_quota_failure(task, report)
  → _is_quota_failure() checks three sources:
    1. report.quota_failure field (set by reflection reviewer, most reliable)
    2. task.metadata["last_failure_type"] == "llm_call_failure" + quota keywords in reason
    3. report.verdict_summary contains "quota", "rate limit", "429", etc.

  [quota detected]
  → _find_alternative_agent(task)
    1. AGENT users on same board (BoardMembership), excluding current assignee
    2. Fallback: any AGENT user in system, excluding current
    3. No alternative: logs warning, posts comment, keeps same agent
  → updates task.assignee + task.model_name
  → TaskHistory for "assignee" and "model" changes
  → TaskComment: "Quota/rate-limit failure detected for X. Reassigned to Y for retry."

  [not quota]
  → returns immediately, no changes
```

Quota keywords (searched case-insensitively):
`"quota"`, `"rate limit"`, `"rate_limit"`, `"429"`, `"too many requests"`, `"usage limit"`, `"out of quota"`, `"quota exceeded"`, `"quota_failure"`

### Loop counting

Loop count is based on `ReflectionReport.objects.filter(task=task, status=COMPLETED).count()`.
No metadata counter — the count comes from actual completed reports in the DB.

| Event | completed_count | Status transition |
|-------|-----------------|-------------------|
| First execution → REVIEW → reflection runs | 1 | PASS → TESTING, or NEEDS_WORK → IN_PROGRESS |
| Second execution → REVIEW → reflection runs | 2 | PASS → TESTING, or NEEDS_WORK → IN_PROGRESS |
| Third execution → REVIEW → reflection runs | 3 | PASS → TESTING, or NEEDS_WORK → FAILED |
