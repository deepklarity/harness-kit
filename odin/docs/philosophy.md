# Odin — Purpose & Philosophy

## What Odin Is

Odin is a **DAG-based orchestration system** where humans and AI agents collaborate on work. It decomposes high-level goals into a dependency graph of tasks, executes them in waves respecting the dependency order, and tracks everything with auditable proof.

TaskIt is the external task-management backend. Odin uses it to create, assign, and track tasks — like an app talks to Trello's API. The DAG logic, wave execution, and planning intelligence live in Odin.

## Core Tenets

### 1. Determinism
Given the same inputs, Odin should produce the same plan. The DAG structure is explicit — no implicit ordering from array position or creation time. Dependencies are declared, validated, and executed in deterministic waves.

### 2. Pareto-Driven Delegation
Assign the cheapest capable agent. Don't use a $0.15/call model when a $0.01/call model can do the job. Cost tier + quota awareness drives agent selection. The human can override, but the default is Pareto-optimal.

### 3. Cost Visibility
Every task carries quota snapshots, cost tier info, and agent reasoning. Before execution, the human sees what will run where and roughly what it costs. No surprise bills.

### 4. Proof of Work
Every task accumulates evidence: agent output, comments, duration, success/failure status supporting screenshots etc. The task board is an audit trail. When a task says "completed", there's a result string proving it.

### 5. Reflection Loops
Execution is iterative, not one-shot. Plan → execute wave 1 → review → adjust → execute wave 2 → ... The human (or a review task) can inspect intermediate results and steer before the next wave runs.

### 6. Crystal Clear Handover
Task descriptions are self-contained prompts. An agent receiving a task needs no external context — the description contains everything. This enables agent substitution: if gemini fails, reassign to codex and re-run with the same description.

### 7. Async Heavy
Tasks within a wave execute concurrently. The DAG maximizes parallelism: independent tasks run simultaneously, dependent tasks wait. Semaphore-limited (default 4) to avoid overwhelming the system.

### 8. Good Enough
Don't over-plan. The LLM's task decomposition is a starting point. The human reviews and adjusts. A slightly imperfect plan executed quickly beats a perfect plan that takes forever to create.

### 9. LLM Assumptions Noted
When the planner makes assumptions (e.g., "this task can run in parallel because files don't conflict"), those assumptions are captured in the reasoning metadata. If an assumption is wrong, the human can add a dependency before execution.

### 10. Questions to Human
When the system encounters ambiguity, it surfaces it rather than guessing silently. Two mechanisms:

- **Staged workflow** (plan → review → exec): the human sees the plan and can intervene before execution.
- **MCP questions during execution**: agents ask blocking questions via the TaskIt MCP channel. The agent freezes (zero token cost) until the human replies on the dashboard. A 10-second question beats a 90-second wrong guess that gets thrown away.

The philosophy extends beyond questions. Agents communicate as they work — status updates, progress milestones, proof of work — not just when they finish. The human should never experience execution as a black box. See [Agent Communication](communication.md) for the full model.

### 11. First Principles
No hardcoded pipeline stages. "Assembly" is just a task. "Review" is just a task. "Testing" is just a task. The only primitive is a task with dependencies. Complex workflows emerge from DAG composition.

### 12. No Slop
AI output must meet the same quality bar as human-authored code. No boilerplate comments, no filler docstrings, no "AI smell." If a reviewer can tell an AI wrote it, it's not done. Code is judged by the same standard regardless of who — or what — produced it.

### 13. Human-First Legibility
Dashboards, logs, and task boards are designed for human scanning first. Dense JSON or LLM-optimized formats are internal plumbing — what the user sees should be visual, scannable, and require minimal cognitive effort. When in doubt, optimize for the human eye, not the token window.

### 14. Platform Agnostic
Odin doesn't depend on any single AI provider. Agents are interchangeable behind a harness interface. If Claude goes down, swap to Gemini. If a new model launches, add a harness. The orchestration layer has zero provider lock-in. AI capabilities evolve monthly — the architecture assumes nothing about any provider is permanent.

### 15. Digital Twin
All work — human and AI — happens on the shared task board. There is no "shadow work" outside the system. If it's not on the board, it didn't happen. The board is the single source of truth for project state, progress, and proof.

This extends to work *in progress*, not just completed work. Through the MCP communication channel, agents post updates, ask questions, and submit proof *during* execution. The board reflects reality in real time — not a snapshot taken after everything finishes.

### 16. Modular Composition
Features compose from small, independent pieces. A harness is a module. A backend is a module. A planner is a module. Each can be swapped, tested, or extended without touching the others. Tight coupling is a bug, not a shortcut.

### 17. Taste as a Filter
LLMs generate volume easily but lack judgment. The human's role is taste — deciding what's good, what to keep, what to rework. Odin's workflow is designed to make taste-application easy: review points between waves, override mechanisms, iterative refinement. The system produces options; the human curates.

### 18. Pareto Observability
Every phase of work — planning, execution, reflection — produces a trace. These traces are captured and attached to the corresponding entity (spec or task), not discarded after terminal display. Observability follows the same Pareto principle as agent selection: capture enough to debug the common case without paying the cost of full instrumentation. Planning traces go on specs. Execution traces go on tasks. The dashboard renders both identically.

### 19. Adaptive Intelligence
The board is not just a record — it's a feedback loop. Execution data (success rates, failure reasons, rework frequency, cost per task) accumulates across runs. When Gemini Flash fails a task, Odin should be able to diagnose *why* — was context missing from the handover? Was the task too complex for the model tier? Was the prompt ambiguous? This diagnosis feeds back into future planning: adjust agent assignments, enrich task descriptions, add dependencies that were missing. Every run makes the next run smarter. The system evolves not by upgrading models, but by learning which agent fits which task shape.

## The DAG Model

### Tasks and Dependencies

Every task has an optional `depends_on` list of task IDs. This forms a directed acyclic graph (DAG):

```
task_1 (scaffold) ─┬─→ task_2 (write intro)  ──┬─→ task_5 (assemble)
                   ├─→ task_3 (write middle) ──┤
                   └─→ task_4 (write closing) ──┘
```

- **No dependencies**: Task is ready immediately (wave 1)
- **All deps completed**: Task becomes ready for the next wave
- **Any dep failed**: Task never runs (fail-fast)

### Wave Execution

Tasks execute in waves:

1. **Wave 1**: All tasks with no dependencies (or all deps already met)
2. **Wave 2**: Tasks whose wave-1 dependencies are now complete
3. **Wave N**: Continue until all tasks are done or a failure stops execution

Within each wave, tasks run concurrently (semaphore-limited to 4).

### Cycle Detection

Before execution, Odin validates the DAG using DFS-based cycle detection. Circular dependencies are caught and reported with the cycle path before any task runs.

### Backward Compatibility

Tasks without `depends_on` (empty list) have no dependencies — they're all ready in wave 1. This means old plans work identically: all tasks run concurrently in a single wave, same as the flat task-board behavior.

## The Flow

```
Requirement
    ↓
Due Diligence (read spec, understand context)
    ↓
Plan (decompose into DAG of tasks)
    ↓
Review (human inspects plan, adjusts assignments, adds/removes deps)
    ↓
Execute (wave-based: wave 1 → wave 2 → ... → done)
    ↓
Merge with Proof (assembly task combines outputs, proof = task results + comments)
```

Each step is optional/repeatable. The human can:
- Re-plan after seeing results
- Add tasks mid-execution
- Skip review and run all-in-one (`odin run`)
- Execute one task at a time for debugging

## What Odin Is NOT

- **Not a fixed pipeline.** No mandatory plan → exec → assemble. The core is a task DAG.
- **Not just for one-shot generation.** Supports iterative workflows with reflection loops.
- **Not a replacement for human judgment.** Plans are suggestions. The human overrides freely.
- **Not a task management system.** TaskIt handles task CRUD. Odin handles orchestration.

## Design Principles

1. **Everything is a task.** Assembly, review, testing — all tasks on the board with dependencies.
2. **DAG over flat list.** Dependencies are explicit, execution order is deterministic.
3. **Suggestive, not prescriptive.** Agent assignments and decompositions are defaults. Override freely.
4. **Fail fast.** A failed task stops its dependents. Don't waste compute on doomed work.
5. **Simple primitives, flexible composition.** Tasks + dependencies + waves. Complex workflows emerge from composition.
