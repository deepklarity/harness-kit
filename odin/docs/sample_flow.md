# Odin Sample Flow

A step-by-step walkthrough of the staged orchestration workflow.

## What is Odin?

Odin is a DAG-based orchestration system. It breaks a task into sub-tasks with
dependencies, assigns each to an AI agent (codex, gemini, qwen, etc.), and
executes them in dependency-respecting waves. Think of it like a Trello board
with dependency arrows: plan creates visible tasks, you review and adjust, then
trigger execution. Tasks execute in waves — independent tasks run in parallel,
dependent tasks wait for their predecessors.

TaskIt is the isolated task-management backend. Odin communicates with it
to create, assign, and track tasks — just like an app talks to Trello's API.

## The Two Ways to Use Odin

### Quick Mode (all-in-one)

```bash
odin run sample_specs/poem_spec.md
```

This does everything automatically: decompose → assign → execute in waves.
Agent assignments use sensible defaults (cheapest capable agent first).
The planner creates all necessary tasks with dependencies, including
assembly/review tasks when the project needs them.

### Staged Mode (recommended for control)

Staged mode lets you review dependencies and assignments before anything runs.

---

## Staged Workflow: Step by Step

### Step 1: Plan

```bash
odin plan sample_specs/poem_spec.md
```

Odin's base agent reads the spec and breaks it into sub-tasks with dependency
information. Each task gets a **suggested** agent assignment — the cheapest
agent that has the right capabilities. Nothing is executed yet.

The planner creates whatever tasks the project needs: implementation, assembly,
review, testing, validation, etc. It also declares dependencies: assembly tasks
depend on the tasks whose outputs they combine.

Output:
```
Plan created! 5 tasks with suggested assignments:

┌──────────────┬────────────────────────┬────────┬──────────┬──────────┬──────────────────────────────┬──────────┐
│ ID           │ Title                  │ Agent  │ Deps     │ Quota    │ Reasoning                    │ Status   │
├──────────────┼────────────────────────┼────────┼──────────┼──────────┼──────────────────────────────┼──────────┤
│ a1b2c3d4e5f6 │ Scaffold HTML          │ codex  │ -        │ 78% left │ Needs file creation capab…   │ assigned │
│ d4e5f6a1b2c3 │ Write intro paragraph  │ gemini │ a1b2c3d4 │ 85% left │ Low cost, writing capable…   │ assigned │
│ f6a1b2c3d4e5 │ Write middle paragraph │ qwen   │ a1b2c3d4 │ 92% left │ Cheapest with writing cap…   │ assigned │
│ b2c3d4e5f6a1 │ Write closing paragraph│ gemini │ a1b2c3d4 │ 85% left │ Low cost, writing capable…   │ assigned │
│ 1234abcd5678 │ Assemble into HTML     │ codex  │ d4e5f6a1,│ 78% left │ Reads all outputs, combi…   │ assigned │
│              │                        │        │ f6a1b2c3,│          │                              │          │
│              │                        │        │ b2c3d4e5 │          │                              │          │
└──────────────┴────────────────────────┴────────┴──────────┴──────────┴──────────────────────────────┴──────────┘

Assignments are suggestions. Reassign with: odin assign <task_id> <agent>
Execute with: odin exec
```

The **Deps** column shows which tasks must complete before this one can start.
The **Reasoning** column explains why each agent was chosen. The **Quota** column
shows remaining quota (requires `harness_usage_status` package; shows "-" if unavailable).

### Step 2: Review

```bash
odin status
```

See all tasks, their assignments, dependencies, and statuses.

```bash
odin show a1b2
```

Inspect a single task's full details including:
- **Deps**: What this task waits for (with current status)
- **Blocks**: What tasks are waiting for this one
- Description, result, comments, metadata

### Step 3: Reassign (optional)

Don't like a suggestion? Change it:

```bash
odin assign a1b2 codex       # Reassign first task to codex
odin assign d4e5 claude       # Reassign second task to claude
```

### Step 4: Execute

Execute all tasks (runs in background by default):

```bash
odin exec
# → Execution started (PID 12345).
# → Use 'odin logs -f' to watch, 'odin stop' to cancel.
```

Or run in foreground (blocking):

```bash
odin exec --fg
```

Or run just one task (always foreground):

```bash
odin exec a1b2
```

Tasks execute in waves:
- **Wave 1**: Tasks with no dependencies (e.g., scaffold)
- **Wave 2**: Tasks whose wave-1 deps completed (e.g., write paragraphs)
- **Wave 3**: Tasks whose wave-2 deps completed (e.g., assemble)

Within each wave, tasks run concurrently (up to 4 at a time). If any task
fails, its dependents are skipped (fail-fast).

### Step 4b: Monitor (background execution)

Watch live output:

```bash
odin logs -f               # Follow all running tasks
odin logs a1b2 -f          # Follow a specific task
odin logs debug -f         # Follow odin_detail.log (tracebacks)
```

Auto-refreshing dashboard:

```bash
odin watch                 # Refresh every 2s
```

Stop execution:

```bash
odin stop                  # Graceful SIGTERM
odin stop --force          # SIGKILL
odin stop a1b2             # Stop a specific task
```

### Step 5: Inspect

Check individual task results:

```bash
odin show a1b2      # Full output, comments, deps, blocks, metadata
odin status         # Summary table with deps and result previews
odin logs           # Structured execution logs (shows wave_started events)
odin logs a1b2      # Logs for a specific task
```

---

## DAG Examples

### Example 1: Sequential (shared file conflict)

When tasks write to the same file, they must be chained:

```
task_1 → task_2 → task_3 → task_4
```

Each task depends on the previous one. Execution: 4 waves, 1 task each.

### Example 2: Parallel with assembly (poem.html)

When tasks write to different files and an assembly step combines them:

```
task_1 (scaffold) ─┬─→ task_2 (write intro.txt)  ──┬─→ task_5 (assemble)
                   ├─→ task_3 (write middle.txt) ──┤
                   └─→ task_4 (write closing.txt) ──┘
```

Execution: 3 waves:
- Wave 1: scaffold (no deps)
- Wave 2: write intro + middle + closing (all depend on scaffold, run in parallel)
- Wave 3: assemble (depends on all 3 writes)

### Example 3: No dependencies (backward compatible)

Old-style plans without `depends_on` fields:

```
task_1 (write intro)
task_2 (write middle)     All in Wave 1 — concurrent
task_3 (write closing)
task_4 (assemble)
```

All tasks run in wave 1 concurrently — same behavior as the flat task-board.

---

## Prefix Matching

All commands that take a task_id accept a unique prefix:

```bash
odin show a1b2              # Matches a1b2c3d4e5f6
odin exec d4                # Matches d4e5f6a1b2c3
odin assign f6a1 gemini     # Matches f6a1b2c3d4e5
```

If the prefix is ambiguous (matches multiple tasks), Odin will tell you.

## Config

```bash
odin config                 # Show loaded config and agent table
```

Odin looks for config in this order:
1. `--config path/to/config.yaml` (explicit)
2. `.odin/config.yaml` (project-local)
3. `~/.odin/config.yaml` (global)
4. Built-in defaults (all agents enabled)

## Running Tests

```bash
odin test                   # Run the full test suite
odin test quick             # Fast tests only (harness availability)
odin test plan              # Test planning only
odin test e2e               # Full end-to-end test
```

## Command Reference

| Command                          | What it does                              |
|----------------------------------|-------------------------------------------|
| `odin plan <spec>`               | Decompose into DAG + suggest assignments  |
| `odin status`                    | Show task table with deps and results     |
| `odin assign <id> <agent>`       | Reassign a task                           |
| `odin exec [id]`                 | Execute tasks (background by default)     |
| `odin exec --fg`                 | Execute in foreground (blocking)          |
| `odin logs [target]`             | View run/debug logs (last 50 lines)       |
| `odin logs -f`                   | Follow all running tasks                  |
| `odin logs debug`                | View odin_detail.log (tracebacks)         |
| `odin logs -b <id>`              | Access logs via board registry            |
| `odin tail [id]`                 | (deprecated → `odin logs -f`)             |
| `odin stop [id]`                 | Stop executor or a specific task          |
| `odin watch`                     | Auto-refreshing status dashboard          |
| `odin show <id>`                 | Full task details with deps and blocks    |
| `odin run <spec>`                | All-in-one: plan + exec (foreground)      |
| `odin guide`                     | Show this walkthrough                     |
| `odin test [suite]`              | Run tests                                 |
| `odin config`                    | Show configuration                        |
