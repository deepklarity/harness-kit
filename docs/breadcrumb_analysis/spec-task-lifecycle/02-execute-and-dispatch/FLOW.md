# Execute and Dispatch

Trigger: Task exists in IN_PROGRESS with assignee and satisfied dependencies
End state: Task reaches REVIEW (success) or FAILED (error)

## Flow

### Entry: How tasks get to IN_PROGRESS

Three paths:
- User drags task on kanban → PATCH `/tasks/:id/` with `status=IN_PROGRESS`
- `odin plan --quick` → creates tasks directly in IN_PROGRESS
- API call → explicit status change

```
views.py :: TaskViewSet.update()
  → if status changing to IN_PROGRESS:
    → clears stale stop guards (metadata cleanup)
    → if task has assignee:
      → execution/registry.py :: get_strategy()
      → strategy.trigger(task)

  [strategy=local]
  execution/local.py :: trigger(task)
    → immediately spawns subprocess: odin exec <task_id>
    → stores PID in task.metadata["active_execution"]
    → does NOT manage status — odin handles that via execution_result endpoint

  [strategy=celery_dag]
  execution/celery_dag.py :: trigger(task)
    → no-op (task waits for poll cycle)
```

### Celery DAG polling (celery_dag strategy)

```
dag_executor.py :: poll_and_execute()  [Celery Beat, every 5s]
  → counts EXECUTING tasks vs DAG_EXECUTOR_MAX_CONCURRENCY (default 3)
  → if no available slots: return

  → queries IN_PROGRESS tasks ordered by created_at (FIFO)
  → for each candidate:
    → skip if no assignee
    → dependencies.py :: check_deps(task)
      → READY: all deps DONE or TESTING
      → WAITING: some deps still running (includes REVIEW — reflection not yet passed)
      → BLOCKED: any dep FAILED
    → if READY: add to ready_tasks list

  → for each ready task (up to available_slots):
    → transaction.atomic():
      → SELECT FOR UPDATE (race condition guard)
      → verify still IN_PROGRESS
      → clear stale stop guards from metadata
      → set metadata["active_execution"] = {strategy, run_token, queued_at, cancel_requested: false}
      → task.status = EXECUTING
      → create TaskHistory record

    → execute_single_task.delay(task.id, run_token)
    → store celery_task_id in metadata for cancellation
```

### Task execution

```
dag_executor.py :: execute_single_task(task_id, run_token)  [Celery task]
  → validate task exists and is EXECUTING
  → validate run_token matches (prevents stale retries)

  → resolve working_dir: task metadata > spec metadata > ODIN_WORKING_DIR env
  → write resolved working_dir back to task.metadata

  → _run_subprocess_with_cancellation(task_id, cmd, working_dir, log_file, run_token)
    → subprocess.Popen("odin exec <task_id>", start_new_session=True)
    → stores PID in metadata["active_execution"]
    → polls proc.wait(timeout=1s) in loop:
      → on exit: return (exit_code, failure_stage)
      → on timeout: check for cancel_requested or run_token_mismatch
        → if cancelled: SIGTERM → wait 5s → SIGKILL
        → if run_token_mismatch: SIGKILL immediately

  → re-read task from DB
  → if odin already changed status (not EXECUTING): respect it, return
  → else fallback status:
    → exit_code 0: task.status = REVIEW
      → _trigger_auto_reflection(task) — creates ReflectionReport, dispatches Celery task
      → see 03-reflection-loop for what happens next
    → exit_code != 0: task.status = FAILED
      → _classify_failure() → sets last_failure_type, last_failure_reason
      → posts failure comment with debug excerpt from log tail
```

### Inside odin exec (what the subprocess does)

IMPORTANT: odin has its OWN dependency check (odin/src/odin/dependencies.py)
separate from TaskIt's (taskit/taskit-backend/tasks/dependencies.py).
Both must use the same COMPLETED_STATUSES = {DONE, TESTING}.
If they diverge, TaskIt dispatches the task but odin skips it — producing
zero output and a false REVIEW → reflection FAIL cascade.

```
cli.py :: exec_task(task_id)
  → orchestrator.py :: exec_task(task_id, working_dir)
    → loads task via TaskManager
    → odin/dependencies.py :: check_deps() — SECOND dep check (belt-and-suspenders)
      → must agree with TaskIt's check, or task gets skipped with exit 0
      → if BLOCKED: returns error, no execution
      → if WAITING: posts "Waiting" comment, returns error (exit 0 — not a crash)
    → injects upstream context (latest comment per completed dep — shallow)
    → injects reflection feedback (latest NEEDS_WORK reflection, if any)
    → injects self-context (prior summary + human notes — empty if no summary)
    → NOTE: most comment history (proof, status_update, execution output) is NOT forwarded
      see docs/solutions/architecture/exec-task-context-injection-gap-20260227.md

    → _execute_task(task_id, agent_name, prompt, working_dir)
      → task.status = EXECUTING (redundant with DAG, but idempotent)
      → _generate_mcp_config() — per-CLI MCP server configs
      → harness.build_execute_command(prompt, context)

      [tmux available]
      → _execute_via_tmux() in session odin-<task_id>
        → user can attach with: tmux attach -t odin-<task_id>

      [tmux unavailable]
      → harness.execute(prompt, context) directly

      → _extract_agent_text(raw_output) — parse JSON stream
      → _parse_envelope() — extract ODIN-STATUS block
      → task_mgr.record_execution_result() → POST /tasks/:id/execution_result/
        → includes: success, raw_output, effective_input, duration_ms, token usage
      → update task status: REVIEW or FAILED
```

### Execution result processing (backend)

```
views.py :: TaskViewSet.execution_result()  [POST /tasks/:id/execution_result/]
  → extracts agent text from structured CLI output
  → parses ODIN-STATUS envelope
  → composes metrics-inline comment (duration, tokens, cost)
  → records TaskHistory for status change
  → stores execution metadata (duration, cost, failure reason)
  → creates TaskComment with formatted output

  → if metadata has "ignore_execution_results": skip (stale execution guard)
```

## Key data: task.metadata during execution

```json
{
  "active_execution": {
    "strategy": "celery_dag",
    "run_token": "abc123",
    "celery_task_id": "task-uuid",
    "pid": 12345,
    "queued_at": 1234567890,
    "cancel_requested": false
  },
  "working_dir": "/path/to/repo",
  "selected_model": "claude-opus-4-6"
}
```

After completion:
```json
{
  "last_duration_ms": 45000,
  "selected_model": "claude-opus-4-6",
  "total_estimated_cost_usd": 0.25,
  "last_failure_type": "agent_execution_failure",
  "last_failure_reason": "...",
  "last_failure_origin": "taskit_dag_executor"
}
```
