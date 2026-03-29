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
| `board-project-lifecycle/` | Board creation → `odin init` → git repo setup → worktree integration → spec planning. Covers CLI-first and UI-first paths, config loading, dual-instance (odin vs odin-dev), and common init/plan mistakes |
| `prompt-presets/` | Prompt presets in CreateTaskModal: static JSON data → backend endpoint → PresetPicker component → form auto-population. 5 categories, 27 templates for code review, UI audit, documentation, analysis, and quality process tasks |
| `notification-system/` | Full notification flow: 6 backend triggers → notify() filtering → DB bulk_create → 30s frontend poll → bell badge + sound + desktop popup. Also covers Web Push path (VAPID → service worker → push delivery) |
| `task-preset-tdd-enforcement/` | **PROPOSED** — Task presets (test, implement, scaffold, integrate, verify, standalone) with context isolation and verification gates. Embeds TDD philosophy into odin plan/exec so projects built by odin inherit fail-first testing |
| `git-worktree-isolation/` | Git worktree per task, spec branch per spec. Merge deferred to reflection pass (not task completion). File-locked merge serialization. Spec = single PR to main. Covers branch model, worktree lifecycle (spec + task worktrees), merge timing, conflict handling, lazy git init, config, CLI commands, and metadata tracking |

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
- **Fresh install — where to start?** → `board-project-lifecycle/FLOW.md` (Flow 1: CLI-first)
- **Spec branch shows `—` in UI?** → `board-project-lifecycle/DEBUG.md` (no git repo at plan time)
- **Used wrong odin binary (stable vs dev)?** → `board-project-lifecycle/DEBUG.md` (Common mistakes table)
- **odin init didn't create git repo?** → `board-project-lifecycle/DEBUG.md` (check `.git/` exists)
- **Config missing agents after YAML load?** → `board-project-lifecycle/DETAILS.md` §6 (config loading)
- **Notifications not appearing?** → `notification-system/DEBUG.md`
- **Bell badge stuck at 0?** → `notification-system/DEBUG.md`
- **Push notifications not delivering?** → `notification-system/DEBUG.md`
- **Sound not playing on notification?** → `notification-system/DEBUG.md`
- **Presets not showing in CreateTaskModal?** → `prompt-presets/DEBUG.md`
- **New category color not appearing?** → `prompt-presets/DEBUG.md` (CATEGORY_COLORS map)
- **How to add a new preset?** → `prompt-presets/DEBUG.md` (Adding new presets section)
- **Planner not creating test→implement pairs?** → `task-preset-tdd-enforcement/DEBUG.md`
- **Test preset agent has implementation context (shouldn't)?** → `task-preset-tdd-enforcement/DEBUG.md` (context isolation leaks)
- **Verification gate rejecting valid work?** → `task-preset-tdd-enforcement/DEBUG.md` (gate parsing)
- **How do task presets work?** → `task-preset-tdd-enforcement/FLOW.md` (proposed design)
- **Task running in project root instead of worktree?** → `git-worktree-isolation/DEBUG.md` (check `worktree_status` metadata)
- **Merge conflict on task completion?** → `git-worktree-isolation/DEBUG.md` (merge happens on reflection pass, not completion)
- **Downstream task missing upstream work?** → `git-worktree-isolation/DEBUG.md` (check spec branch for merge commit)
- **Spec branch not created?** → `git-worktree-isolation/DEBUG.md` (check `_ensure_git_repo` and base_branch config)
- **How does worktree isolation work?** → `git-worktree-isolation/FLOW.md`
- **Merge never attempted after task succeeded?** → `git-worktree-isolation/DEBUG.md` (merge deferred to REVIEW → TESTING)
- **Worktree config options?** → `git-worktree-isolation/DETAILS.md` §2 (Configuration)
- **odin worktree list/status/clean?** → `git-worktree-isolation/DETAILS.md` §10 (CLI Commands)
