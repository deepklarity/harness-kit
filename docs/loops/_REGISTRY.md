# Loop Registry

This registry names the system loops that appear in the current codebase and breadcrumb traces. Each entry lists the trigger, the concrete state carrier, closure criteria, a measurable closure metric, and the default owner.

State locations cite existing repository files that declare the table, data file, or trace artifact used by the loop.

## plan -> review -> exec

- Trigger: A human or UI PTY runs `odin plan <spec>` / `odin plan --direct <spec>`, reviews the generated DAG, then moves ready tasks to `IN_PROGRESS` or runs `odin exec`.
- State location: tables `specs`, `tasks`, `task_comments`, and `task_history` declared in `taskit/taskit-backend/tasks/models.py`; planning flow traced in `docs/breadcrumb_analysis/planning-flow/03-orchestrator/FLOW.md` and execution flow in `docs/breadcrumb_analysis/spec-task-lifecycle/02-execute-and-dispatch/FLOW.md`.
- Exit criteria: The spec has a persisted plan, every created task has a terminal or waiting status consistent with dependencies, and completed tasks have proof comments.
- Closure metric: 100% of spec tasks are in `TESTING`, `DONE`, or `FAILED`, and every `TESTING`/`DONE` task has at least 1 `proof` comment.
- Owner: human

## DAG dispatch polling

- Trigger: Celery beat invokes the DAG executor poll cycle for tasks in `IN_PROGRESS`.
- State location: tables `tasks` and `task_history` declared in `taskit/taskit-backend/tasks/models.py`; dispatch details traced in `docs/breadcrumb_analysis/spec-task-lifecycle/02-execute-and-dispatch/FLOW.md`.
- Exit criteria: Each ready task is either moved to `EXECUTING`, left waiting on dependencies, or blocked by a failed dependency.
- Closure metric: 0 ready `IN_PROGRESS` tasks remain unclaimed after a poll when executor capacity is available.
- Owner: scheduled

## TDD wave

- Trigger: A feature task is decomposed or executed using the test-first wave discipline from the root guidance and proposed task presets.
- State location: proposed preset state is `task.metadata["preset"]` on table `tasks` declared in `taskit/taskit-backend/tasks/models.py`; target flow is documented in `docs/breadcrumb_analysis/task-preset-tdd-enforcement/FLOW.md` and `docs/breadcrumb_analysis/task-preset-tdd-enforcement/DETAILS.md`.
- Exit criteria: The test wave lands meaningful failing tests before implementation, then the implementation wave makes those same tests pass.
- Closure metric: Wave 1 records a failing test command with exit code != 0; Wave 2 records the same test command with exit code 0 plus 1 successful build/typecheck command.
- Owner: agent

## reflection -> retry

- Trigger: A task transitions from `EXECUTING` to `REVIEW`, or a human manually requests reflection.
- State location: tables `reflection_reports`, `tasks`, `task_comments`, and `task_history` declared in `taskit/taskit-backend/tasks/models.py`; loop traced in `docs/breadcrumb_analysis/spec-task-lifecycle/03-reflection-loop/FLOW.md`.
- Exit criteria: Reflection verdict `PASS` moves the task to `TESTING`; repeated `NEEDS_WORK` requeues the task until the retry cap; terminal failure moves it to `FAILED`.
- Closure metric: 1 `PASS` verdict closes the loop successfully, or 3 completed non-passing reflection reports close it as `FAILED`.
- Owner: scheduled

## worktree merge

- Trigger: Reflection passes and a task moves from `REVIEW` to `TESTING` with a task branch in metadata.
- State location: task worktree/merge fields live in `tasks.metadata` declared in `taskit/taskit-backend/tasks/models.py`; merge behavior and lock file naming are traced in `docs/breadcrumb_analysis/git-worktree-isolation/FLOW.md`.
- Exit criteria: The task branch is merged into the spec branch, or the conflict/error is recorded and the branch is preserved.
- Closure metric: Exactly 1 terminal `merge_status` value per passed task: `merged`, `conflict`, or `error`.
- Owner: scheduled

## spec finalization

- Trigger: A human runs `odin spec finalize <spec_id>` or auto-finalize is enabled after all spec tasks reach terminal state.
- State location: spec metadata lives in table `specs` declared in `taskit/taskit-backend/tasks/models.py`; finalization flow is traced in `docs/breadcrumb_analysis/git-worktree-isolation/FLOW.md`.
- Exit criteria: Spec worktrees are cleaned and the spec branch has a PR URL or an explicit no-PR finalization result.
- Closure metric: 1 terminal finalization record per spec: either `metadata.pr_url` is present or `metadata.finalized_at` is present with no PR.
- Owner: human

## proof submission

- Trigger: An executing agent calls `taskit_add_comment(comment_type="proof")`, optionally with screenshot paths.
- State location: tables `task_comments` and `comment_attachments` declared in `taskit/taskit-backend/tasks/models.py`; proof flow traced in `docs/breadcrumb_analysis/task-proof-submission/FLOW.md`.
- Exit criteria: The proof comment is visible on the task and any screenshots are uploaded and attached.
- Closure metric: At least 1 `proof` comment exists per completed task; browser/mobile deliverables include at least 1 screenshot attachment unless proof states why capture failed.
- Owner: agent

## question -> reply

- Trigger: An agent posts `taskit_add_comment(comment_type="question")` because execution needs human input.
- State location: `task_comments.comment_type` stores `question` and `reply` records in table `task_comments` declared in `taskit/taskit-backend/tasks/models.py`; execution context handling is traced in `docs/breadcrumb_analysis/spec-task-lifecycle/02-execute-and-dispatch/FLOW.md`.
- Exit criteria: A human reply is attached and the blocked task resumes, or the task is explicitly failed/canceled.
- Closure metric: 1 human `reply` comment per blocking `question`, or 1 terminal task status if the question is abandoned.
- Owner: human

## scheduled task release

- Trigger: A one-time or recurring task schedule reaches `next_run_at_utc`.
- State location: tables `task_schedules` and `task_schedule_runs` declared in `taskit/taskit-backend/tasks/models.py`.
- Exit criteria: The due occurrence materializes as a task, is skipped for overlap, or reaches a terminal schedule-run status.
- Closure metric: 1 `task_schedule_runs` row per due occurrence, with 0 duplicate rows for the same `(schedule, scheduled_for_utc)` pair.
- Owner: scheduled

## notification poll

- Trigger: Backend task/spec/comment events create notifications; the frontend polls for updates on its interval.
- State location: table `notifications` declared in `taskit/taskit-backend/tasks/models.py`; notification flow traced in `docs/breadcrumb_analysis/notification-system/FLOW.md`.
- Exit criteria: The user sees the unread notification state, or the notification is marked read.
- Closure metric: Frontend unread count matches the unread `notifications` rows for the user within 30 seconds.
- Owner: scheduled

## trace ingestion

- Trigger: Harness execution completes and execution output/trace metadata is recorded.
- State location: execution metrics are stored in `tasks.metadata` and `task_comments` declared in `taskit/taskit-backend/tasks/models.py`; trace flow is documented in `docs/breadcrumb_analysis/trace-data-pipeline/FLOW.md`.
- Exit criteria: Attempt duration, token, cost, and summary data are available to the backend/frontend when the harness emitted them.
- Closure metric: 100% of completed attempts have a duration metric; token and cost metrics are present for every harness output that includes usage data.
- Owner: scheduled

## compounding (/hk-compound)

- Trigger: A human or agent invokes `/hk-compound` or uses the Knowledge Capture task preset to turn a reusable learning into documentation.
- State location: the Knowledge Capture preset is stored in `taskit/taskit-backend/data/task_presets.json`; the compounded-learning index exists at `docs/_INDEX.md`.
- Exit criteria: A reusable learning is created or an existing one is updated with evidence, applicability, and searchable tags.
- Closure metric: 1 durable documentation artifact is created or updated, and it contains at least 3 tags or equivalent index/search metadata.
- Owner: agent

## slop audit

- Trigger: A human or agent runs a slop/codebase hygiene audit preset.
- State location: slop audit presets are stored in `taskit/taskit-backend/data/task_presets.json`; audit reports and proof are carried by table `task_comments` declared in `taskit/taskit-backend/tasks/models.py`.
- Exit criteria: Findings are reported with evidence, severity/priority, and false-positive checks.
- Closure metric: 1 report includes P0-P4 or severity-ranked counts, and 0 P0-P2 findings are left without a cited repository location.
- Owner: agent

## autonomy audit

- Trigger: A human or agent runs the loop/autonomy audit preset against a target area.
- State location: the autonomy audit preset is stored in `taskit/taskit-backend/data/task_presets.json`; resulting audit proof is carried by table `task_comments` declared in `taskit/taskit-backend/tasks/models.py`.
- Exit criteria: The target area is rated across discover, diagnose, hypothesize, fix, verify, and document stages with concrete gaps and recommendations.
- Closure metric: All 6 stages have GREEN/YELLOW/RED ratings and the report lists 3-5 prioritized recommendations.
- Owner: agent

## benchmark (planned)

- Trigger: A human or scheduled runner executes the harness speed probe.
- State location: benchmark runner is `tests/benchmarks/harness_speed.py`; benchmark history is `tests/benchmarks/speed_log.csv`.
- Exit criteria: Each configured harness is attempted and its wall time, exit code, output size, usage, cost, and error fields are appended.
- Closure metric: 1 CSV row per configured harness per run ID, with non-empty `wall_ms` and `exit_code` fields.
- Owner: scheduled
