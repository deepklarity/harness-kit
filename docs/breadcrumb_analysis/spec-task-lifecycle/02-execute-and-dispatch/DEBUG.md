# Execute and Dispatch — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| DAG executor | `taskit/taskit-backend/logs/spec_<spec_id>_task_<task_id>.log` | Per-task subprocess output (odin exec stdout/stderr) |
| DAG executor | `taskit/taskit-backend/logs/taskit_detail.log` | Full tracebacks from DAG polling and execution |
| Odin orchestrator | `.odin/logs/run_<run_id>.jsonl` | Structured execution events from odin |
| Agent harness | `.odin/logs/trace_<task_id>.jsonl` | Raw stream-json from agent CLI |
| Django views | `taskit/taskit-backend/logs/taskit.log` | Abbreviated request/response log |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Task stuck in IN_PROGRESS, never moves to EXECUTING | `taskit_detail.log` | `poll_and_execute` — check if polling is running and what dep_status is returned |
| Task has no assignee (skipped by DAG) | `taskit_detail.log` | `Dep check` or check task directly with `task_inspect.py <id> --brief` |
| Task moves to EXECUTING then immediately FAILED | `spec_<spec_id>_task_<task_id>.log` | Check subprocess output; often auth failure or CLI not found |
| Run token mismatch (stale execution) | `taskit_detail.log` | `run_token mismatch` |
| Task cancelled but still running | Check PID: `ps -p <pid>` | PID stored in `task.metadata["active_execution"]["pid"]` |
| Odin updated status but DAG also tried | `taskit_detail.log` | `status already changed to` — this is normal, DAG defers |
| Task goes EXECUTING→REVIEW instantly, reflection says "no implementation" | `spec_<spec_id>_task_<task_id>.log` | `Waiting — unmet deps` — odin's dep check disagreed with TaskIt's. Check both `COMPLETED_STATUSES` definitions match |
| Dependency blocked but upstream looks done | Both `dependencies.py` files use `COMPLETED_STATUSES = {DONE, TESTING}` | Check upstream task status — REVIEW doesn't count as complete (still under reflection) |

## Quick commands

```bash
# Check task state and execution metadata
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --brief

# Check all tasks in a spec and their statuses
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --brief

# See what the DAG executor is doing right now
grep "poll_and_execute\|EXECUTING\|Dep check" taskit/taskit-backend/logs/taskit_detail.log | tail -30

# Check if Celery Beat is scheduling poll_and_execute
# (look for periodic task dispatch messages)
grep "poll_and_execute" taskit/taskit-backend/logs/taskit_detail.log | tail -5

# Check a task's execution log
cat taskit/taskit-backend/logs/spec_*_task_<task_id>.log | tail -50

# See active executions (tasks currently EXECUTING)
cd taskit/taskit-backend && python -c "
import django; import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from tasks.models import Task, TaskStatus
for t in Task.objects.filter(status=TaskStatus.EXECUTING):
    ae = (t.metadata or {}).get('active_execution', {})
    print(f'{t.id}: pid={ae.get(\"pid\")} strategy={ae.get(\"strategy\")} token={ae.get(\"run_token\",\"\")[:8]}')
"

# Check if odin CLI is on PATH
which odin || echo "odin not found on PATH"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_EXECUTION_STRATEGY` | Which execution path (local/celery_dag/disabled) | `""` (disabled) |
| `ODIN_CLI_PATH` | Path to odin binary for subprocess calls | `odin` |
| `ODIN_WORKING_DIR` | Fallback working directory if task/spec metadata don't specify | None |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | Max simultaneous EXECUTING tasks | `3` |
| `DAG_EXECUTOR_POLL_INTERVAL` | Seconds between poll_and_execute runs | `5` |

## Common breakpoints

- `dag_executor.py:poll_and_execute()` line 86 — after `check_deps()` returns, see why a task isn't ready
- `dag_executor.py:execute_single_task()` line 206 — after subprocess returns, before fallback status decision
- `taskit-backend/tasks/dependencies.py:check_deps()` line 44 — TaskIt's dep loop, see each dependency's status
- `odin/src/odin/dependencies.py:check_deps()` line 44 — odin's dep loop (must agree with TaskIt's)
- `views.py:execution_result()` line 999 — when odin posts results back, before status update
- `orchestrator.py:exec_task()` line 529 — dependency check inside odin (different from DAG's check)
- `orchestrator.py:_execute_task()` line 1814 — right before harness dispatch, see the final prompt
