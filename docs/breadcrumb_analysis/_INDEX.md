# Breadcrumb Analyses

Workflow traces for debugging. Each folder traces a specific flow end-to-end with FLOW.md (high-level), DETAILS.md (file/function level), and DEBUG.md (logs, search patterns, commands).

## Flows

| Folder | What it traces |
|--------|---------------|
| `planning-flow/` | End-to-end planning: UI spec creation → WebSocket/PTY → `odin plan --direct` → orchestrator (prompt, agent dispatch, plan parse, task creation). Merges former `odin-plan-mode/`, `spec-to-task-planning/`, `ui-planning-terminal/`. Split into 01-spec-creation, 02-pty-session, 03-orchestrator. |
| `spec-task-lifecycle/` | Post-planning execution and reflection. Split into sub-flows: DAG dispatch (02) and reflection loop (03) |
| `task-state-machine-celery-automation/` | The full task state machine in one place: all 8 statuses, operator-driven vs celery-automatic edges, stale recovery, model escalation, merge-gated TESTING promotion, spec finalize → DONE, and the operator rules (no raw-ORM transitions, no worktree edits). DEBUG.md is the <60s fast-first-checks runbook + watch_task.sh reference. Cross-references `spec-task-lifecycle/` for per-hop detail |
| `harness-isolation-testing/` | Agent harness testing: CLI command construction, MCP config generation, token extraction, streaming |
| `task-proof-submission/` | Agent proof output → screenshots → TaskIt backend → frontend rendering |
| `intelligent-agent-routing/` | Task routing: tier-based distribution, premium model upgrade, routing reasoning, config visibility in UI |
| `board-agent-roster/` | Which agents a board's planner may use: BoardMembership as the enabled flag, toggle endpoints, how the roster reaches the planning prompt, disable-unassigns gotcha |
| `trace-data-pipeline/` | Trace capture (harness JSONL) → backend ingestion → cost/token computation → frontend TraceViewer. Covers all 6 harness formats, snapshot golden data, and regression testing gaps |
| `board-project-lifecycle/` | Board creation → `odin init` → git repo setup → worktree integration → spec planning. Covers CLI-first and UI-first paths, config loading, dual-instance (odin vs odin-dev), and common init/plan mistakes |
| `prompt-presets/` | Prompt presets in CreateTaskModal: static JSON data → backend endpoint → PresetPicker component → form auto-population |
| `notification-system/` | Full notification flow: 6 backend triggers → notify() filtering → DB bulk_create → 30s frontend poll → bell badge + sound + desktop popup. Also covers Web Push path |
| `task-preset-tdd-enforcement/` | **PROPOSED** — Task presets with context isolation and verification gates. Embeds TDD philosophy into odin plan/exec |
| `git-worktree-isolation/` | Git worktree per task, spec branch per spec. Merge deferred to reflection pass. File-locked merge serialization. Covers branch model, worktree lifecycle, merge timing, conflict handling, config, CLI commands |
| `task-execution-worktree-lifecycle/` | End-to-end spine: dispatch → worktree create → microVM (microsandbox) boot → auto-commit → merge → cleanup. Focuses on the glue plus two recurring pitfalls: the **shadow `.odin/config`** trap and the **run-scoped sandbox cleanup**. Cross-refs `git-worktree-isolation/` and `spec-task-lifecycle/02-execute-and-dispatch/` for depth |
| `quota-failover-reassignment/` | Quota/429 failover as merged in **W3.11**: detection → ground-truth check via `harness_usage_status` (95% threshold) → 429-with-headroom backs off the *same* agent, genuine exhaustion reassigns to a same-cost-tier fallback. Covers the `harness_usage_status` CLI interface |
| `task-liveness-retry/` | The run lifecycle the dispatcher owns: dispatch gates (concurrency/assignee/deps/memory-budget/worktree + run-token fence + W12.7 trace rotation), the three liveness checks (heartbeat-lease vs trace-progress vs error-loop, plus legacy adopt-if-alive), the shared reap tail, and the auto-redispatch + failure-policy retry path. DEBUG.md's worked example is the W12.7 stale-trace reap fix: a dead run's leftover trace killed every retry; the fix is dispatch-time rotation in `poll_and_execute` + the age-vs-run-start guard in `_run_progress_mtime`. |

## Quick navigation

### Planning (spec creation → task board)
- **Planning failed or created wrong tasks?** → `planning-flow/03-orchestrator/DEBUG.md`
- **Planning terminal not connecting?** → `planning-flow/02-pty-session/DEBUG.md`
- **Spec stuck in `planning` status?** → `planning-flow/02-pty-session/DEBUG.md`
- **Tasks not created after planning completes?** → `planning-flow/02-pty-session/DEBUG.md` (_complete_planning sibling lookup)
- **Terminal reconnect shows no output?** → `planning-flow/02-pty-session/DEBUG.md` (session buffer)
- **How does CreateSpecModal build the odin command?** → `planning-flow/01-spec-creation/FLOW.md`
- **Spec created but status is not `planning`?** → `planning-flow/01-spec-creation/DEBUG.md`
- **Claude Code opens but kickoff not sent?** → `planning-flow/02-pty-session/DEBUG.md` (auto-kickoff detection)
- **Plan written but Claude Code doesn't exit?** → `planning-flow/02-pty-session/DEBUG.md` (auto-exit detection)
- **tmux status bar visible in terminal?** → `planning-flow/02-pty-session/DEBUG.md` (--direct flag missing)
- **Planning agent output lost?** → `planning-flow/03-orchestrator/DEBUG.md` (known trace gap)

### Execution & reflection
- **Task failed or looks stuck — first 60 seconds?** → `task-state-machine-celery-automation/DEBUG.md` (fast-first-checks runbook)
- **Which transitions am I allowed to make vs celery?** → `task-state-machine-celery-automation/FLOW.md` (edge ownership table)
- **Rules: raw ORM writes, worktree edits, dispatch protocol?** → `task-state-machine-celery-automation/DETAILS.md` §12
- **watch_task.sh exit signals / when to use it?** → `task-state-machine-celery-automation/DEBUG.md`
- **Task flipped FAILED ~2 min after dispatch?** → `task-state-machine-celery-automation/DEBUG.md` (stale recovery)
- **PASS verdict but task still in REVIEW?** → `task-state-machine-celery-automation/DETAILS.md` §10 (merge gate)
- **Task changed model/agent by itself?** → `task-state-machine-celery-automation/DETAILS.md` §7 (escalation) and §9 (quota reassignment)
- **Task stuck in IN_PROGRESS?** → `spec-task-lifecycle/02-execute-and-dispatch/DEBUG.md`
- **Reflection didn't advance status?** → `spec-task-lifecycle/03-reflection-loop/DEBUG.md`
- **Agent produced no output?** → `spec-task-lifecycle/02-execute-and-dispatch/DEBUG.md` (check dual dep check)
- **What reviewer model does auto-reflection use?** → `spec-task-lifecycle/03-reflection-loop/FLOW.md` (dynamic; no fixed haiku/sonnet default)
- **PASS verdict but task didn't reach TESTING?** → `spec-task-lifecycle/03-reflection-loop/DETAILS.md` §6 (PASS merges first, then advances)
- **Reviewer seeing garbled/truncated context?** → `spec-task-lifecycle/03-reflection-loop/DETAILS.md` §4–5 (comment assembly + truncation/laundering guards)
- **FAIL verdict — is it advisory?** → `spec-task-lifecycle/03-reflection-loop/FLOW.md` (FAIL shares the NEEDS_WORK retry/fail path)

### Task liveness, reaping & retry
- **Run died ~3 min after dispatch?** → `task-liveness-retry/DEBUG.md` (lease reap — dead supervisor)
- **Run killed ~10 min in while the process was alive?** → `task-liveness-retry/DEBUG.md` (progress reap — live supervisor, dead agent)
- **Run killed while the trace kept growing?** → `task-liveness-retry/DEBUG.md` (error-loop reap)
- **Every retry dies the instant it starts?** → `task-liveness-retry/DEBUG.md` (stale-trace reap worked example; W12.7 fix = dispatch-time rotation + age-vs-run-start guard)
- **How are runs watched / what are the three liveness checks?** → `task-liveness-retry/DETAILS.md` §4
- **Worker restarted — is my run adopted or re-dispatched?** → `task-liveness-retry/DETAILS.md` §4d (adopt-if-alive)
- **Which failure classes auto-retry, and to the cap?** → `task-liveness-retry/DETAILS.md` §7–8 (policy table + redispatch)
- **Trace file path / "which file is *the* trace?"** → `task-liveness-retry/DETAILS.md` §5 (session_resolver)
- **What does dispatch-time trace rotation do?** → `task-liveness-retry/DETAILS.md` §1a (W12.7 structural fix)

### Task execution + worktree lifecycle
- **Worktree created inside another worktree / shadow `.odin/config`?** → `task-execution-worktree-lifecycle/DEBUG.md` (root manager at `board.working_dir`)
- **microVM left running / orphan sandboxes?** → `task-execution-worktree-lifecycle/DEBUG.md` (run-scoped cleanup vs `odin gc --prune`)
- **Full dispatch → merge → cleanup path?** → `task-execution-worktree-lifecycle/FLOW.md`
- **Downstream task started while upstream was in REVIEW?** → `task-execution-worktree-lifecycle/DEBUG.md` (REVIEW is now a completed dependency)
- **Worktree failed — did the task run in project root?** → `task-execution-worktree-lifecycle/FLOW.md` (now FAILED unless `allow_project_root_execution`)

### Quota failover + provider reassignment
- **Task retrying same agent after a 429?** → `quota-failover-reassignment/DEBUG.md` (often correct — headroom backoff, not a miss)
- **Provider switched when it shouldn't have (or vice-versa)?** → `quota-failover-reassignment/FLOW.md` (ground-truth 95% threshold)
- **Reassign comment says "unverified"?** → `quota-failover-reassignment/DEBUG.md` (`harness_usage_status` not importable)
- **How is the ground-truth usage % obtained?** → `quota-failover-reassignment/DETAILS.md` §7 (`harness_usage_status` CLI/library)

### Routing & traces
- **All tasks assigned to one agent?** → `intelligent-agent-routing/DEBUG.md`
- **Routing reasoning not showing in UI?** → `intelligent-agent-routing/DEBUG.md`
- **Token count shows 0 or "---"?** → `trace-data-pipeline/DEBUG.md`
- **Cost mismatch between backend and UI?** → `trace-data-pipeline/DEBUG.md` (field mapping cheat sheet)
- **TraceViewer shows unknown format?** → `trace-data-pipeline/DEBUG.md`
- **Trace file empty or missing?** → `trace-data-pipeline/DEBUG.md`
- **Snapshot data stale or needs re-capture?** → `trace-data-pipeline/DEBUG.md` (snapshot commands)
- **Screenshots not showing in UI?** → `task-proof-submission/DEBUG.md`

### Board & setup
- **Board has no working directory?** → `board-project-lifecycle/DEBUG.md`
- **odin exec fails with "no such directory"?** → `board-project-lifecycle/DEBUG.md`
- **board_id mismatch between CLI config and UI?** → `board-project-lifecycle/DEBUG.md`
- **Fresh install — where to start?** → `board-project-lifecycle/FLOW.md` (Flow 1: CLI-first)
- **Spec branch shows `—` in UI?** → `board-project-lifecycle/DEBUG.md` (no git repo at plan time)
- **Used wrong odin binary (stable vs dev)?** → `board-project-lifecycle/DEBUG.md` (Common mistakes table)
- **After merging odin changes, backend tests error with `TypeError ... unexpected keyword argument`?** → `taskit-backend/tests/test_odin_import_skew.py` (odin version skew: tests imported the main-checkout odin, not the worktree's — `scripts/verify.sh` pins `odin/src` to PYTHONPATH; the guard fails loudly naming the fix)
- **odin init didn't create git repo?** → `board-project-lifecycle/DEBUG.md` (check `.git/` exists)
- **Config missing agents after YAML load?** → `board-project-lifecycle/DETAILS.md` §6 (config loading)

### Features
- **Notifications not appearing?** → `notification-system/DEBUG.md`
- **Bell badge stuck at 0?** → `notification-system/DEBUG.md`
- **Push notifications not delivering?** → `notification-system/DEBUG.md`
- **Sound not playing on notification?** → `notification-system/DEBUG.md`
- **Presets not showing in CreateTaskModal?** → `prompt-presets/DEBUG.md`
- **New category color not appearing?** → `prompt-presets/DEBUG.md` (CATEGORY_COLORS map)
- **How to add a new preset?** → `prompt-presets/DEBUG.md` (Adding new presets section)
- **Planner not creating test→implement pairs?** → `task-preset-tdd-enforcement/DEBUG.md`

### Git worktree isolation
- **Task running in project root instead of worktree?** → `git-worktree-isolation/DEBUG.md`
- **Merge conflict on task completion?** → `git-worktree-isolation/DEBUG.md` (merge happens on reflection pass)
- **Downstream task missing upstream work?** → `git-worktree-isolation/DEBUG.md` (check spec branch)
- **Spec branch not created?** → `git-worktree-isolation/DEBUG.md`
- **How does worktree isolation work?** → `git-worktree-isolation/FLOW.md`
- **Merge never attempted after task succeeded?** → `git-worktree-isolation/DEBUG.md` (merge deferred to REVIEW → TESTING)
- **Worktree config options?** → `git-worktree-isolation/DETAILS.md` §2
- **odin worktree list/status/clean?** → `git-worktree-isolation/DETAILS.md` §10
