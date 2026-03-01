# Execution Model

Odin is a **single-task executor**. Dependency resolution and scheduling
belong to TaskIt + Celery. Odin executes one task at a time in the
foreground.

## Single-Task Execution

```bash
odin exec <task_id>          # Execute a single task (foreground)
odin exec <task_id> --mock   # Mock mode: no backend writes
```

Task output streams to `.odin/logs/task_<id>.out` files for live tailing.

## Mock Mode

`odin exec <id> --mock` runs the harness locally but **skips all backend
writes**: no status changes, no comments, no cost tracking.  Use this to
test execution flow without affecting TaskIt state or triggering Celery.

## Status Semantics

| Status | Meaning | Set by |
|---|---|---|
| `TODO` | Planned, assigned, not started | `odin plan` |
| `IN_PROGRESS` | Queued for execution (dispatch signal) | `odin plan --quick` / `odin run` / user |
| `EXECUTING` | Agent actively running | `odin exec <id>` or Celery DAG executor |
| `REVIEW` | Agent finished, awaiting human review | Executor on success |
| `FAILED` | Execution failed | Executor on failure |

### Lifecycle Rule

`IN_PROGRESS` → `EXECUTING` → `REVIEW` or `FAILED`.

`TODO → EXECUTING` is forbidden — a task must pass through `IN_PROGRESS`
first. This ensures consistent history tracking and lets the frontend
distinguish "queued, waiting for deps" from "agent actively running."

When a task is already `EXECUTING` (set by the DAG executor before calling
odin), odin skips the transition and starts running immediately.

## Live Output — `odin logs`

View logs and follow live output from running tasks:

```bash
# Last 50 lines of the latest run log
odin logs

# Run log filtered to a specific task
odin logs a1b2

# Debug log (odin_detail.log — full tracebacks)
odin logs debug

# Follow all running tasks (interleaved, color-coded by agent)
odin logs -f

# Follow a specific task
odin logs a1b2 -f

# Follow the debug log
odin logs debug -f

# Control line count
odin logs -n 100

# Access logs from any directory via board registry
odin logs -b 5
odin logs debug -b 5 -f
```

When following all tasks, output is prefixed with `[task_id:agent]`:

```
[a1b2c3d4:gemini] Writing intro paragraph...
[d4e5f6a1:qwen] Generating code scaffold...
[a1b2c3d4:gemini] Done.
```

Follow mode exits automatically when the task(s) finish. Press Ctrl+C to stop early.

> **Note:** `odin tail` still works as a deprecated alias for `odin logs -f`.

## Stopping Execution — `odin stop`

Stop a running task:

```bash
# Graceful stop (SIGTERM)
odin stop a1b2

# Force kill (SIGKILL)
odin stop a1b2 --force
```

## Dashboard — `odin watch`

Auto-refreshing status view:

```bash
odin watch                # Refresh every 2s
odin watch --interval 5   # Custom interval
odin watch --spec sp_a1   # Filter by spec
```

Shows the task table. Ctrl+C to exit.

## DAG Executor (Celery)

When using the TaskIt backend with `ODIN_EXECUTION_STRATEGY=celery_dag`, execution
is handled by a Celery worker that polls for ready tasks:

### State Machine

```
BACKLOG → TODO → IN_PROGRESS → EXECUTING → REVIEW → DONE
                      ↑              ↓
                      └── FAILED ←───┘
```

- **TODO**: Task exists but is not queued for execution
- **IN_PROGRESS**: Task is queued for execution (dependencies may not be met yet)
- **EXECUTING**: Agent is actively running (set by DAG executor when deps are satisfied)
- **REVIEW**: Agent finished successfully, awaiting human review
- **FAILED**: Execution failed (human can fix and retry)

### How It Works

The DAG executor **only acts on IN_PROGRESS tasks**. It never touches TODO
tasks — moving a task from TODO to IN_PROGRESS is always an explicit action
by the user (kanban drag, `odin plan --quick`, `odin run`, or API call).

Each `poll_and_execute` cycle (fired by Celery Beat every N seconds):
1. Finds IN_PROGRESS tasks with satisfied dependencies and available
   concurrency slots
2. Transitions them to EXECUTING
3. Fires `execute_single_task.delay()` for each

The normal flow:
1. `odin plan --quick` (or `odin run`) bulk-moves assigned tasks to IN_PROGRESS
2. Celery Beat fires `poll_and_execute` every 5 seconds
3. Each IN_PROGRESS task is checked:
   - Are all `depends_on` tasks DONE or REVIEW? (REVIEW = agent finished)
   - Is an assignee set?
   - Is there a concurrency slot available?
4. Ready tasks transition to EXECUTING and `execute_single_task.delay()` fires
5. After execution: EXECUTING → REVIEW (success) or EXECUTING → FAILED

### Configuration

```bash
# .env
ODIN_EXECUTION_STRATEGY=celery_dag
CELERY_BROKER_URL=redis://localhost:6379/0
DAG_EXECUTOR_MAX_CONCURRENCY=3    # Max simultaneous executions
DAG_EXECUTOR_POLL_INTERVAL=5      # Seconds between polls
```

### Running

```bash
# Start Celery worker + beat
celery -A config worker --beat --loglevel=info
```

### Context Injection

When a task has dependencies, `exec_task()` fetches comments from completed upstream
tasks and prepends them to the task description:

```
Context from upstream task a1b2c3d4 (Generate data):
Completed in 2.0s · 1,200 tokens

Generated data.csv with 100 rows.

---

[original task description]
```

- Only DONE and REVIEW tasks contribute upstream context

### Agent Communication During Execution

When TaskIt is configured, each agent gets an MCP server that provides a live communication channel back to the task board. During execution, agents can:

- **Post status updates** — progress visibility for the human watching the dashboard
- **Ask blocking questions** — the agent freezes until the human replies (zero token cost while waiting)
- **Submit proof of work** — verification evidence before marking done

This means the human doesn't experience execution as a black box. They see updates arrive in real time, answer questions when needed, and review proof as it's submitted. See [Agent Communication](communication.md) for the full philosophy and [MCP Technical Reference](mcp.md) for config details.

### Debug Comments

Each task execution posts two debug comments with attachment markers:
- `debug:effective_input` — The full prompt sent to the agent (after context injection)
- `debug:full_output` — The complete agent response

These are hidden by default in the TaskIt frontend (toggle "Show debug logs" in comments).
In mock mode, debug comments are skipped.

### Key Design Decisions

- **Polling > events**: Self-healing, no missed events, resilient to race conditions
- **REVIEW counts as satisfied**: Downstream tasks can start while upstream awaits human review
- **Block-don't-fail on dep failure**: Tasks with failed deps stay in place (never picked up) — human can fix and retry
- **TODO is a human boundary**: The DAG executor never promotes TODO tasks. IN_PROGRESS is the dispatch signal — only explicit user/odin actions cross that boundary
- **No skipping statuses**: Every transition follows the lifecycle: TODO → IN_PROGRESS → EXECUTING
- **Concurrency limit**: Prevents overwhelming the system (default 3 simultaneous)
- **Single-task execution**: Odin executes one task; Celery owns scheduling

### Known Issue: poll_and_execute double-fire

Observed: with a single Celery worker, `poll_and_execute` can fire
`execute_single_task` twice for the same task in the same poll cycle (seen in
taskit.log as duplicate `dag_executor.py:104` entries at the same timestamp).
The `select_for_update` lock should prevent this, but the race may occur when
two Beat intervals overlap or the worker processes the same schedule entry
twice. The second execution is usually a no-op (odin sets status to REVIEW
before the wrapper checks), but it wastes a subprocess and can cause tmux
session collisions if timing is unlucky. Needs investigation — potential fix is
a Redis-based distributed lock around `poll_and_execute`.

## Quick Mode End-to-End Flow

`odin plan --quick` is the one-shot path: plan + dispatch in a single command.

```
odin plan --quick specs/table_spec.md
    │
    ├─ 1. Decompose spec into sub-tasks (claude, ~35s)
    ├─ 2. Create tasks in TODO on TaskIt (with deps, assignments)
    ├─ 3. Bulk-move all assigned tasks → IN_PROGRESS
    │       └─ This is the dispatch signal for the DAG executor
    │
    ▼  (Odin's job is done — Celery takes over)
    │
    ├─ 4. Celery Beat fires poll_and_execute every 5s
    │       ├─ Finds IN_PROGRESS tasks with satisfied deps
    │       ├─ Transitions to EXECUTING (with select_for_update lock)
    │       └─ Fires execute_single_task.delay(task_id)
    │
    ├─ 5. execute_single_task runs `odin exec <id> --fg` as subprocess
    │       ├─ Odin authenticates with TaskIt
    │       ├─ Posts debug:effective_input comment
    │       ├─ Launches agent via tmux session
    │       ├─ Posts debug:full_output comment
    │       └─ Sets status to REVIEW (success) or FAILED
    │
    └─ 6. Next poll picks up newly-ready downstream tasks
```

### Execution Strategies

TaskIt supports two execution strategies (`ODIN_EXECUTION_STRATEGY` env var):

| Strategy | Trigger | Deps | Use case |
|---|---|---|---|
| `local` | Immediate Popen on IN_PROGRESS | None (fire-and-forget) | Dev, single tasks |
| `celery_dag` | No-op trigger; DAG executor polls | Full dep checking | Production, DAG workflows |

**Important**: Do NOT run `celery_dag` strategy with Celery Beat active AND `local`
strategy simultaneously. Both will fire execution for the same tasks, causing tmux
session collisions and duplicate work. Use one or the other.

### Log Files

Execution produces logs in two locations:

| Log | Location | Written by |
|---|---|---|
| Odin run log | `.odin/logs/run_<timestamp>.jsonl` | `odin plan` (planning phase) |
| DAG exec log | `taskit-backend/logs/dag_exec_<task_id>.log` | Celery `execute_single_task` |
| Local exec log | `taskit-backend/logs/odin_exec_<task_id>.log` | `local.py` strategy |
| TaskIt app log | `taskit-backend/logs/taskit.log` | Django views, DAG executor |

To debug a spec run end-to-end:

```bash
# 1. Find the run log (odin side — planning phase)
ls -lt .odin/logs/run_*.jsonl | head -1

# 2. Find which tasks were created (from the run log)
grep task_assigned .odin/logs/run_*.jsonl | tail -10

# 3. Read DAG executor logs for those tasks (taskit side — execution phase)
cat taskit/taskit-backend/logs/dag_exec_<task_id>.log

# 4. Cross-reference with taskit app log
grep "task <task_id>" taskit/taskit-backend/logs/taskit.log
```

## Full Workflow Example

```bash
# 1. Plan
odin plan sample_specs/poem_spec.md

# 2. Review
odin status
odin show a1b2

# 3. Reassign if needed
odin assign d4e5 claude

# 4. Execute tasks individually
odin exec a1b2
odin exec d4e5

# 5. Or dispatch all at once (TaskIt backend)
odin run sample_specs/poem_spec.md   # plan + queue for Celery

# 6. Watch progress
odin watch

# 7. Check results
odin status
odin show a1b2
```
