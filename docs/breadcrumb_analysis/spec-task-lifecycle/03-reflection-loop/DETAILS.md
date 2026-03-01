# Reflection Loop — Detailed Trace

## 1. Manual reflection trigger

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `TaskViewSet.reflect()` (line ~1167)
**Called by**: `POST /tasks/:id/reflect/` (user action)
**Calls**: `ReflectionReport.objects.create()`, `execute_reflection.delay()`

Key logic:
- Status gate: task must be REVIEW, DONE, or FAILED
- Creates report with: reviewer_agent, reviewer_model, custom_prompt, context_selections, requested_by
- Dispatches Celery task immediately

Data in: `{"reviewer_agent": "claude", "reviewer_model": "claude-opus-4-6"}`
Data out: ReflectionReport(status=PENDING), 202 Accepted

---

## 2. Auto-reflection trigger

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `_trigger_auto_reflection(task)` (line 74)
**Called by**: `dag_executor.py :: execute_single_task()` after setting REVIEW (line 246)
**Calls**: `ReflectionReport.objects.create()`, `execute_reflection.delay()`

Key logic:
- Duplicate guard: checks for existing PENDING or RUNNING reflections on the task. Skips if one exists.
- Hardcoded defaults: `reviewer_agent="claude"`, `reviewer_model="claude-sonnet-4-5-20250929"`
- `requested_by="system@taskit"` distinguishes auto from manual
- Context selections: description, comments, execution_result, dependencies, metadata

Data in: task (already in REVIEW status)
Data out: ReflectionReport(status=PENDING), Celery task dispatched

---

## 3. Celery reflection execution

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `execute_reflection(report_id)` (line 396)
**Called by**: Celery (dispatched by either manual or auto trigger)
**Calls**: `subprocess.run("odin reflect ...")`

Key logic:
- Guards: report must be PENDING (line 412)
- Marks RUNNING before subprocess (line 416)
- Subprocess: `odin reflect <task_id> --report-id <id> --model <model> --agent <agent>`
- 300s timeout (line 452)
- Fallback: if odin didn't PATCH the report, sets COMPLETED (exit 0) or FAILED (exit != 0)

Data in: report_id
Data out: subprocess runs, odin PATCHes report directly

---

## 4. Odin reflection orchestrator

**File**: `odin/src/odin/reflection.py`
**Function**: `reflect_task()` (line ~238)
**Called by**: `odin reflect` CLI command
**Calls**: TaskIt API (GET task detail, PATCH report), harness.execute()

Key logic:
- Step 1: PATCH report status=RUNNING with assembled_prompt
- Step 2: GET /tasks/:id/detail/ → gather task context
  - Filters comments: skips status_update noise, CLI warnings, raw JSON
  - Parses execution output via `extract_text_from_stream()` (JSONL → text)
  - Truncates execution output to ~5000 chars
- Step 3: `build_reflection_prompt(context)` — structured audit prompt
  - Constraint-based: 5 exact section headers, exact verdict enum
  - Agent runs in READ-ONLY mode (can grep/read, no file modifications)
- Step 4: `harness.execute(prompt)` — reviewer agent runs
- Step 5: `parse_reflection_report(output)` — extracts sections + verdict
- Step 6: PATCH /reflections/:id/ with all results

Data in: task_id, report_id, model, agent
Data out: PATCH with verdict (PASS/NEEDS_WORK/FAIL), sections, token usage

---

## 5. Report result processing + status transitions

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `ReflectionReportViewSet.partial_update()` (line ~1280)
**Called by**: Odin PATCH /reflections/:id/
**Calls**: `TaskComment.objects.create()`, task status transitions

Key logic:
- Updates only fields explicitly sent in request
- Sets `completed_at` on terminal status (COMPLETED/FAILED)
- On COMPLETED with verdict_summary: posts reflection comment on task
  - Comment type: REFLECTION
  - Content: `**Reflection: <VERDICT>**\n\n<verdict_summary>`
  - Attachment: `{type: "reflection", report_id, verdict}`

### PASS verdict (lines ~1319-1342)
- Guard: task must still be in REVIEW (refresh_from_db)
- Transition: REVIEW → TESTING
- TaskHistory with `changed_by="system@taskit"`
- Downstream tasks now unblocked (TESTING is in COMPLETED_STATUSES)

### NEEDS_WORK or FAIL verdict (lines ~1350-1410)
- Guard: task must still be in REVIEW
- Counts completed reflections: `ReflectionReport.objects.filter(task=task, status=COMPLETED).count()`
- If count >= 3: REVIEW → FAILED + comment "Task failed after 3 reflection attempts without passing"
- If count < 3:
  1. Calls `_maybe_reassign_on_quota_failure(task, report)` — see §5a below
  2. REVIEW → IN_PROGRESS
  3. Fires execution strategy (triggers re-execution, now potentially with new agent)

---

## 5a. Quota failure detection and agent reassignment

**File**: `taskit/taskit-backend/tasks/views.py`
**Functions**: `_is_quota_failure()` (~line 116), `_find_alternative_agent()` (~line 146), `_maybe_reassign_on_quota_failure()` (~line 196)
**Called by**: Auto-advance block (§5) before status transition to IN_PROGRESS
**Side effects**: Updates task.assignee + task.model_name, creates TaskHistory entries, creates TaskComment

### Detection: `_is_quota_failure(task, report)`

Three sources, checked in priority order:
1. **Reflection field**: `report.quota_failure` is non-empty and not `"none."` — set by the reviewer agent's `### Quota / Resource Failure` section
2. **Task metadata**: `task.metadata["last_failure_type"] == "llm_call_failure"` AND `last_failure_reason` contains a quota keyword — set by orchestrator's `_classify_failure()` during execution
3. **Verdict summary**: `report.verdict_summary` contains a quota keyword — fallback when the reviewer mentions it in justification but didn't fill the dedicated field

Keywords: `"quota"`, `"rate limit"`, `"rate_limit"`, `"429"`, `"too many requests"`, `"usage limit"`, `"out of quota"`, `"quota exceeded"`, `"quota_failure"`

### Reassignment: `_find_alternative_agent(task)`

1. Queries `BoardMembership` for `role=AGENT` users on the same board, excluding current `task.assignee_id`
2. Fallback: any `role=AGENT` user system-wide, excluding current assignee
3. Picks first candidate (ordered by ID)
4. Model: uses `agent.available_models[0]` — handles both `[{"name": "..."}]` and `["..."]` formats

If no alternative exists: logs warning, posts "no alternative agent available" comment, returns without reassignment. Task retries with the same agent.

### Mutation: `_maybe_reassign_on_quota_failure(task, report)`

When quota failure is detected and an alternative agent is found:
- `task.assignee = new_agent`, `task.model_name = new_model`
- `task.save(update_fields=["assignee_id", "model_name"])`
- TaskHistory for `"assignee"` (old_name → new_name)
- TaskHistory for `"model"` (old_model → new_model, only if changed)
- TaskComment: `"Quota/rate-limit failure detected for {old}. Reassigned to {new} for retry."`

This runs BEFORE the status transition to IN_PROGRESS, so when execution fires, it uses the new agent.

### Origin: how quota failures reach task metadata

```
odin/src/odin/orchestrator.py :: _classify_failure() (~line 1817)
  → keyword match "rate|quota|token|429" → failure_type = "llm_call_failure"
  → bundled into execution_payload → POST /tasks/{id}/execution_result/

taskit/taskit-backend/tasks/views.py :: execution_result() (~line 1080)
  → stores in task.metadata: last_failure_type, last_failure_reason, last_failure_origin
```

### Origin: how quota_failure field reaches the reflection report

```
odin/src/odin/reflection.py :: build_reflection_prompt()
  → includes "### Quota / Resource Failure" section in audit prompt
  → instructs reviewer to output "QUOTA_FAILURE: <agent>" or "None."

odin/src/odin/reflection.py :: parse_reflection_report()
  → extracts "quota / resource failure" header → result["quota_failure"]

odin/src/odin/reflection.py :: reflect_task()
  → PATCH /reflections/{id}/ includes quota_failure field
```

---

## 6. Re-execution after NEEDS_WORK

After NEEDS_WORK moves a task to IN_PROGRESS and fires the execution strategy:

- `celery_dag` strategy: no-op at trigger time; `poll_and_execute()` picks it up on next 5s cycle
- `local` strategy: immediately spawns subprocess
- DAG executor checks deps again (should still be READY since upstream hasn't changed)
- Task moves to EXECUTING → agent reworks → REVIEW → auto-reflection triggers again

### Context passed to agent on re-execution

`orchestrator.exec_task()` builds the prompt with three context layers:

1. **Reflection feedback** (partial fix, 2026-02-27): `_build_reflection_context()` finds the latest NEEDS_WORK reflection comment and prepends it as "Address ALL of the following issues before resubmitting". **Limitation**: Only NEEDS_WORK verdicts are injected — if a task is manually retried after FAIL, the feedback is not forwarded.

2. **Self-context**: `_build_self_context()` includes latest `summary` comment + human notes after it. Returns empty if no summary exists (common on first retry).

3. **Upstream context**: Latest single comment per completed dependency.

**Known gap**: Status_update comments, proof submissions, and agent execution output from previous rounds are NOT included in the re-execution prompt. The agent sees reflection feedback (if NEEDS_WORK) but not what it actually produced or attempted. See `docs/solutions/architecture/exec-task-context-injection-gap-20260227.md` for the full analysis and remaining fix plan.

---

## 7. ReflectionReport model

**File**: `taskit/taskit-backend/tasks/models.py`

Fields relevant to the flow:
- `status`: PENDING → RUNNING → COMPLETED/FAILED
- `verdict`: PASS, NEEDS_WORK, FAIL (string, max 20 chars)
- `verdict_summary`: justification text
- `improvements`: actionable items (max 5)
- `quota_failure`: "QUOTA_FAILURE: <agent>" or "None." or empty (set by reviewer)
- `task` (FK): links report to task
- `requested_by`: "system@taskit" (auto) or user email (manual)

No task metadata counter — loop count derived from completed report count in DB.
