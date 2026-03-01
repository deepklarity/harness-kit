# Odin Test Plan

Checklist of what needs testing. Each item is tagged:
- **`[simple]`** — Unit/pure-function test, no external deps, fast
- **`[llm]`** — Requires real LLM CLI agents on PATH (codex, gemini, qwen, etc.)
- **`[io]`** — Disk/file I/O but no LLM calls
- **`[mock]`** — Needs mocked subprocess/HTTP, no real agents

Status: `[ ]` = not covered, `[x]` = covered, `[~]` = partial

---

## Harness System

- [x] Harness availability — gemini/qwen/codex on PATH `[simple]`
- [x] All 6 harnesses registered in HARNESS_REGISTRY `[simple]`
- [x] Single harness execute — gemini returns output `[llm]`
- [x] Single harness execute — qwen returns output `[llm]`
- [ ] Harness timeout handling — subprocess killed on timeout `[mock]`
- [ ] Harness stderr/non-zero exit → TaskResult.success=False `[mock]`
- [ ] API harness (minimax) execute with mocked HTTP `[mock]`
- [ ] API harness (glm) execute with mocked HTTP `[mock]`
- [ ] `is_available()` returns False when CLI missing `[simple]`
- [ ] `cli_command` config override respected by harness `[simple]`

## Task Management (taskit)

- [x] TaskManager CRUD — create, get, list, delete `[io]` — `disk/test_taskit.py::TestTaskManagerCRUD`
- [x] Task status lifecycle: pending → assigned → in_progress → completed `[io]` — `disk/test_taskit.py::TestTaskLifecycle`
- [x] Task status lifecycle: pending → assigned → in_progress → failed `[io]` — `disk/test_taskit.py::TestTaskLifecycle`
- [x] Prefix resolution — unique prefix resolves correctly `[io]` — `disk/test_taskit.py::TestPrefixResolution`
- [x] Prefix resolution — ambiguous prefix returns None `[io]` — `disk/test_taskit.py::TestPrefixResolution`
- [x] Task filtering by status `[io]` — `disk/test_taskit.py::TestTaskFiltering`
- [x] Task filtering by agent `[io]` — `disk/test_taskit.py::TestTaskFiltering`
- [x] Task filtering by spec_id `[io]` — `disk/test_taskit.py::TestTaskFiltering`
- [x] Task assignment — assign_task sets agent + status `[io]` — `disk/test_taskit.py::TestTaskLifecycle`
- [x] Task comments — add_comment persists `[io]` — `disk/test_taskit.py::TestTaskComments`
- [x] Task index consistency after create/delete `[io]` — `disk/test_taskit.py::TestIndexConsistency`
- [x] Ready tasks — dependency-aware filtering `[io]` — `disk/test_taskit.py::TestReadyTasks`

## Spec System

- [x] SpecStore CRUD — save, load, load_all `[simple]` — `disk/test_specs_io.py::TestSpecStore`
- [x] SpecStore set_abandoned `[simple]` — `disk/test_specs_io.py::TestSpecStore`
- [x] SpecStore prefix resolution `[simple]` — `disk/test_specs_io.py::TestSpecStore`
- [x] derive_spec_status — all 9 rules (abandoned/active/blocked/done/partial/planned/draft/empty) `[simple]` — `unit/test_specs.py::TestDeriveSpecStatus`
- [x] spec_short_tag — file paths, inline prompts, headings `[simple]` — `unit/test_specs.py::TestSpecShortTag`
- [x] Multi-spec coexistence — filtering, abandoned exclusion `[simple]` — `disk/test_specs_io.py::TestMultiSpecCoexistence`
- [ ] generate_spec_id uniqueness `[simple]`

## Orchestrator — Planning

- [x] Decomposition — base agent produces valid JSON subtasks `[llm]`
- [x] Plan-only — creates tasks in ASSIGNED status, no execution `[llm]`
- [x] Plan creates spec archive with metadata `[llm]`
- [ ] Plan from inline prompt (`--prompt "..."`) `[llm]`
- [ ] Plan with dependencies — `depends_on` fields in subtasks `[llm]`
- [x] Envelope parsing — `_parse_envelope()` extracts status/summary `[simple]` — `unit/test_dag.py::TestParseEnvelope`
- [x] Envelope parsing — unit tests `[simple]`
- [x] Prompt wrapping — `_wrap_prompt()` injects envelope markers `[simple]` — `unit/test_dag.py::TestParseEnvelope`
- [x] Prompt wrapping — unit tests `[simple]`
- [x] Prompt wrapping — MCP section omitted when mcp_task_id is None `[simple]` — `unit/test_dag.py::TestParseEnvelope::test_wrap_prompt_without_mcp_omits_mcp_section`
- [x] Prompt wrapping — MCP section included when mcp_task_id is provided `[simple]` — `unit/test_dag.py::TestParseEnvelope::test_wrap_prompt_with_mcp_includes_mcp_section`
- [x] Prompt wrapping — MCP section ordered between prompt and envelope `[simple]` — `unit/test_dag.py::TestParseEnvelope::test_wrap_prompt_mcp_section_between_prompt_and_envelope`
- [x] Prompt wrapping — working_dir + MCP + envelope compose together `[simple]` — `unit/test_dag.py::TestParseEnvelope::test_wrap_prompt_with_working_dir_and_mcp`
- [ ] Reasoning metadata stored on tasks after plan `[llm]`

## Orchestrator — Execution

- [x] Full E2E — plan + exec produces output `[llm]`
- [x] Exec single task by ID `[llm]`
- [x] Cycle detection — DAG with cycle raises error before exec `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] EXECUTING status — _execute_task sets EXECUTING (not IN_PROGRESS) `[mock]` — `mock/test_mock_mode.py::TestExecutingStatusTransition`
- [x] Already EXECUTING skips transition (Celery path) `[mock]` — `mock/test_mock_mode.py::TestExecutingStatusTransition`
- [x] mark_interrupted — includes EXECUTING tasks `[simple]`

## Mock Mode

- [x] Mock exec does not change task status `[mock]` — `mock/test_mock_mode.py::TestMockModeExecution`
- [x] Mock exec does not post comments `[mock]` — `mock/test_mock_mode.py::TestMockModeExecution`
- [x] Mock exec returns parsed result `[mock]` — `mock/test_mock_mode.py::TestMockModeExecution`
- [x] Mock exec skips EXECUTING transition `[mock]` — `mock/test_mock_mode.py::TestMockModeExecution`
- [x] Mock exec does not record cost data `[mock]` — `mock/test_mock_mode.py::TestMockModeExecution`

## DAG Executor (TaskIt Celery)

- [x] _deps_satisfied — no deps always satisfied `[simple]` — `taskit-backend/tests/test_dag_executor.py`
- [x] _deps_satisfied — all deps DONE `[simple]`
- [x] _deps_satisfied — REVIEW counts as satisfied `[simple]`
- [x] _deps_satisfied — partial deps not satisfied `[simple]`
- [x] _deps_satisfied — EXECUTING deps not satisfied `[simple]`
- [x] _any_dep_failed — failed dep detected `[simple]`
- [x] poll_and_execute — transitions ready task to EXECUTING `[simple]`
- [x] poll_and_execute — skips unassigned tasks `[simple]`
- [x] poll_and_execute — skips unsatisfied deps `[simple]`
- [x] poll_and_execute — skips failed deps `[simple]`
- [x] poll_and_execute — respects concurrency limit `[simple]`
- [x] execute_single_task — success → REVIEW `[mock]`
- [x] execute_single_task — failure → FAILED `[mock]`
- [x] execute_single_task — timeout → FAILED `[mock]`
- [x] execute_single_task — skips non-EXECUTING task `[simple]`
- [x] execute_single_task — respects odin's own status update `[mock]`
- [x] CeleryDAGStrategy — no-op trigger (logs only) `[simple]`

## DAG Validation

- [x] No deps — validation passes `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Linear chain valid `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Diamond deps valid `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Simple cycle detected `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Self-cycle detected `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Three-node cycle detected `[simple]` — `unit/test_dag.py::TestDAGValidation`
- [x] Wave grouping — independent tasks all ready `[simple]` — `unit/test_dag.py::TestWaveGrouping`
- [x] Wave grouping — chain one-per-wave `[simple]` — `unit/test_dag.py::TestWaveGrouping`
- [x] Wave grouping — mixed ready/blocked `[simple]` — `unit/test_dag.py::TestWaveGrouping`

## Tmux Module

- [x] session_name formatting `[simple]` — `mock/test_tmux.py::TestSessionName`
- [x] is_available checks PATH `[simple]` — `mock/test_tmux.py::TestIsAvailable`
- [x] Wrapper script has pipefail `[simple]` — `mock/test_tmux.py::TestWrapperScriptContent`
- [x] Wrapper script has tee `[simple]` — `mock/test_tmux.py::TestWrapperScriptContent`
- [x] Wrapper script has exit marker `[simple]` — `mock/test_tmux.py::TestWrapperScriptContent`
- [x] Wrapper script env_unset `[simple]` — `mock/test_tmux.py::TestWrapperScriptContent`
- [x] Wrapper script command escaping `[simple]` — `mock/test_tmux.py::TestWrapperScriptContent`
- [x] Launch creates executable script `[mock]` — `mock/test_tmux.py::TestLaunchCreatesScript`
- [x] Launch returns session name `[mock]` — `mock/test_tmux.py::TestLaunchCreatesScript`
- [x] Launch raises on tmux failure `[mock]` — `mock/test_tmux.py::TestLaunchCreatesScript`
- [x] Real tmux: launch + wait_for_exit `[tmux_real]` — `mock/test_tmux.py::TestTmuxReal`
- [x] Real tmux: has_session lifecycle `[tmux_real]` — `mock/test_tmux.py::TestTmuxReal`
- [x] Real tmux: exit code capture `[tmux_real]` — `mock/test_tmux.py::TestTmuxReal`
- [x] Real tmux: kill nonexistent returns false `[tmux_real]` — `mock/test_tmux.py::TestTmuxReal`

## TaskIt Backend (HTTP)

- [x] Login via /auth/login/ returns token `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login caches token (no duplicate calls) `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login sends correct email+password payload `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login bad credentials raises with .env guidance `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login server error raises `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login connection error raises with guidance `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Login when auth disabled returns empty token `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthLogin`
- [x] Re-login when token expired `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthExpiry`
- [x] Re-login when near expiry (5-min margin) `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthExpiry`
- [x] No re-login when token still valid `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthExpiry`
- [x] Expiry set from response `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthExpiry`
- [x] httpx.Auth flow injects Bearer header `[mock]` — `mock/test_taskit_backend.py::TestTaskItAuthFlow`
- [x] Backend without auth has no auth handler `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendAuth`
- [x] Backend with auth has TaskItAuth on client `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendAuth`
- [x] Backend partial auth config → no auth `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendAuth`
- [x] Auth login URL constructed from base_url `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendAuth`
- [x] Authenticated request includes Bearer header `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendAuth`
- [x] Save new task (POST) `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendCRUD`
- [x] Save existing task (PUT) `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendCRUD`
- [x] Load task found/not found `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendCRUD`
- [x] Delete task success/not found `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendCRUD`
- [x] Load all tasks with agent resolution `[mock]` — `mock/test_taskit_backend.py::TestTaskItBackendCRUD`
- [x] Config env vars populate TaskItConfig `[simple]` — `mock/test_taskit_backend.py::TestTaskItConfigFromEnv`
- [x] Config env vars override YAML config `[simple]` — `mock/test_taskit_backend.py::TestTaskItConfigFromEnv`
- [ ] Spec CRUD with mocked HTTP `[mock]`
- [ ] Agent user resolution with mocked HTTP `[mock]`

## Comments & Actor Identity

- [x] TaskIt backend add_comment POSTs to /tasks/:id/comments/ `[mock]` — `mock/test_comments.py::TestTaskItBackendAddComment`
- [x] add_comment sends attachments when provided `[mock]` — `mock/test_comments.py::TestTaskItBackendAddComment`
- [x] add_comment raises on HTTP error `[mock]` — `mock/test_comments.py::TestTaskItBackendAddComment`
- [x] Actor email: agent+model → {agent}+{model}@odin.agent `[simple]` — `mock/test_comments.py::TestActorIdentity`
- [x] Actor email: agent only → {agent}@odin.agent `[simple]` — `mock/test_comments.py::TestActorIdentity`
- [x] Actor email: odin system → odin@harness.kit `[simple]` — `mock/test_comments.py::TestActorIdentity`
- [x] Actor label: agent+model → "agent (model)" `[simple]` — `mock/test_comments.py::TestActorIdentity`
- [x] Actor label: agent only → "agent" `[simple]` — `mock/test_comments.py::TestActorIdentity`
- [x] Compose comment: duration + tokens formatted `[simple]` — `mock/test_comments.py::TestComposeComment`
- [x] Compose comment: duration only (no tokens) `[simple]` — `mock/test_comments.py::TestComposeComment`
- [x] Compose comment: no metrics → summary only `[simple]` — `mock/test_comments.py::TestComposeComment`
- [x] Compose comment: failed verb `[simple]` — `mock/test_comments.py::TestComposeComment`
- [x] Compose comment: alternative token keys (prompt/completion) `[simple]` — `mock/test_comments.py::TestComposeComment`
- [x] TaskManager routes comments through backend when available `[mock]` — `mock/test_comments.py::TestTaskManagerCommentRouting`
- [x] TaskManager falls back to local disk when no backend `[io]` — `mock/test_comments.py::TestTaskManagerCommentRouting`

## Mock Harness

- [x] MockHarness registered in HARNESS_REGISTRY `[simple]` — `mock/test_mock_harness.py::TestMockHarness`
- [x] MockHarness execute returns success with ODIN envelope `[simple]` — `mock/test_mock_harness.py::TestMockHarness`
- [x] MockHarness output contains ODIN-STATUS envelope `[simple]` — `mock/test_mock_harness.py::TestMockHarness`
- [x] MockHarness is_available returns True `[simple]` — `mock/test_mock_harness.py::TestMockHarness`
- [x] MockHarness build_execute_command raises NotImplementedError `[simple]` — `mock/test_mock_harness.py::TestMockHarness`
- [x] MockHarness metadata includes token breakdown `[simple]` — `mock/test_mock_harness.py::TestMockHarness`

## E2E Comment Pipeline

- [x] Mock harness → parse envelope → compose comment `[mock]` — `mock/test_e2e_comments.py::TestE2ECommentPipeline`
- [x] Full pipeline: mock exec → compose → POST to TaskIt `[mock]` — `mock/test_e2e_comments.py::TestE2ECommentPipeline`
- [x] Failed task posts failure comment with metrics `[mock]` — `mock/test_e2e_comments.py::TestE2ECommentPipeline`
- [x] Odin system comment uses odin@harness.kit email `[mock]` — `mock/test_e2e_comments.py::TestE2ECommentPipeline`

## Reassignment

- [x] Reassign task to different agent `[llm]`
- [ ] Reassign preserves other task fields `[io]`

## Cost Tracking

- [x] CostStore — save_record persists to disk `[io]` — `disk/test_cost_tracking.py::TestCostStore`
- [x] CostStore — load_by_spec returns correct records `[io]` — `disk/test_cost_tracking.py::TestCostStore`
- [x] CostStore — load_all across multiple specs `[io]` — `disk/test_cost_tracking.py::TestCostStore`
- [x] CostStore — summarize_spec aggregates correctly `[simple]` — `disk/test_cost_tracking.py::TestCostStoreSummarize`
- [x] CostStore — summarize_all groups by spec `[simple]` — `disk/test_cost_tracking.py::TestCostStoreSummarize`
- [x] CostStore — handles missing/corrupt JSON gracefully `[io]` — `disk/test_cost_tracking.py::TestCostStore`
- [x] CostTracker — record_task extracts from TaskResult `[simple]` — `disk/test_cost_tracking.py::TestCostTracker`
- [x] CostTracker — handles missing usage metadata `[simple]` — `disk/test_cost_tracking.py::TestCostTracker`
- [x] TaskCostRecord model validation `[simple]` — `disk/test_cost_tracking.py::TestTaskCostRecord`
- [x] CostTracker — OpenAI-style token keys `[simple]` — `disk/test_cost_tracking.py::TestCostTracker`
- [x] CostStore — orphan tasks stored correctly `[io]` — `disk/test_cost_tracking.py::TestCostStore`
- [x] CostStore — empty spec summarization `[simple]` — `disk/test_cost_tracking.py::TestCostStoreSummarize`

## Cost Estimation

- [x] load_pricing_table — loads all models from agent_models.json `[simple]` — `unit/test_cost_estimator.py::TestLoadPricingTable`
- [x] load_pricing_table — known model has non-None prices `[simple]` — `unit/test_cost_estimator.py::TestLoadPricingTable`
- [x] load_pricing_table — unknown model has None prices `[simple]` — `unit/test_cost_estimator.py::TestLoadPricingTable`
- [x] load_pricing_table — loads from minimal JSON `[simple]` — `unit/test_cost_estimator.py::TestLoadPricingTable`
- [x] estimate_cost — known model computes correctly `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — unknown model returns None `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — missing model returns None `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — zero tokens returns 0 `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — null tokens returns None `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — partial null tokens returns None `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`
- [x] estimate_cost — large token count `[simple]` — `unit/test_cost_estimator.py::TestEstimateCost`

## Configuration

- [x] Config loading — YAML file parsed correctly `[io]` — `unit/test_config.py::TestYAMLLoading`
- [x] Config hierarchy — explicit > project > global > defaults `[io]` — `unit/test_config.py::TestConfigHierarchy`
- [x] Config env var substitution (`${VAR}`) `[simple]` — `unit/test_config.py::TestEnvVarSubstitution`
- [x] Missing config file → built-in defaults used `[simple]` — `unit/test_config.py::TestConfigHierarchy`
- [x] Agent config — capabilities, cost_tier, cli_command `[simple]` — `unit/test_config.py::TestDefaultConfig`
- [x] Base agent selection from config `[simple]` — `unit/test_config.py::TestDefaultConfig`
- [x] Model parsing — list and dict formats `[simple]` — `unit/test_config.py::TestParseModels`
- [x] Model routing parsing `[simple]` — `unit/test_config.py::TestParseModelRouting`
- [x] Unknown config keys ignored `[io]` — `unit/test_config.py::TestYAMLLoading`
- [x] Enabled agents filtering `[simple]` — `unit/test_config.py::TestOdinConfigMethods`

## Logging

- [x] StructuredLogger writes valid JSONL `[io]` — `disk/test_logging.py::TestOdinLogger`
- [x] Log entries have correct action types `[io]` — `disk/test_logging.py::TestOdinLogger`
- [x] Log entries include timestamps and task_id `[io]` — `disk/test_logging.py::TestOdinLogger`
- [x] Duration tracking on task_completed events `[io]` — `disk/test_logging.py::TestOdinLogger`
- [x] Input/output truncation at 2000 chars `[simple]` — `disk/test_logging.py::TestOdinLogger`
- [x] None values excluded from entries `[simple]` — `disk/test_logging.py::TestOdinLogger`
- [x] Multiple entries appended to same file `[io]` — `disk/test_logging.py::TestOdinLogger`

## CLI (Smoke Tests)

- [ ] `odin plan` — exits 0 with spec file `[llm]`
- [ ] `odin status` — renders table without error `[io]`
- [ ] `odin specs` — renders spec list `[io]`
- [ ] `odin show <id>` — renders task detail `[io]`
- [ ] `odin config` — renders config table `[simple]`
- [ ] `odin logs` — renders log output `[io]`
- [ ] Prefix matching works in CLI commands `[io]`

## Disk Write / Agent Capabilities

- [x] Agents can write files to disk (codex/gemini/qwen) `[llm]`

---

## Streaming (Plan Output)

- [x] CLI harnesses yield chunks incrementally (claude/gemini/codex/qwen) `[mock]`
- [x] Streaming chunk order preserved `[mock]`
- [x] Streaming callback called per chunk with incremental timing `[mock]`
- [x] Streaming handles CLI not found gracefully `[mock]`
- [x] Streaming handles empty output `[mock]`
- [x] BaseHarness fallback yields full output as single chunk `[simple]`
- [x] BaseHarness fallback yields nothing for empty output `[simple]`
- [x] _decompose() streams via callback incrementally `[mock]`
- [x] _decompose() without callback uses execute() not streaming `[mock]`
- [x] _decompose() streaming accumulates full output for JSON parsing `[mock]`
- [x] plan() with callback streams incrementally `[mock]`
- [x] plan() without callback does not use streaming `[mock]`
- [x] CLI _stream_chunk writes and flushes per chunk `[simple]`
- [x] CLI _stream_chunk preserves partial lines `[simple]`
- [x] Streaming first chunk arrives before last (timing) `[mock]`
- [x] Non-streaming delivers all output at once `[mock]`

## Task Output

- [x] Task output file created via harness `[io]`
- [x] Task output file flushed per line `[io]`
- [x] started_at metadata set on EXECUTING tasks `[io]`
- [x] Tail exits when task leaves IN_PROGRESS `[simple]`

## TaskIt MCP Server

- [x] taskit_add_comment — status_update calls post_comment `[simple]` — `unit/test_taskit_mcp.py::TestAddComment`
- [x] taskit_add_comment — question calls ask_question(wait=True, timeout=0) `[simple]` — `unit/test_taskit_mcp.py::TestAddComment`
- [x] taskit_add_comment — question timeout returns reply=None `[simple]` — `unit/test_taskit_mcp.py::TestAddComment`
- [x] taskit_add_comment — default type is status_update `[simple]` — `unit/test_taskit_mcp.py::TestAddComment`
- [x] taskit_add_comment — string enum values accepted `[simple]` — `unit/test_taskit_mcp.py::TestAddComment`
- [x] taskit_add_attachment — proof calls submit_proof `[simple]` — `unit/test_taskit_mcp.py::TestAddAttachment`
- [x] taskit_add_attachment — file calls post_comment `[simple]` — `unit/test_taskit_mcp.py::TestAddAttachment`
- [x] taskit_add_attachment — default type is file `[simple]` — `unit/test_taskit_mcp.py::TestAddAttachment`
- [x] _make_client — env var defaults `[simple]` — `unit/test_taskit_mcp.py::TestMakeClient`
- [x] _make_client — env var overrides `[simple]` — `unit/test_taskit_mcp.py::TestMakeClient`
- [x] Integration: post comment via MCP → verify in TaskIt API `[integration]` — `integration/test_taskit_mcp_live.py`
- [x] Integration: question creates pending comment `[integration]` — `integration/test_taskit_mcp_live.py`
- [x] Integration: question → reply → poll roundtrip `[integration]` — `integration/test_taskit_mcp_live.py`
- [x] Integration: proof attachment created `[integration]` — `integration/test_taskit_mcp_live.py`
- [x] Integration: file attachment created `[integration]` — `integration/test_taskit_mcp_live.py`
- [x] CommentType enum has status_update and question (no telemetry) `[simple]` — `unit/test_taskit_mcp_comment_type.py::test_comment_type_enum_values`
- [x] taskit_add_comment — status_update passes comment_type to client `[simple]` — `unit/test_taskit_mcp_comment_type.py::test_status_update_calls_post_comment_with_type`
- [x] taskit_add_comment — string comment_type works (FastMCP compat) `[simple]` — `unit/test_taskit_mcp_comment_type.py::test_string_comment_type_works`
- [x] taskit_add_comment — defaults task_id from TASKIT_TASK_ID env var `[simple]` — `unit/test_taskit_mcp.py::TestTaskIdEnvDefault`
- [x] taskit_add_comment — explicit task_id overrides env var `[simple]` — `unit/test_taskit_mcp.py::TestTaskIdEnvDefault`
- [x] taskit_add_comment — no task_id and no env returns error `[simple]` — `unit/test_taskit_mcp.py::TestTaskIdEnvDefault`
- [x] taskit_add_attachment — defaults task_id from TASKIT_TASK_ID env var `[simple]` — `unit/test_taskit_mcp.py::TestTaskIdEnvDefault`
- [x] taskit_add_attachment — no task_id and no env returns error `[simple]` — `unit/test_taskit_mcp.py::TestTaskIdEnvDefault`
- [ ] MCP server starts via entry point (taskit-mcp) `[simple]`
- [ ] MCP Inspector manual test `[integration]`

## MCP Harness Integration

- [x] Claude harness adds --mcp-config flag when context has mcp_config `[simple]` — `unit/test_mcp_harness_integration.py::TestClaudeHarnessMcpConfig`
- [x] Gemini harness adds --mcp-config flag `[simple]` — `unit/test_mcp_harness_integration.py::TestGeminiHarnessMcpConfig`
- [x] Qwen harness adds --mcp-config flag `[simple]` — `unit/test_mcp_harness_integration.py::TestQwenHarnessMcpConfig`
- [x] Codex harness ignores MCP config (no support) `[simple]` — `unit/test_mcp_harness_integration.py::TestCodexHarnessNoMcp`
- [x] Orchestrator generates valid MCP config JSON `[simple]` — `unit/test_mcp_harness_integration.py::TestMcpConfigGeneration`
- [x] MCP config has correct env vars (URL, token, task_id, author) `[simple]` — `unit/test_mcp_harness_integration.py::TestMcpConfigGeneration`
- [x] Auth failure gracefully sets empty token `[simple]` — `unit/test_mcp_harness_integration.py::TestMcpConfigGeneration`
- [x] No taskit config returns None (no MCP) `[simple]` — `unit/test_mcp_harness_integration.py::TestMcpConfigGeneration`
- [x] All harness configs include taskit tools `[simple]` — `unit/test_mcp_harness_integration.py::TestAllHarnessConfigsConsistency`
- [x] MCP env includes auth token `[simple]` — `unit/test_mcp_harness_integration.py::TestAllHarnessConfigsConsistency`
- [x] MCP env includes correct author identity per harness `[simple]` — `unit/test_mcp_harness_integration.py::TestAllHarnessConfigsConsistency`
- [x] Claude config includes question tool `[simple]` — `unit/test_mcp_harness_integration.py::TestAllHarnessConfigsConsistency`
- [ ] Live harness execution with MCP tools available `[integration]`
- [ ] MiniMax/GLM/Codex gracefully work without MCP `[integration]`

## Mobile MCP Integration

- [x] MOBILE_TOOL_NAMES has 19 entries `[simple]` — `unit/test_mobile_mcp_config.py::TestMobileToolNames`
- [x] Tool names are sorted `[simple]` — `unit/test_mobile_mcp_config.py::TestMobileToolNames`
- [x] Claude-prefixed names use mcp__mobile__ format `[simple]` — `unit/test_mobile_mcp_config.py::TestClaudeMobileToolNames`
- [x] Server fragment: Claude has no env `[simple]` — `unit/test_mobile_mcp_config.py::TestServerFragmentClaude`
- [x] Server fragment: Gemini has trust:true `[simple]` — `unit/test_mobile_mcp_config.py::TestServerFragmentGemini`
- [x] Server fragment: OpenCode has type:local, command as array `[simple]` — `unit/test_mobile_mcp_config.py::TestServerFragmentOpencode`
- [x] Merged config: Claude has both taskit + mobile servers `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] Merged config: Gemini has both servers `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] Merged config: Codex has both servers (TOML) `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] Merged config: OpenCode has both servers `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] Claude allowed tools include mobile tools `[simple]` — `unit/test_mcp_harness_integration.py::TestMobileToolApproval`
- [x] OpenCode permission includes mobile tools `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] Default mcps (taskit only) has no mobile `[simple]` — `unit/test_mcp_harness_integration.py::TestMultiServerMerging`
- [x] _wrap_prompt includes mobile section when configured `[simple]` — `unit/test_mcp_harness_integration.py::TestWrapPromptMobile`
- [x] _wrap_prompt excludes mobile when not configured `[simple]` — `unit/test_mcp_harness_integration.py::TestWrapPromptMobile`
- [x] OdinConfig mcps defaults to ["taskit"] `[simple]` — `unit/test_config.py::TestOdinConfigMcps`
- [x] mcps parsed from YAML config `[simple]` — `unit/test_config.py::TestOdinConfigMcps`
- [x] mcps defaults when missing from YAML `[simple]` — `unit/test_config.py::TestOdinConfigMcps`
- [x] Codex harness injects mobile -c flags `[simple]` — `unit/test_mcp_harness_integration.py::TestCodexMobileFlags`
- [ ] Integration: mobile_list_devices returns device list `[integration]` — `integration/test_mobile_mcp_live.py`
- [ ] Integration: mobile_screenshot saves PNG to disk `[integration]` — `integration/test_mobile_mcp_live.py`
- [ ] Integration: screenshot → upload → verify in TaskIt `[integration]` — `integration/test_mobile_mcp_live.py`

## TaskIt Tool Client

- [x] post_comment calls correct API `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientPostComment`
- [x] ask_question no wait `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientAsk`
- [x] ask_question with wait gets reply `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientAsk`
- [x] ask_question timeout `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientAsk`
- [x] submit_proof `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientProof`
- [x] get_context `[simple]` — `unit/test_taskit_tool.py::TestTaskItToolClientContext`
- [x] client_from_env reads env vars `[simple]` — `unit/test_taskit_tool.py::TestClientFromEnv`
- [x] post_comment sends comment_type in payload `[simple]` — `unit/test_tool_client_comment_type.py::test_post_comment_sends_comment_type`
- [x] post_comment defaults to status_update `[simple]` — `unit/test_tool_client_comment_type.py::test_post_comment_defaults_to_status_update`
- [x] poll with malformed attachments doesn't crash `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`
- [x] poll with empty attachments skipped gracefully `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`
- [x] poll with missing reply_to field doesn't match `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`
- [x] ask_question HTTP error raises `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`
- [x] poll HTTP error raises `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`
- [x] ask_question auth failure raises `[simple]` — `unit/test_taskit_tool.py::TestPollEdgeCases`

## Question/Poll Roundtrip (Mock)

- [x] Question posts to /question/ endpoint (not /comments/) `[mock]` — `mock/test_question_poll_roundtrip.py::TestQuestionEndpoint`
- [x] Poll hits /comments/?after= endpoint `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollEndpoint`
- [x] Poll finds reply by attachment type `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollReplyMatching`
- [x] Poll ignores non-reply comments `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollReplyMatching`
- [x] Poll ignores reply to different question `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollReplyMatching`
- [x] Indefinite poll (timeout=0) no deadline `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollTimingBehavior`
- [x] Poll interval is 5 seconds `[mock]` — `mock/test_question_poll_roundtrip.py::TestPollTimingBehavior`
- [x] MCP question returns reply content `[mock]` — `mock/test_question_poll_roundtrip.py::TestMcpReturnFormat`
- [x] MCP question blocks until reply `[mock]` — `mock/test_question_poll_roundtrip.py::TestMcpReturnFormat`
- [x] Network error during poll propagates `[mock]` — `mock/test_question_poll_roundtrip.py::TestErrorPropagation`

## Reflection

- [x] Reflection prompt contains read-only instruction `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_contains_readonly_instruction`
- [x] Reflection prompt includes task context `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_task_title_and_description`
- [x] Reflection prompt includes execution output `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_execution_output`
- [x] Reflection prompt includes dependent tasks `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_dependent_tasks`
- [x] Reflection prompt includes custom prompt when provided `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_custom_prompt_when_provided`
- [x] Reflection prompt omits custom section when empty `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_omits_custom_prompt_section_when_empty`
- [x] Reflection prompt includes agent/model info `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_agent_and_model_info`
- [x] Reflection prompt includes all section headers `[simple]` — `unit/test_reflection.py::TestBuildReflectionPrompt::test_prompt_includes_section_headers`
- [x] Report parser extracts all sections `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_extracts_all_five_sections`
- [x] Report parser extracts verdict PASS `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_extracts_verdict_pass`
- [x] Report parser extracts verdict NEEDS_WORK `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_extracts_verdict_needs_work`
- [x] Report parser extracts verdict FAIL `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_extracts_verdict_fail`
- [x] Report parser extracts verdict summary `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_extracts_verdict_summary`
- [x] Report parser handles missing sections `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_handles_missing_sections_gracefully`
- [x] Report parser handles empty output `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_handles_empty_output`
- [x] Report parser handles no headers `[simple]` — `unit/test_reflection.py::TestParseReflectionReport::test_parse_handles_no_headers`
- [x] reflect_task updates report to RUNNING `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_updates_report_to_running`
- [x] reflect_task gathers context from API `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_gathers_context_from_api`
- [x] reflect_task calls harness with working_dir `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_calls_harness_with_working_dir`
- [x] reflect_task submits parsed report `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_submits_parsed_report`
- [x] reflect_task report has correct sections `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_report_has_correct_sections`
- [x] reflect_task handles harness failure `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_handles_harness_failure`
- [x] reflect_task posts FAILED on error `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_posts_failed_status_on_error`
- [x] reflect_task with custom model override `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_with_custom_model_override`
- [x] reflect_task patches assembled_prompt `[mock]` — `mock/test_reflect_command.py::TestReflectTask::test_reflect_task_patches_assembled_prompt`
- [x] execute_reflection Celery task dispatched on reflect `[mock]` — `taskit-backend/tests/test_reflection.py::TestReflectEndpoint::test_reflect_dispatches_celery_task`
- [x] Cancel pending reflection → FAILED `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionCancel::test_cancel_pending_reflection`
- [x] Cancel running reflection → FAILED `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionCancel::test_cancel_running_reflection`
- [x] Cancel completed reflection rejected `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionCancel::test_cancel_completed_reflection_rejected`
- [x] Cancel failed reflection rejected `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionCancel::test_cancel_failed_reflection_rejected`
- [x] List all reflections `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionListAll::test_list_all_reflections`
- [x] List reflections filter by status `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionListAll::test_list_reflections_filter_by_status`
- [x] List reflections filter by verdict `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionListAll::test_list_reflections_filter_by_verdict`
- [x] Assembled prompt stored in report `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionReportUpdate::test_assembled_prompt_stored_in_report`
- [x] Serializer includes task_title `[simple]` — `taskit-backend/tests/test_reflection.py::TestReflectionListAll::test_serializer_includes_task_title`

## Priority Order for Remaining Tests

1. **Harness error handling** `[mock]` — Timeout, stderr, non-zero exit
2. **API harness mocks** `[mock]` — minimax/glm with mocked HTTP
3. **CLI smoke tests** `[io]` — No direct CLI tests
4. **Reassign preserves fields** `[io]` — Edge case
