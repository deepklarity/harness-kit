# Breadcrumb Analyses

Workflow traces for debugging. Each folder traces a specific flow end-to-end with FLOW.md (high-level), DETAILS.md (file/function level), and DEBUG.md (logs, search patterns, commands).

## Flows

| Folder | What it traces |
|--------|---------------|
| `odin-plan-mode/` | `odin plan` → spec archive creation, LLM planning dispatch, task creation with dependency resolution |
| `spec-to-task-planning/` | Spec file → task board: archive persistence, quota fetching, agent discovery, plan prompt building |
| `spec-task-lifecycle/` | Post-planning execution and reflection. Split into sub-flows: DAG dispatch (02) and reflection loop (03) |
| `harness-isolation-testing/` | Agent harness testing: CLI command construction, MCP config generation, token extraction, streaming |
| `task-proof-submission/` | Agent proof output → screenshots → TaskIt backend → frontend rendering |
| `intelligent-agent-routing/` | Task routing: tier-based distribution, premium model upgrade, routing reasoning, config visibility in UI |
| `trace-data-pipeline/` | Trace capture (harness JSONL) -> backend ingestion -> cost/token computation -> frontend TraceViewer. Covers all 6 harness formats, snapshot golden data, and regression testing gaps |
| `board-project-lifecycle/` | Board creation → project directory linkage → spec/task execution. Current flow + PROPOSED.md for board-as-project refactor (working_dir on Board, odin init via API, UI-first onboarding) |
| `prompt-presets/` | Prompt presets in CreateTaskModal: static JSON data → backend endpoint → PresetPicker component → form auto-population. 5 categories, 27 templates for code review, UI audit, documentation, analysis, and quality process tasks |
| `task-preset-tdd-enforcement/` | **PROPOSED** — Task presets (test, implement, scaffold, integrate, verify, standalone) with context isolation and verification gates. Embeds TDD philosophy into odin plan/exec so projects built by odin inherit fail-first testing |

## Quick navigation

- **Task stuck in IN_PROGRESS?** → `spec-task-lifecycle/02-execute-and-dispatch/DEBUG.md`
- **Reflection didn't advance status?** → `spec-task-lifecycle/03-reflection-loop/DEBUG.md`
- **Agent produced no output?** → `spec-task-lifecycle/02-execute-and-dispatch/DEBUG.md` (check dual dep check)
- **Planning failed or created wrong tasks?** → `odin-plan-mode/DEBUG.md`
- **Screenshots not showing in UI?** → `task-proof-submission/DEBUG.md`
- **Task retrying same agent after quota failure?** → `spec-task-lifecycle/03-reflection-loop/DEBUG.md`
- **All tasks assigned to one agent?** → `intelligent-agent-routing/DEBUG.md`
- **Routing reasoning not showing in UI?** → `intelligent-agent-routing/DEBUG.md`
- **Token count shows 0 or "---"?** → `trace-data-pipeline/DEBUG.md`
- **Cost mismatch between backend and UI?** → `trace-data-pipeline/DEBUG.md` (field mapping cheat sheet)
- **TraceViewer shows unknown format?** → `trace-data-pipeline/DEBUG.md`
- **Trace file empty or missing?** → `trace-data-pipeline/DEBUG.md`
- **Snapshot data stale or needs re-capture?** → `trace-data-pipeline/DEBUG.md` (snapshot commands)
- **Board has no working directory?** → `board-project-lifecycle/DEBUG.md`
- **odin exec fails with "no such directory"?** → `board-project-lifecycle/DEBUG.md`
- **board_id mismatch between CLI config and UI?** → `board-project-lifecycle/DEBUG.md`
- **Fresh install — where to start?** → `board-project-lifecycle/PROPOSED.md` (onboarding flow)
- **Presets not showing in CreateTaskModal?** → `prompt-presets/DEBUG.md`
- **New category color not appearing?** → `prompt-presets/DEBUG.md` (CATEGORY_COLORS map)
- **How to add a new preset?** → `prompt-presets/DEBUG.md` (Adding new presets section)
- **Planner not creating test→implement pairs?** → `task-preset-tdd-enforcement/DEBUG.md`
- **Test preset agent has implementation context (shouldn't)?** → `task-preset-tdd-enforcement/DEBUG.md` (context isolation leaks)
- **Verification gate rejecting valid work?** → `task-preset-tdd-enforcement/DEBUG.md` (gate parsing)
- **How do task presets work?** → `task-preset-tdd-enforcement/FLOW.md` (proposed design)
