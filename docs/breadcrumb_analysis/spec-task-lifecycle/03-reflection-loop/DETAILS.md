# Reflection Loop — Detailed Trace

Line numbers verified against the spec branch. Backend paths under `taskit/taskit-backend/`, orchestrator/reviewer paths under `odin/`.

## 1. Manual reflection trigger

**File**: `tasks/views.py`
**Function**: `TaskViewSet.reflect()` — line ~3501 (`@action url_path="reflect"` at ~3500)
Gates on `task.status in (REVIEW, DONE, FAILED)` (~3504), builds the report (from the serializer, or a forced provider), dispatches `execute_reflection.delay(report.id)` (~3532).
Data out: `ReflectionReport(status=PENDING)`, 202 Accepted.

## 2. Auto-reflection trigger

**File**: `tasks/views.py`
**Function**: `_trigger_auto_reflection(task)` — line ~277
Called from **three** sites: `dag_executor.py :: execute_single_task()` (~607), `views.py` TaskViewSet update (~2862), `views.py` execution_result endpoint (~3397) — each when a task moves to REVIEW.
- Duplicate guard: skips if a PENDING/RUNNING reflection already exists.
- **Reviewer default is dynamic** — `_reflection_reviewer_defaults(board=task.board)` (~300) → `_find_first_available_reviewer()` (~123): available board-member agent from `REFLECTION_PREFERRED_AGENTS = ["gemini","codex","claude"]` (~109), randomized among the enabled set (`random.shuffle` ~194), each on its default/first `available_models` entry (~200). Board `reflection_model` or a forced-provider selection overrides (~175). `is_active` filter (~142) keeps retired providers out. **No `haiku` / `claude-sonnet-4-5-20250929` default exists in this path.**
- Early exits that merge directly (no reviewer): `skip_reflection` (~283), "no reviewer available" (~301).
- `requested_by="system@taskit"` distinguishes auto from manual.

## 3. Celery reflection execution

**File**: `tasks/dag_executor.py`
**Function**: `execute_reflection(report_id)` — line ~1203
- Guard: report must be PENDING (~1219); marks RUNNING (~1226).
- Subprocess: `odin reflect <task_id> --report-id <id> --model <model> --agent <agent>` (~1234), run with `cwd=resolve_working_dir(task)` (~1232, 1256).
- **Timeout = `DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS`, default 1800s** (~1249) — *not* 300s.
- Fallback if odin didn't PATCH (still RUNNING): FAILED on non-zero exit, else COMPLETED (~1274).

## 4. Odin reflection orchestrator + comment-assembly path

**File**: `odin/src/odin/reflection.py`
**Function**: `reflect_task(task_id, report_id, model, agent, …)` — line ~553
- Reviewer invoked via `harness.execute(prompt, context)` (~811, `get_harness` at ~806) with `read_only_workspace=True` (~800), `validate_status=False` (~794).
- Prompt built by `build_reflection_prompt(task_context)` (~85, called ~759); report parsed by `parse_reflection_report(clean_output)` (~409, called ~857).
- **Verdict extraction**: `parse_reflection_report` reads the `### Verdict` section, regex `^(PASS|NEEDS_WORK|FAIL)\b` (~482); unrecognized → `NEEDS_WORK` (~494); last-resort whole-output scan (~500); truly none → `verdict="ERROR"` (~516, the reviewer-failure sentinel). Values (convention, not a Django `choices=` enum): **PASS / NEEDS_WORK / FAIL**.

**The comment / context assembly** (what the reviewer actually sees), all in `reflect_task`:
- `GET /tasks/:id/detail/` → `task_data` (~620); comments list (~627).
- **Checkpoint detection** splits prior attempts from the current one: finds the latest `reflection`/`summary` comment (~632) and inserts a `--- CURRENT ATTEMPT (evaluate this) ---` separator (~646, ~667).
- **Per-comment formatting**: `_format_comment_for_prompt(comment)` (~72) → clean + truncate (§5).
- **Skip filters**: `Effective input` echoes (~657) and `{"type":"system"` hook JSON (~660) are dropped.
- **Proof / screenshots**: `_extract_screenshot_urls()` (~358, called ~675) → `_download_screenshots()` (~374, called ~735); worktree staging `_stage_reflection_screenshots_for_workspace()` (~332).
- **Execution output**: `raw_execution = metadata["full_output"]` → `extract_text_from_stream()` (~683).
- Assembled `task_context` dict (~703); prompt built (~759); stored via `_patch_report({"status":"RUNNING","assembled_prompt":prompt})` (~764).

## 5. Truncation / laundering / sanitization guards

The assembled context and the reviewer's raw output both pass through cleaning guards so raw tool-stream noise, provider 429 dumps, and stutter never reach the reviewer or the stored report. Module constants: `_COMMENT_CHAR_LIMIT = 2000` (~25), `_NOISY_COMMENT_TYPES` (~27), `_NOISE_PATTERNS` (~28).

| Guard | `reflection.py` location | What it does |
|-------|--------------------------|--------------|
| `_clean_comment_content()` | ~50 | Strips raw JSON lines and tool-stream noise patterns from non-proof comments |
| `_truncate_comment_content()` | ~63 | Caps each noisy comment at 2000 chars, appends a `[truncated …]` marker |
| `_format_comment_for_prompt()` | ~72 | Applies clean+truncate only to `_NOISY_COMMENT_TYPES`; proof comments pass through un-truncated (~79) |
| Inline skip filters | ~657–661 | Drop "Effective input" echoes and system-hook JSON comments before assembly |
| Execution-output cap | ~687 | `raw_execution[:5000]` fallback when the stream can't be parsed |
| `_strip_odin_envelopes()` | ~267 | Removes `-------ODIN-STATUS-------` envelope framing from reviewer output |
| `_sanitize_reflection_output()` | ~286 | Strips ANSI codes, seeks to the first report header (dropping provider retry/429 dumps), normalizes headers, drops stack-trace/noise-prefixed lines |
| `_deduplicate_summary()` | ~247 | Removes stuttered/repeated verdict-summary lines |
| PATCH output caps | ~846, ~868 | `clean_output[:10000]` and `_truncate_trace(raw_jsonl, 50000)` (imported from orchestrator, ~21) |

There is no function literally named "launder" — the laundering role is served by `_sanitize_reflection_output`, `_clean_comment_content`, and `_strip_odin_envelopes`.

## 6. Report result processing + status transitions

**File**: `tasks/views.py`
**Function**: `ReflectionReportViewSet.partial_update()` — line ~3566 (odin PATCHes here)
- Posts a `TaskComment(type=REFLECTION)` on COMPLETED + summary (~3589): `**Reflection: <VERDICT>**\n\n<verdict_summary>`, attachment `{type:"reflection", report_id, verdict}`.

**PASS** (~3610): if the task is still REVIEW → `_merge_task_on_reflection_pass(task)` (`views.py` ~327) → `merge_task_on_reflection.delay(task.id)` (Celery, `dag_executor.py` ~1288) which merges the task branch, then `_advance_task_to_testing(task)` (`dag_executor.py` ~1478) sets TESTING (~1493). **PASS merges first, then advances — it is not a direct REVIEW→TESTING flip.**

**NEEDS_WORK or FAIL** (~3620, guard `verdict in ("NEEDS_WORK","FAIL")` at ~3624 — **FAIL is not advisory**):
- `completed_count = ReflectionReport.filter(status=COMPLETED).count()` (~3629).
- `≥ 3` → REVIEW → FAILED + "failed after 3 reflection attempts" (~3633).
- `< 3` → snapshot pre-rework agent/model (~3665), `_maybe_reassign_on_quota_failure(task, report)` (~3676, see §7), `_record_rework_continuity(...)` (~3683), REVIEW → IN_PROGRESS (~3692), re-trigger execution gated on `DAG_EXECUTOR_MAX_CONCURRENCY` (~3712); at capacity → `_set_dispatch_blocked_reason(task, "concurrency_cap_reached")` (~3720).

## 7. Quota-failure reassignment (cross-reference)

`_maybe_reassign_on_quota_failure()` (`views.py` ~616), `_is_quota_failure()` (~358), `_find_alternative_agent()` (~507). Quota keywords are imported from `failure_tagger.QUOTA_KEYWORDS` (~355), not an inline list. As merged in **W3.11**, this verifies real usage via `harness_usage_status` (95% threshold) before switching — 429-with-headroom backs off the same agent; genuine exhaustion reassigns to a same-cost-tier fallback.

Full trace: **`../../quota-failover-reassignment/`**. Do not restate the branch logic here.

## 8. Re-execution after NEEDS_WORK

After NEEDS_WORK moves a task to IN_PROGRESS and re-fires the execution strategy, `orchestrator.exec_task()` (~1754) rebuilds the prompt via `_build_task_context()` (~2313, prepended to the brief at ~1885). This single pass collects every comment type and, when a NEEDS_WORK/FAIL reflection is present, emits a **rework directive** that LEADS the prompt: the latest reviewer finding (full verdict text) plus every operator/human comment posted since that reflection, under `## Fix this first — the reviewer's finding`, with an explicit objective ("resolve the finding; do not re-verify the whole brief"). Earlier rounds, the summary, remaining notes, Q&A, proof, and prior agent output follow as context. (The older `_build_reflection_context()` / `_build_self_context()` pair is deprecated.) Continuity of assignee/model across the rework is recorded by `_record_rework_continuity()` (`views.py` ~756).

**Why the directive leads** (task #363): the rework prompt used to bury a one-line verdict under a passive "Previous Review Feedback" heading while operator replies sat in a separate section, so every re-dispatched agent re-did the whole brief and ignored the targeted fix. Bundling the finding + operator notes at the top with an explicit objective makes a one-line fix converge in one round.

## 9. ReflectionReport model

**File**: `tasks/models.py`
Relevant fields: `status` (PENDING→RUNNING→COMPLETED/FAILED), `verdict` (PASS/NEEDS_WORK/FAIL), `verdict_summary`, `improvements`, `quota_failure` (~445), `task` (FK), `requested_by`. No metadata counter — loop count is derived from the completed-report count.
