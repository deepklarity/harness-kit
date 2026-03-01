# Spec Task Lifecycle (Post-Planning)

Tasks already exist in TODO/IN_PROGRESS with dependencies resolved and agents assigned. This traces what happens from there to completion or failure.

Split into sub-flows because: execution and reflection are distinct phases that fail independently, and the auto-reflection loop creates a cycle between them.

## Sub-flows (execution order)

1. **02-execute-and-dispatch** — DAG picks up IN_PROGRESS tasks, checks dependencies (dual check: TaskIt + odin), dispatches to harnesses via odin exec, processes results into REVIEW or FAILED
2. **03-reflection-loop** — Auto-reflection triggers on REVIEW, verdict drives status transitions (PASS → TESTING, NEEDS_WORK/FAIL → retry up to 3x with quota-aware agent reassignment, then FAILED)

## Status lifecycle

```
TODO → IN_PROGRESS → EXECUTING → REVIEW → [reflection] → TESTING → DONE
            ↑                                  ↓ (NEEDS_WORK, <3 loops)
            └──────────────────────────────────┘
                                               ↓ (NEEDS_WORK, >=3 loops)
                                             FAILED
```

## Dependency satisfaction

Both TaskIt and odin define `COMPLETED_STATUSES` independently — they MUST agree:

```python
# taskit-backend/tasks/dependencies.py
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}

# odin/src/odin/dependencies.py
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}
```

REVIEW is excluded: task is still under reflection and may loop back to IN_PROGRESS.
Only TESTING (reflection passed) and DONE unblock dependents.

## Execution strategies

| Strategy | Env var | How tasks move IN_PROGRESS → EXECUTING |
|----------|---------|----------------------------------------|
| `local` | `ODIN_EXECUTION_STRATEGY=local` | Immediate subprocess on status change |
| `celery_dag` | `ODIN_EXECUTION_STRATEGY=celery_dag` | Polled every 5s by Celery Beat |
| disabled | `ODIN_EXECUTION_STRATEGY=""` | Manual `odin exec` only |

## Key env vars

| Variable | Used by | Default |
|----------|---------|---------|
| `ODIN_EXECUTION_STRATEGY` | taskit-backend | `""` (disabled) |
| `ODIN_CLI_PATH` | dag_executor.py | `odin` |
| `ODIN_WORKING_DIR` | dag_executor.py, orchestrator.py | cwd |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | dag_executor.py | `3` |
| `DAG_EXECUTOR_POLL_INTERVAL` | dag_executor.py | `5` (seconds) |
