# Execute and Dispatch — Detailed Trace

## 1. Task enters IN_PROGRESS

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `TaskViewSet.update()` (line ~640)
**Called by**: PATCH `/tasks/:id/` (kanban drag, API call, odin plan)
**Calls**: `execution/registry.py :: get_strategy()` → `strategy.trigger(task)`

Key logic:
- Clears stale stop guards when transitioning TO IN_PROGRESS (lines 693-695): `_clear_stop_guards(task.metadata)`
- Only triggers execution strategy if task has an assignee (line 708)
- Executing mutation lock: if task is currently EXECUTING, rejects status/assignee/model changes with 409 (lines ~670-690)

Data in: `{"status": "IN_PROGRESS"}` via PATCH
Data out: Task saved with cleared metadata, strategy triggered

---

## 2. DAG poll cycle

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `poll_and_execute()` (line 52)
**Called by**: Celery Beat schedule (every `DAG_EXECUTOR_POLL_INTERVAL` seconds)
**Calls**: `check_deps()`, `execute_single_task.delay()`

Key logic:
- Concurrency gate (lines 61-67): counts EXECUTING tasks, exits if at max
- Only queries IN_PROGRESS tasks (line 69-71) — TODO tasks never touched
- FIFO ordering by `created_at` (line 71)
- Skips tasks without assignee (line 82-83)
- Atomic transition to EXECUTING with SELECT FOR UPDATE (lines 98-123) — prevents race with concurrent poll cycles
- Clears stale metadata fields: `ignore_execution_results`, `stopped_run_token`, `execution_stopped_at` (lines 104-106)

Data in: None (polls DB)
Data out: Tasks transitioned to EXECUTING, Celery tasks dispatched

---

## 3. Dependency checking (dual — TaskIt + odin)

There are TWO independent dependency checks. Both must use the same
`COMPLETED_STATUSES` or tasks get dispatched then skipped.

### 3a. TaskIt check (Celery poller)

**File**: `taskit/taskit-backend/tasks/dependencies.py`
**Function**: `check_deps(task)` (line 30)
**Called by**: `poll_and_execute()`
**Calls**: DB query on dependent tasks

Key logic:
- Always queries DB at runtime — never cached. This enables recovery: fix a FAILED upstream, dependent auto-unblocks on next poll
- `COMPLETED_STATUSES = {DONE, TESTING}` (line 21). REVIEW is excluded — task is still under reflection and may loop back to IN_PROGRESS via NEEDS_WORK
- Algorithm: any FAILED → BLOCKED, all completed → READY, else → WAITING

Data in: `task.depends_on` (list of task IDs)
Data out: `DepStatus.READY | WAITING | BLOCKED`

### 3b. Odin check (inside subprocess)

**File**: `odin/src/odin/dependencies.py`
**Function**: `check_deps(task, task_resolver)` (line 29)
**Called by**: `orchestrator.exec_task()`
**Calls**: task_resolver (HTTP GET to TaskIt)

Key logic:
- Same algorithm as TaskIt's check but operates on odin's Pydantic Task model
- `COMPLETED_STATUSES = {DONE, TESTING}` — must match TaskIt's definition
- If these diverge: TaskIt dispatches the task (EXECUTING) but odin skips it (exit 0, no output), DAG executor sets REVIEW, reflection sees no implementation → FAIL

DIVERGENCE RISK: These two files define COMPLETED_STATUSES independently.
A TODO exists to DRY them into a single source of truth.

Data in: `task.depends_on` (list of task IDs) resolved via HTTP
Data out: `DepStatus.READY | WAITING | BLOCKED`

---

## 4. Subprocess execution with cancellation

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `_run_subprocess_with_cancellation()` (line 319)
**Called by**: `execute_single_task()`
**Calls**: `subprocess.Popen()`, polls `proc.wait()`

Key logic:
- Spawns in new session (`start_new_session=True`, line 333) for clean SIGKILL of process group
- Stores PID in metadata (line 342) for external stop requests
- Poll loop (lines 347-361):
  - `proc.wait(timeout=1s)` — exits on completion
  - On timeout: refresh task from DB, check for `cancel_requested` or `run_token_mismatch`
  - Cancel: SIGTERM → 5s grace → SIGKILL (via `_terminate_process`)
  - Token mismatch: immediate SIGKILL

Data in: CLI command, working_dir, log file path
Data out: `(exit_code, failure_stage)` tuple

---

## 5. Odin exec task (inside subprocess)

**File**: `odin/src/odin/orchestrator.py`
**Function**: `exec_task()` (line 504)
**Called by**: `odin exec <task_id>` CLI
**Calls**: `_execute_task()`, `task_mgr.record_execution_result()`

Key logic:
- Resolves task by ID prefix (lines 510-520)
- Re-checks dependencies via odin/dependencies.py (belt-and-suspenders with TaskIt's check — see section 3b)
- Context injection layers (each prepended to task description with `---` separator):
  1. **Upstream context** (lines 654-672): For each completed dep, takes the **latest single comment** only (first 2000 chars). Other dep comments are lost.
  2. **Reflection feedback** (lines 674-677): `_build_reflection_context()` scans for latest `NEEDS_WORK` reflection comment. **Gap**: Only NEEDS_WORK verdicts are injected — FAIL verdicts are skipped even if task is manually retried.
  3. **Self-context** (lines 680-682): `_build_self_context()` finds latest `summary` comment + human notes posted after it. **Gap**: Returns empty string if no summary exists (common on first retry). Status_update, proof, and agent execution output comments are never included.

**Known gap**: On re-execution after reflection, the agent often receives only the original task description with no history of what was attempted, what feedback was given, or what proof was submitted. See `docs/solutions/architecture/exec-task-context-injection-gap-20260227.md` for full analysis and fix plan.

Side effects:
- Updates task status to EXECUTING (in `_execute_task`)
- Generates per-CLI MCP config files
- Posts execution result via `task_mgr.record_execution_result()` → backend endpoint

---

## 6. Harness execution

**File**: `odin/src/odin/harnesses/<agent>.py` (claude.py, codex.py, gemini.py, etc.)
**Function**: `execute(prompt, context)` or `build_execute_command(prompt, context)`
**Called by**: `_execute_task()` in orchestrator
**Calls**: `asyncio.create_subprocess_exec()` for CLIs

Key logic per harness:
- **Claude**: `claude -p <prompt> --output-format stream-json --verbose --model <model> --mcp-config <path>`
- **Codex**: `codex --quiet --model <model> --full-auto --no-server exec -p <prompt>`
- **Gemini**: `gemini -p <prompt> --output-format json --model <model>`

All CLI harnesses:
- Write raw JSONL to trace_file
- Extract readable text to output_file
- Parse token usage from final protocol event
- Return `TaskResult(success, output, duration_ms, metadata)`

---

## 7. Result reporting

**File**: `odin/src/odin/taskit/manager.py`
**Function**: `record_execution_result()` (line 207)
**Called by**: `_execute_task()` after harness completes
**Calls**: Backend `POST /tasks/:id/execution_result/`

Key logic:
- Parses ODIN-STATUS envelope from agent output
- Composes metric-inline comment: duration, token counts, cost
- For local backend: updates task status directly
- For TaskIt backend: delegates entire payload to REST endpoint

Data passed to backend:
```json
{
  "success": true,
  "raw_output": "...",
  "effective_input": "...",
  "error": null,
  "duration_ms": 45000,
  "agent": "claude",
  "metadata": {
    "usage": {"input_tokens": 5000, "output_tokens": 2000},
    "selected_model": "claude-opus-4-6",
    "taskit_run_token": "abc123"
  }
}
```

---

## 8. Fallback status in DAG executor

**File**: `taskit/taskit-backend/tasks/dag_executor.py`
**Function**: `execute_single_task()` (lines 206-265)
**Called by**: Celery (after subprocess returns)
**Calls**: DB save, TaskComment.objects.create()

Key logic:
- Re-reads task from DB (line 207)
- If odin already changed status (not EXECUTING): respects it, returns (line 208-211)
- Otherwise fallback: exit 0 → REVIEW, non-zero → FAILED (lines 214-219)
- On REVIEW: calls `_trigger_auto_reflection(task)` (line 246-247) — creates ReflectionReport and dispatches Celery task. See 03-reflection-loop for the full cycle.
- On failure:
  - `_classify_failure()` categorizes: cancelled, timeout, spawn_exception, backend_auth_failure, agent_execution_failure
  - Posts comment with failure type + reason + log tail excerpt
  - Stores failure metadata: `last_failure_type`, `last_failure_reason`, `last_failure_origin`
