# Odin Test Cases

Quick reference for agents and humans. Run `python -m pytest tests/ -v` from `odin/`.

## How Tests Are Organized

Tests are split into four subdirectories by dependency profile:

- **`unit/`** — Pure logic, no I/O, no mocks. Fastest tests.
- **`disk/`** — Disk I/O but no network, no subprocesses.
- **`mock/`** — Mocked subprocesses/HTTP. No real services.
- **`integration/`** — Real CLI agents required. Excluded by default.

```
tests/
  conftest.py              # Shared fixtures (odin_dirs, task_mgr, make_config, FakeDelayedStdout)
  specs/                   # Test spec files for manual integration testing
    mini_spec.md           # 3-task linear DAG smoke test
    testspec.md            # 2-task DAG smoke test

  unit/                    # Pure logic — no I/O, no mocks
    test_config.py         # Config loading, hierarchy, env var substitution
    test_cost_estimator.py # Pricing table loading, cost estimation from tokens
    test_dag.py            # DAG validation (cycle detection), wave grouping, envelope parsing
    test_reflection.py     # Reflection prompt builder and report parser
    test_routing.py        # Agent routing: suggestion respected/fallback, quota awareness
    test_specs.py          # derive_spec_status, spec_short_tag

  disk/                    # Disk I/O only — no network, no subprocesses
    test_cost_tracking.py  # CostStore/CostTracker persistence and summarization
    test_logging.py        # StructuredLogger JSONL output
    test_specs_io.py       # SpecStore CRUD, multi-spec coexistence
    test_taskit.py         # TaskManager CRUD, lifecycle, prefix resolution, filtering

  mock/                    # Mocked subprocesses/HTTP — no real services
    test_comments.py       # Comment bridge, actor identity, metrics composition
    test_context_injection.py # Upstream context injection in exec_task()
    test_e2e_comments.py   # End-to-end comment pipeline (mock harness → TaskIt)
    test_execution_logging.py # Execution I/O debug comments
    test_mock_harness.py   # Mock harness for testing without real LLMs
    test_mock_mode.py      # Mock mode: no backend writes, EXECUTING status transitions
    test_question_poll_roundtrip.py # Question→poll→reply cycle (mocked HTTP)
    test_reflect_command.py # Reflection task orchestration (mocked HTTP + harness)
    test_streaming.py      # Streaming chunk delivery, callbacks, timing behavior
    test_taskit_backend.py # TaskIt REST backend: auth, CRUD, config (mocked HTTP)
    test_tmux.py           # Tmux session names, wrapper scripts, launch (mock + real)
    test_trace_logging.py  # Trace file writing, JSON stream text extraction

  integration/             # Real CLI agents required — excluded by default
    conftest.py            # Integration-specific fixtures (work_dir, _make_config)
    test_real.py           # Real CLI agent integration tests (gemini, qwen, codex)
```

---

## Unit Tests (unit/)

### test_config.py — Configuration system

| Test | What it checks |
|---|---|
| `TestDefaultConfig::test_default_config_has_agents` | Built-in defaults include claude, gemini, qwen |
| `TestDefaultConfig::test_default_config_base_agent` | Default base agent is "claude" |
| `TestDefaultConfig::test_default_config_model_routing` | Default config has ModelRoute entries |
| `TestDefaultConfig::test_default_config_source` | config_source field set from source argument |
| `TestDefaultConfig::test_default_agent_cost_tiers` | Claude=HIGH, gemini/qwen=LOW |
| `TestDefaultConfig::test_default_cli_agents_enabled` | minimax and glm agents enabled by default |
| `TestYAMLLoading::test_load_from_yaml` | YAML with base_agent and agents loads correctly |
| `TestYAMLLoading::test_empty_yaml_returns_defaults` | Empty YAML falls back to defaults |
| `TestYAMLLoading::test_unknown_keys_ignored` | Unknown keys stored in extras, not rejected |
| `TestConfigHierarchy::test_explicit_path_takes_priority` | --config path beats local config |
| `TestConfigHierarchy::test_no_config_uses_defaults` | No config files returns defaults |
| `TestEnvVarSubstitution::test_api_key_from_env` | ${ENV_VAR} substitution in YAML works |
| `TestEnvVarSubstitution::test_missing_env_var_returns_none` | Missing env var becomes None |
| `TestParseModels::test_list_format` | List of model names parsed to dict |
| `TestParseModels::test_dict_format` | Dict of model->alias parsed correctly |
| `TestParseModels::test_invalid_returns_empty` | Non-list/dict returns {} |
| `TestParseModelRouting::test_valid_list` | Route dicts produce ModelRoute objects |
| `TestParseModelRouting::test_empty_returns_empty` | None or [] returns [] |
| `TestParseModelRouting::test_invalid_entries_skipped` | Malformed entries skipped |
| `TestOdinConfigMethods::test_enabled_agents` | enabled_agents() filters by enabled=True |

### test_cost_estimator.py — Cost estimation from pricing + tokens

| Test | What it checks |
|---|---|
| `TestLoadPricingTable::test_loads_all_models` | All models in agent_models.json have entries |
| `TestLoadPricingTable::test_known_model_has_prices` | claude-sonnet-4-5 has $3.00/$15.00 pricing |
| `TestLoadPricingTable::test_unknown_model_has_none_prices` | qwen3-coder has None pricing |
| `TestLoadPricingTable::test_loads_from_minimal_json` | Custom JSON loads correctly |
| `TestEstimateCost::test_known_model` | 1000 in / 500 out on claude-sonnet-4-5 → $0.0105 |
| `TestEstimateCost::test_unknown_model` | Null pricing returns None |
| `TestEstimateCost::test_missing_model` | Model not in table returns None |
| `TestEstimateCost::test_zero_tokens` | 0/0 tokens → $0.00 |
| `TestEstimateCost::test_null_tokens` | None tokens → None |
| `TestEstimateCost::test_partial_null_tokens` | One None token → None |
| `TestEstimateCost::test_large_token_count` | 100k/50k on gemini-2.5-flash → $0.045 |

### test_dag.py — DAG validation and wave grouping

| Test | What it checks |
|---|---|
| `TestDAGValidation::test_no_deps_valid` | Independent tasks pass validation |
| `TestDAGValidation::test_linear_chain_valid` | A->B->C chain is valid |
| `TestDAGValidation::test_diamond_deps_valid` | Diamond pattern is valid |
| `TestDAGValidation::test_simple_cycle_detected` | A->B->A raises RuntimeError |
| `TestDAGValidation::test_self_cycle_detected` | Self-dep raises RuntimeError |
| `TestDAGValidation::test_three_node_cycle_detected` | A->B->C->A detected |
| `TestDAGValidation::test_empty_task_list_valid` | Empty list passes |
| `TestWaveGrouping::test_independent_tasks_all_in_first_wave` | All independent tasks ready |
| `TestWaveGrouping::test_chain_one_task_per_wave` | Chain: only head is ready |
| `TestWaveGrouping::test_mixed_ready_and_blocked` | Independent+blocked correctly split |
| `TestParseEnvelope::test_success_envelope` | SUCCESS status parsed from envelope |
| `TestParseEnvelope::test_failed_envelope` | FAILED status and summary parsed |
| `TestParseEnvelope::test_no_envelope` | Plain output returns None fields |
| `TestParseEnvelope::test_status_only_no_summary` | Status without summary returns summary=None |
| `TestParseEnvelope::test_wrap_prompt` | _wrap_prompt appends envelope instructions |
| `TestParseEnvelope::test_wrap_prompt_without_mcp_omits_mcp_section` | No MCP section when mcp_task_id=None |
| `TestParseEnvelope::test_wrap_prompt_with_mcp_includes_mcp_section` | MCP section injected with task ID and tool names |
| `TestParseEnvelope::test_wrap_prompt_mcp_section_between_prompt_and_envelope` | MCP section ordered between prompt and ODIN-STATUS |
| `TestParseEnvelope::test_wrap_prompt_with_working_dir_and_mcp` | Working dir + MCP + envelope compose together |

### test_routing.py — Agent routing and fallback

| Test | What it checks |
|---|---|
| `TestRouteTaskSuggestionRespected::test_suggested_agent_used_when_valid` | Suggested "gemini" honored |
| `TestRouteTaskSuggestionRespected::test_suggested_agent_gets_model_from_routing` | Model from routing table |
| `TestRouteTaskSuggestionRespected::test_suggested_glm_respected` | API-based agent respected |
| `TestRouteTaskSuggestionRespected::test_suggested_claude_respected` | High-cost agent respected |
| `TestRouteTaskSuggestionRespected::test_multiple_tasks_different_agents` | Each suggestion yields correct agent |
| `TestRouteTaskSuggestionFallback::test_no_suggestion_uses_routing_priority` | Falls through to routing order |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_unavailable_falls_back` | Unavailable -> fallback |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_missing_caps_falls_back` | Missing caps -> fallback |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_disabled_falls_back` | Disabled -> fallback |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_unknown_falls_back` | Unknown name -> fallback |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_over_quota_falls_back` | Over quota (medium) -> fallback |
| `TestRouteTaskSuggestionFallback::test_suggested_agent_over_quota_but_high_complexity_kept` | Over quota + high complexity -> kept |

### test_mcp_harness_integration.py — MCP config generation and harness CLI flags

| Test | What it checks |
|---|---|
| `TestClaudeHarnessMcpConfig::test_adds_mcp_config_flag` | Claude adds --mcp-config flag |
| `TestClaudeHarnessMcpConfig::test_no_mcp_config_no_flag` | No flag without config |
| `TestClaudeHarnessMcpConfig::test_mcp_config_in_interactive_command` | Interactive mode gets MCP flag |
| `TestClaudeHarnessMcpConfig::test_mcp_config_with_model` | MCP + model flags coexist |
| `TestGeminiHarnessMcpConfig::test_adds_mcp_config_flag` | Gemini adds --mcp-config flag |
| `TestGeminiHarnessMcpConfig::test_no_mcp_config_no_flag` | No flag without config |
| `TestGeminiHarnessMcpConfig::test_mcp_config_in_interactive_command` | Interactive mode gets MCP flag |
| `TestQwenHarnessMcpConfig::test_adds_mcp_config_flag` | Qwen adds --mcp-config flag |
| `TestQwenHarnessMcpConfig::test_no_mcp_config_no_flag` | No flag without config |
| `TestCodexHarnessNoMcp::test_no_mcp_flag_even_with_config` | Codex ignores MCP config |
| `TestMcpConfigGeneration::test_generates_valid_json` | Config is valid JSON |
| `TestMcpConfigGeneration::test_config_has_correct_env_vars` | Env vars match orchestrator state |
| `TestMcpConfigGeneration::test_config_command_is_taskit_mcp` | Command is taskit-mcp |
| `TestMcpConfigGeneration::test_no_auth_sets_empty_token` | No backend -> empty token |
| `TestMcpConfigGeneration::test_returns_none_without_taskit_config` | No taskit config -> None |
| `TestMcpConfigGeneration::test_different_agents_get_correct_email` | Agent email follows convention |
| `TestMcpConfigGeneration::test_config_file_path_includes_task_id` | File named with task ID |
| `TestMcpConfigGeneration::test_auth_failure_sets_empty_token` | Auth error gracefully handled |
| `TestAllHarnessConfigsConsistency::test_all_harness_configs_include_taskit_tools` | All 6 harness configs reference taskit MCP |
| `TestAllHarnessConfigsConsistency::test_mcp_env_includes_auth_token` | Auth token in all harness configs |
| `TestAllHarnessConfigsConsistency::test_mcp_env_includes_author_identity` | Correct TASKIT_AUTHOR_EMAIL per harness |
| `TestAllHarnessConfigsConsistency::test_claude_mcp_config_includes_question_tool` | Claude toolnames include taskit_add_comment |
| `TestMultiServerMerging::test_merged_config_claude_has_both_servers` | Claude config has taskit + mobile |
| `TestMultiServerMerging::test_merged_config_gemini_has_both_servers` | Gemini config has taskit + mobile |
| `TestMultiServerMerging::test_merged_config_codex_has_both_servers` | Codex TOML has both servers |
| `TestMultiServerMerging::test_merged_config_opencode_has_both_servers` | OpenCode config has both servers |
| `TestMultiServerMerging::test_opencode_permission_includes_mobile_tools` | Mobile tool permissions in OpenCode |
| `TestMultiServerMerging::test_default_mcps_no_mobile` | Default config excludes mobile |
| `TestMultiServerMerging::test_mobile_only_no_taskit` | Mobile-only config works |
| `TestMobileToolApproval::test_claude_allowed_tools_include_mobile` | Mobile tools in Claude allowed list |
| `TestMobileToolApproval::test_claude_settings_includes_mobile_tools` | Claude settings includes mobile |
| `TestMobileToolApproval::test_claude_settings_no_mobile_by_default` | No mobile in default settings |
| `TestWrapPromptMobile::test_wrap_prompt_includes_mobile_section` | Mobile section in prompt |
| `TestWrapPromptMobile::test_wrap_prompt_no_mobile_when_not_configured` | No mobile section without config |
| `TestWrapPromptMobile::test_wrap_prompt_no_mobile_when_mcps_none` | No mobile section when mcps=None |
| `TestCodexMobileFlags::test_mobile_flags_when_enabled` | Codex injects mobile -c flags |
| `TestCodexMobileFlags::test_no_mobile_flags_when_not_enabled` | No mobile flags by default |

### test_mobile_mcp_config.py — Mobile MCP tool names and server fragments

| Test | What it checks |
|---|---|
| `TestMobileToolNames::test_has_19_entries` | 19 tools from mobile-mcp |
| `TestMobileToolNames::test_sorted` | Tool names are sorted |
| `TestMobileToolNames::test_mobile_tool_names_returns_copy` | Returns copy, not reference |
| `TestClaudeMobileToolNames::test_all_prefixed` | All tools prefixed mcp__mobile__ |
| `TestClaudeMobileToolNames::test_contains_known_tool` | Known tools present |
| `TestServerFragmentClaude::test_no_env_needed` | No env vars for mobile |
| `TestServerFragmentClaude::test_command_is_npx` | Command is npx |
| `TestServerFragmentGemini::test_has_trust` | Gemini has trust:true |
| `TestServerFragmentGemini::test_command_is_npx` | Command is npx |
| `TestServerFragmentQwen::test_has_trust` | Qwen has trust:true |
| `TestServerFragmentCodex::test_returns_flag_list` | Returns -c flag list |
| `TestServerFragmentCodex::test_contains_mobile_command` | Contains mobile command |
| `TestServerFragmentOpencode::test_structure` | type:local, command array |
| `TestServerFragmentOpencode::test_glm_same_as_minimax` | GLM = MiniMax format |
| `TestServerFragmentKilocode::test_has_always_allow` | alwaysAllow has 19 tools |
| `TestOpenCodePermissions::test_all_tools_allowed` | All tools = "allow" |
| `TestUnknownAgent::test_falls_back_to_claude` | Unknown agent defaults to Claude format |

### test_taskit_mcp.py — TaskIt MCP server tools (mocked client)

| Test | What it checks |
|---|---|
| `TestAddComment::test_status_update_calls_post_comment` | status_update type calls post_comment() |
| `TestAddComment::test_question_calls_ask_question_blocking` | question type calls ask_question(wait=True, timeout=0) |
| `TestAddComment::test_question_timeout_returns_none_reply` | Timeout returns reply=None |
| `TestAddComment::test_default_comment_type_is_status_update` | Default is status_update |
| `TestAddComment::test_string_comment_type_status_update` | String enum "status_update" accepted |
| `TestAddComment::test_string_comment_type_question` | String enum "question" accepted |
| `TestAddAttachment::test_proof_calls_submit_proof` | proof type calls submit_proof() |
| `TestAddAttachment::test_file_calls_post_comment` | file type calls post_comment() |
| `TestAddAttachment::test_proof_without_files` | Proof without file_paths works |
| `TestAddAttachment::test_default_attachment_type_is_file` | Default is file |
| `TestMakeClient::test_defaults` | Default env var values |
| `TestMakeClient::test_env_vars_override` | Env vars override defaults |
| `TestTaskIdEnvDefault::test_add_comment_defaults_task_id_from_env` | task_id defaults to TASKIT_TASK_ID env var |
| `TestTaskIdEnvDefault::test_add_comment_explicit_task_id_overrides_env` | Explicit task_id overrides env var |
| `TestTaskIdEnvDefault::test_add_comment_no_task_id_no_env_returns_error` | No task_id and no env returns error |
| `TestTaskIdEnvDefault::test_add_attachment_defaults_task_id_from_env` | Attachment task_id defaults to env var |
| `TestTaskIdEnvDefault::test_add_attachment_no_task_id_no_env_returns_error` | No task_id and no env returns error |

### test_taskit_mcp_comment_type.py — Comment type taxonomy in MCP

| Test | What it checks |
|---|---|
| `test_comment_type_enum_values` | CommentType enum has status_update and question (no telemetry) |
| `test_status_update_calls_post_comment_with_type` | status_update type passes comment_type="status_update" to client |
| `test_string_comment_type_works` | String comment_type accepted (FastMCP compatibility) |

### test_tool_client_comment_type.py — Tool client comment_type parameter

| Test | What it checks |
|---|---|
| `test_post_comment_sends_comment_type` | post_comment includes comment_type in JSON payload |
| `test_post_comment_defaults_to_status_update` | post_comment without arg sends status_update |

### test_taskit_tool.py — TaskIt tool client and CLI

| Test | What it checks |
|---|---|
| `TestTaskItToolClientPostComment::test_post_comment_calls_correct_api` | POST to /tasks/:id/comments/ |
| `TestTaskItToolClientAsk::test_ask_no_wait` | Question without wait returns immediately |
| `TestTaskItToolClientAsk::test_ask_with_wait_gets_reply` | Question with wait polls and returns reply |
| `TestTaskItToolClientAsk::test_ask_timeout` | Timeout returns None |
| `TestTaskItToolClientProof::test_submit_proof` | Proof with files/steps/handover |
| `TestTaskItToolClientProof::test_submit_proof_minimal` | Minimal proof (summary only) |
| `TestTaskItToolClientContext::test_get_context` | GET /tasks/:id/detail/ |
| `TestClientFromEnv::test_reads_env_vars` | client_from_env resolves env vars |
| `TestClientFromEnv::test_missing_task_id_raises` | Missing TASKIT_TASK_ID raises |
| `TestPollEdgeCases::test_poll_with_malformed_attachments` | Non-dict attachments don't crash polling |
| `TestPollEdgeCases::test_poll_with_empty_attachments` | Empty attachments skipped gracefully |
| `TestPollEdgeCases::test_poll_with_missing_reply_to_field` | Missing reply_to doesn't match |
| `TestPollEdgeCases::test_ask_question_http_error_raises` | POST /question/ 500 propagates |
| `TestPollEdgeCases::test_poll_http_error_raises` | GET poll 500 propagates |
| `TestPollEdgeCases::test_ask_question_auth_failure` | 401 on POST raises |
| `TestCLIComment::test_cli_comment_command` | CLI comment subcommand |
| `TestCLIProof::test_cli_proof_command` | CLI proof subcommand |
| `TestCLIProof::test_cli_proof_with_handover` | CLI proof with handover |
| `TestCLIAsk::test_cli_ask_no_wait` | CLI ask subcommand |
| `TestCLIContext::test_cli_context_command` | CLI context subcommand |
| `TestCLIMissingEnv::test_cli_exits_on_missing_task_id` | Missing task_id exits |

### test_reflection.py — Reflection prompt builder and report parser

| Test | What it checks |
|---|---|
| `TestBuildReflectionPrompt::test_prompt_contains_readonly_instruction` | READ-ONLY mode instruction present |
| `TestBuildReflectionPrompt::test_prompt_includes_task_title_and_description` | Task title and description in prompt |
| `TestBuildReflectionPrompt::test_prompt_includes_execution_output` | Execution output section populated |
| `TestBuildReflectionPrompt::test_prompt_includes_dependent_tasks` | Dependent tasks listed |
| `TestBuildReflectionPrompt::test_prompt_includes_custom_prompt_when_provided` | Custom prompt injected |
| `TestBuildReflectionPrompt::test_prompt_omits_custom_prompt_section_when_empty` | No ADDITIONAL INSTRUCTIONS when empty |
| `TestBuildReflectionPrompt::test_prompt_includes_agent_and_model_info` | Agent and model in context |
| `TestBuildReflectionPrompt::test_prompt_includes_section_headers` | All 5 report section headers present |
| `TestParseReflectionReport::test_parse_extracts_all_five_sections` | All sections extracted from well-formed output |
| `TestParseReflectionReport::test_parse_extracts_verdict_pass` | PASS verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_needs_work` | NEEDS_WORK verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_fail` | FAIL verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_summary` | Summary text after verdict enum extracted |
| `TestParseReflectionReport::test_parse_handles_missing_sections_gracefully` | Missing sections → empty strings |
| `TestParseReflectionReport::test_parse_handles_empty_output` | Empty string → all empty fields |
| `TestParseReflectionReport::test_parse_handles_no_headers` | Plain text → empty structured fields |

### test_specs.py — Spec pure functions

| Test | What it checks |
|---|---|
| `TestDeriveSpecStatus::test_abandoned_overrides_everything` | abandoned -> "abandoned" |
| `TestDeriveSpecStatus::test_empty_tasks` | No tasks -> "empty" |
| `TestDeriveSpecStatus::test_all_completed_is_done` | All DONE -> "done" |
| `TestDeriveSpecStatus::test_any_in_progress_is_active` | Any IN_PROGRESS -> "active" |
| `TestDeriveSpecStatus::test_any_failed_none_running_is_blocked` | FAILED+DONE -> "blocked" |
| `TestDeriveSpecStatus::test_in_progress_beats_failed` | IN_PROGRESS wins over FAILED |
| `TestDeriveSpecStatus::test_some_completed_some_assigned_is_partial` | DONE+TODO -> "partial" |
| `TestDeriveSpecStatus::test_all_assigned_is_planned` | All TODO -> "planned" |
| `TestDeriveSpecStatus::test_all_pending_is_draft` | All BACKLOG -> "draft" |
| `TestSpecShortTag::test_file_path` | File path -> short label |
| `TestSpecShortTag::test_inline_prompt` | Free text -> truncated label |
| `TestSpecShortTag::test_heading` | Heading -> non-empty label |

---

## Disk Tests (disk/)

### test_cost_tracking.py — Cost persistence and summarization

| Test | What it checks |
|---|---|
| `TestTaskCostRecord::test_minimal_creation` | Record with only task_id has sensible defaults |
| `TestTaskCostRecord::test_full_creation` | Record with all fields stores them correctly |
| `TestCostStore::test_save_and_load` | Record saved and reloaded by spec_id |
| `TestCostStore::test_multiple_records_same_spec` | Three records for same spec all retrieved |
| `TestCostStore::test_load_all_across_specs` | load_all() returns records across specs |
| `TestCostStore::test_load_empty_spec` | Nonexistent spec returns [] |
| `TestCostStore::test_orphan_tasks_use_underscore_orphan` | spec_id=None -> costs__orphan.json |
| `TestCostStore::test_corrupt_json_returns_empty` | Corrupt JSON returns [] |
| `TestCostStoreSummarize::test_summarize_spec` | Aggregates counts, tokens, duration |
| `TestCostStoreSummarize::test_summarize_all` | One summary per spec |
| `TestCostStoreSummarize::test_summarize_empty_spec` | Nonexistent spec returns zeroed summary |
| `TestCostTracker::test_record_task` | Extracts tokens from anthropic-style metadata |
| `TestCostTracker::test_record_task_no_usage` | No metadata -> None token fields |
| `TestCostTracker::test_record_task_openai_style_tokens` | prompt_tokens/completion_tokens handled |


### test_logging.py — Structured JSONL logging

| Test | What it checks |
|---|---|
| `TestOdinLogger::test_creates_log_file` | log() creates the file |
| `TestOdinLogger::test_log_entry_is_valid_json` | Valid JSON with correct fields |
| `TestOdinLogger::test_log_has_timestamp` | Each entry has timestamp |
| `TestOdinLogger::test_log_with_task_id_and_agent` | Optional fields included |
| `TestOdinLogger::test_none_values_excluded` | Absent fields omitted from JSON |
| `TestOdinLogger::test_output_truncation` | Output > 2000 chars truncated |
| `TestOdinLogger::test_multiple_entries_appended` | Multiple calls append JSONL lines |
| `TestOdinLogger::test_duration_ms_recorded` | duration_ms serialized correctly |

### test_specs_io.py — Spec I/O (SpecStore, multi-spec coexistence)

| Test | What it checks |
|---|---|
| `TestSpecStore::test_save_and_load` | Save and reload by ID |
| `TestSpecStore::test_load_all` | load_all returns all saved specs |
| `TestSpecStore::test_set_abandoned` | set_abandoned persists |
| `TestSpecStore::test_resolve_prefix` | Prefix resolution works |
| `TestSpecStore::test_load_nonexistent` | Nonexistent returns None |
| `TestMultiSpecCoexistence::test_tasks_from_different_specs` | Tasks filtered per spec_id |
| `TestMultiSpecCoexistence::test_abandoned_spec_excluded_from_exec` | Abandoned tasks excluded |
| `TestMultiSpecCoexistence::test_tasks_without_spec_id_still_work` | Legacy tasks still visible |

### test_taskit.py — Local task manager

| Test | What it checks |
|---|---|
| `TestTaskManagerCRUD::test_create_and_get` | Create assigns ID, title, BACKLOG status |
| `TestTaskManagerCRUD::test_create_with_metadata` | Metadata persisted |
| `TestTaskManagerCRUD::test_create_with_spec_id` | spec_id persisted |
| `TestTaskManagerCRUD::test_list_tasks_empty` | Empty store returns [] |
| `TestTaskManagerCRUD::test_list_tasks_returns_all` | All tasks returned |
| `TestTaskManagerCRUD::test_delete_task` | Deleted task gone |
| `TestTaskManagerCRUD::test_delete_nonexistent` | Bad ID returns False |
| `TestTaskManagerCRUD::test_get_nonexistent` | Bad ID returns None |
| `TestTaskLifecycle::test_backlog_to_todo` | assign_task: BACKLOG -> TODO |
| `TestTaskLifecycle::test_todo_to_in_progress` | update_status: TODO -> IN_PROGRESS |
| `TestTaskLifecycle::test_in_progress_to_done` | Transition to DONE with result |
| `TestTaskLifecycle::test_in_progress_to_failed` | Transition to FAILED |
| `TestTaskLifecycle::test_assign_nonexistent_returns_none` | Bad task ID returns None |
| `TestTaskLifecycle::test_update_status_nonexistent_returns_none` | Bad task ID returns None |
| `TestPrefixResolution::test_unique_prefix_resolves` | 4-char prefix resolves |
| `TestPrefixResolution::test_ambiguous_prefix_returns_none` | Empty prefix returns None |
| `TestPrefixResolution::test_no_match_returns_none` | Unmatched prefix returns None |
| `TestTaskFiltering::test_filter_by_status` | list_tasks(status=...) filters |
| `TestTaskFiltering::test_filter_by_agent` | list_tasks(agent=...) filters |
| `TestTaskFiltering::test_filter_by_spec_id` | list_tasks(spec_id=...) filters |
| `TestTaskComments::test_add_comment` | Comment added with author |
| `TestTaskComments::test_add_multiple_comments` | Two comments persist |
| `TestTaskComments::test_comment_on_nonexistent_returns_none` | Bad task returns None |
| `TestIndexConsistency::test_index_updated_on_create` | index.json includes new task |
| `TestIndexConsistency::test_index_updated_on_delete` | index.json removes task |
| `TestIndexConsistency::test_index_reflects_status_change` | index.json reflects assignment |
| `TestReadyTasks::test_no_deps_all_ready` | Independent tasks all ready |
| `TestReadyTasks::test_dep_blocks_task` | Unmet dep blocks task |
| `TestReadyTasks::test_dep_satisfied_unblocks_task` | Completing dep unblocks |
| `TestReadyTasks::test_backlog_tasks_not_ready` | Unassigned not ready |

---

## Mock Tests (mock/)

### test_streaming.py — Streaming output delivery

| Test | What it checks |
|---|---|
| `TestHarnessStreaming::test_streaming_yields_chunks_incrementally` | Chunks arrive over time (x4 harnesses) |
| `TestHarnessStreaming::test_streaming_chunk_order_preserved` | Chunk order matches subprocess (x4) |
| `TestHarnessStreaming::test_streaming_callback_called_per_chunk` | Callback per chunk with timing (x4) |
| `TestHarnessStreaming::test_streaming_handles_cli_not_found` | Missing CLI -> error chunk |
| `TestHarnessStreaming::test_streaming_empty_output` | Empty output -> zero chunks |
| `TestBaseHarnessFallbackStreaming::test_fallback_yields_full_output_once` | BaseHarness yields all as one chunk |
| `TestBaseHarnessFallbackStreaming::test_fallback_yields_nothing_for_empty_output` | Empty -> zero chunks |
| `TestDecomposeStreaming::test_decompose_streams_via_callback` | _decompose callback is incremental |
| `TestDecomposeStreaming::test_decompose_without_callback_uses_execute` | No callback -> execute() |
| `TestDecomposeStreaming::test_decompose_streaming_accumulates_full_output` | Multi-line JSON assembled |
| `TestPlanStreaming::test_plan_with_callback_streams_incrementally` | plan() passes callback through |
| `TestPlanStreaming::test_plan_without_callback_does_not_stream` | No callback -> no streaming |
| `TestCLIStreamChunk::test_stream_chunk_writes_and_flushes` | Chunks written in order |
| `TestCLIStreamChunk::test_stream_chunk_preserves_partial_lines` | Partial lines preserved |
| `TestStreamingTimingBehavior::test_streaming_first_chunk_arrives_before_last` | First chunk before last |
| `TestStreamingTimingBehavior::test_non_streaming_delivers_all_at_once` | execute() returns single block |

### test_taskit_backend.py — TaskIt REST backend (mocked HTTP)

| Test | What it checks |
|---|---|
| `TestTaskItAuthLogin::test_login_returns_token` | Successful login returns JWT |
| `TestTaskItAuthLogin::test_login_caches_token` | No duplicate HTTP calls |
| `TestTaskItAuthLogin::test_login_sends_correct_payload` | email/password in POST body |
| `TestTaskItAuthLogin::test_login_bad_credentials_raises_with_guidance` | 401 -> TaskItAuthError |
| `TestTaskItAuthLogin::test_login_server_error_raises` | 500 -> TaskItAuthError |
| `TestTaskItAuthLogin::test_login_connection_error_raises_with_guidance` | Connection refused -> guidance |
| `TestTaskItAuthLogin::test_login_when_auth_disabled_returns_empty_token` | Auth disabled -> empty token |
| `TestTaskItAuthExpiry::test_re_login_when_token_expired` | Expired -> re-login |
| `TestTaskItAuthExpiry::test_re_login_when_near_expiry` | Near expiry -> proactive re-login |
| `TestTaskItAuthExpiry::test_no_re_login_when_token_valid` | Valid token reused |
| `TestTaskItAuthExpiry::test_expiry_set_from_response` | expires_in -> _expires_at |
| `TestTaskItAuthFlow::test_auth_flow_injects_bearer_header` | Bearer header added |
| `TestTaskItBackendAuth::test_backend_without_auth_has_no_auth_handler` | No creds -> no auth |
| `TestTaskItBackendAuth::test_backend_with_auth_has_taskit_auth` | Creds -> TaskItAuth |
| `TestTaskItBackendAuth::test_backend_partial_auth_config_no_auth` | Partial creds -> no auth |
| `TestTaskItBackendAuth::test_auth_login_url_constructed_from_base_url` | URL = base_url/auth/login/ |
| `TestTaskItBackendAuth::test_authenticated_request_includes_bearer_token` | Bearer in requests |
| `TestTaskItBackendCRUD::test_save_new_task` | POST creates task |
| `TestTaskItBackendCRUD::test_save_existing_task` | PUT updates task |
| `TestTaskItBackendCRUD::test_load_task_found` | GET returns task |
| `TestTaskItBackendCRUD::test_load_task_not_found` | 404 returns None |
| `TestTaskItBackendCRUD::test_delete_task_success` | 204 returns True |
| `TestTaskItBackendCRUD::test_delete_task_not_found` | 404 returns False |
| `TestTaskItBackendCRUD::test_load_all_tasks` | List with agent resolution |
| `TestGetComments::test_get_comments_returns_list` | GET /tasks/:id/comments/ returns comment list |
| `TestGetComments::test_get_comments_handles_paginated_response` | Paginated DRF response unwrapped |
| `TestGetComments::test_get_comments_empty` | Empty list for no comments |
| `TestTaskItConfigFromEnv::test_env_vars_populate_taskit_config` | Env vars populate config |
| `TestTaskItConfigFromEnv::test_env_vars_not_set_leaves_defaults` | Absent env vars -> None |
| `TestTaskItConfigFromEnv::test_env_vars_override_yaml_config` | Env vars override YAML |
| `TestPaginatedResponseHandling::test_save_spec_with_paginated_response` | save_spec handles DRF paginated dict |
| `TestPaginatedResponseHandling::test_load_spec_with_paginated_response` | load_spec handles DRF paginated dict |
| `TestPaginatedResponseHandling::test_load_spec_paginated_empty` | load_spec returns None for empty paginated results |
| `TestPaginatedResponseHandling::test_load_all_tasks_with_paginated_response` | load_all_tasks handles DRF paginated dict |
| `TestPaginatedResponseHandling::test_load_all_specs_with_paginated_response` | load_all_specs handles DRF paginated dict |
| `TestPaginatedResponseHandling::test_set_spec_abandoned_with_paginated_response` | set_spec_abandoned handles DRF paginated dict |
| `TestPaginatedResponseHandling::test_delete_spec_with_paginated_response` | delete_spec handles DRF paginated dict |

### test_tmux.py — Tmux session management

| Test | What it checks |
|---|---|
| `TestSessionName::test_format` | Session name uses first 8 chars of ID |
| `TestSessionName::test_short_id` | Short ID uses full ID |
| `TestSessionName::test_prefix` | Starts with SESSION_PREFIX |
| `TestIsAvailable::test_returns_true_when_tmux_on_path` | True when tmux found |
| `TestIsAvailable::test_returns_false_when_missing` | False when tmux missing |
| `TestWrapperScriptContent::test_script_has_pipefail` | set -o pipefail present |
| `TestWrapperScriptContent::test_script_has_tee` | tee for output capture |
| `TestWrapperScriptContent::test_script_has_exit_marker` | Exit code + .exit marker |
| `TestWrapperScriptContent::test_script_with_env_unset` | unset lines present |
| `TestWrapperScriptContent::test_script_without_env_unset` | No unset when not needed |
| `TestWrapperScriptContent::test_script_escapes_command` | shlex.join escaping |
| `TestLaunchCreatesScript::test_script_file_exists` | launch() writes executable |
| `TestLaunchCreatesScript::test_launch_returns_session_name` | Returns session name |
| `TestLaunchCreatesScript::test_launch_raises_on_tmux_failure` | Non-zero exit raises error |
| `TestTmuxReal::test_launch_echo_and_wait` | [tmux_real] Real echo runs |
| `TestTmuxReal::test_has_session_lifecycle` | [tmux_real] Session exists/gone |
| `TestTmuxReal::test_exit_code_capture` | [tmux_real] Exit code captured |
| `TestTmuxReal::test_kill_nonexistent_returns_false` | [tmux_real] Kill absent -> False |

### test_comments.py — Comment bridge, actor identity, metrics composition

| Test | What it checks |
|---|---|
| `TestTaskItBackendAddComment::test_add_comment_posts_to_correct_url` | POST to /tasks/:id/comments/ |
| `TestTaskItBackendAddComment::test_add_comment_sends_attachments` | Attachments included in payload |
| `TestTaskItBackendAddComment::test_add_comment_raises_on_http_error` | HTTP error raises exception |
| `TestActorIdentity::test_agent_with_model_email` | agent+model → {agent}+{model}@odin.agent |
| `TestActorIdentity::test_agent_only_email` | agent only → {agent}@odin.agent |
| `TestActorIdentity::test_odin_system_email` | odin → odin@harness.kit |
| `TestActorIdentity::test_agent_with_model_label` | agent+model → "agent (model)" |
| `TestActorIdentity::test_agent_only_label` | agent only → "agent" |
| `TestComposeComment::test_duration_and_tokens` | "Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)" |
| `TestComposeComment::test_duration_only` | Duration without token metrics |
| `TestComposeComment::test_no_metrics` | Summary only when no metrics |
| `TestComposeComment::test_failed_verb` | "Failed in ..." prefix |
| `TestComposeComment::test_alternative_token_keys` | prompt_tokens/completion_tokens handled |
| `TestTaskManagerCommentRouting::test_routes_through_backend` | Backend.add_comment() called |
| `TestTaskManagerCommentRouting::test_falls_back_to_local_disk` | Local disk when no backend |

### test_mock_harness.py — Mock harness for testing without real LLMs

| Test | What it checks |
|---|---|
| `TestMockHarness::test_mock_registered` | "mock" in HARNESS_REGISTRY |
| `TestMockHarness::test_execute_returns_success` | TaskResult.success=True |
| `TestMockHarness::test_output_contains_odin_envelope` | ODIN-STATUS/ODIN-SUMMARY in output |
| `TestMockHarness::test_is_available` | Always returns True |
| `TestMockHarness::test_build_execute_command_raises` | NotImplementedError (no subprocess) |
| `TestMockHarness::test_metadata_has_token_breakdown` | usage.input_tokens + output_tokens = total_tokens |

### test_question_poll_roundtrip.py — Question→poll→reply cycle (mocked HTTP)

| Test | What it checks |
|---|---|
| `TestQuestionEndpoint::test_question_posts_to_question_endpoint` | POSTs to /tasks/:id/question/ (not /comments/) |
| `TestPollEndpoint::test_poll_hits_comments_after_endpoint` | GETs /tasks/:id/comments/?after=<id> |
| `TestPollReplyMatching::test_poll_finds_reply_by_attachment_type` | Reply detected via attachment type + reply_to |
| `TestPollReplyMatching::test_poll_ignores_non_reply_comments` | Status updates don't satisfy poll |
| `TestPollReplyMatching::test_poll_ignores_reply_to_different_question` | Reply to wrong question is skipped |
| `TestPollTimingBehavior::test_indefinite_poll_no_deadline` | timeout=0 polls indefinitely |
| `TestPollTimingBehavior::test_poll_interval_is_5_seconds` | sleep(5) between poll attempts |
| `TestMcpReturnFormat::test_mcp_question_returns_reply_content` | Full result has id + reply |
| `TestMcpReturnFormat::test_mcp_question_blocks_until_reply` | Multiple empty polls before reply |
| `TestErrorPropagation::test_network_error_during_poll_propagates` | HTTP 500 during poll raises |

### test_context_injection.py — Upstream context injection in exec_task()

| Test | What it checks |
|---|---|
| `TestContextInjection::test_exec_task_injects_upstream_comments` | Completed dep's comment injected into downstream prompt |
| `TestContextInjection::test_exec_task_no_injection_without_deps` | No deps → original description unchanged |
| `TestContextInjection::test_exec_task_skips_incomplete_deps` | IN_PROGRESS deps → task blocked (WAITING) |
| `TestContextInjection::test_exec_task_merges_multiple_upstream` | A+B→C: both upstream comments in C's prompt |

### test_mock_mode.py — Mock mode execution and EXECUTING status

| Test | What it checks |
|---|---|
| `TestMockModeExecution::test_mock_no_status_writes` | Mock mode does not change task status |
| `TestMockModeExecution::test_mock_no_comments` | Mock mode does not post comments |
| `TestMockModeExecution::test_mock_returns_result` | Mock mode still runs harness and returns result |
| `TestMockModeExecution::test_mock_executing_transition_skipped` | Task stays in original status (no EXECUTING) |
| `TestMockModeExecution::test_mock_no_cost_tracking` | Mock mode does not record cost data |
| `TestExecutingStatusTransition::test_normal_exec_sets_executing` | Normal exec sets EXECUTING (not IN_PROGRESS) |
| `TestExecutingStatusTransition::test_already_executing_skips_transition` | Already EXECUTING → skip transition |

### test_planning.py — Artifact-aware planning

| Test | What it checks |
|---|---|
| `TestArtifactAwarePlanning::test_expected_outputs_stored_in_metadata` | expected_outputs persisted in task.metadata |
| `TestArtifactAwarePlanning::test_assumptions_posted_as_initial_comment` | Assumptions posted as first comment on task |
| `TestArtifactAwarePlanning::test_no_assumption_comment_when_empty` | No comment when assumptions list empty |
| `TestArtifactAwarePlanning::test_decomposition_prompt_includes_artifact_rules` | ARTIFACT COORDINATION RULES in prompt |

### test_execution_logging.py — Execution I/O debug comments

| Test | What it checks |
|---|---|
| `TestExecutionDebugComments::test_debug_comments_posted_during_execution` | debug:effective_input and debug:full_output comments posted |
| `TestExecutionDebugComments::test_debug_effective_input_includes_upstream_context` | Injected upstream context appears in debug input comment |
| `TestExecutionDebugComments::test_execution_result_includes_effective_input` | effective_input in execution_result payload |
| `TestExecutionDebugComments::test_debug_output_truncated_at_8000` | Debug content truncated at 8000 chars |

### test_e2e_comments.py — End-to-end comment pipeline (mock harness → TaskIt)

| Test | What it checks |
|---|---|
| `TestE2ECommentPipeline::test_mock_harness_through_orchestrator_compose` | Mock exec → parse envelope → compose comment |
| `TestE2ECommentPipeline::test_full_pipeline_posts_comment_to_taskit` | Full pipeline with mocked HTTP, correct actor email |
| `TestE2ECommentPipeline::test_failed_task_posts_failure_comment` | Failed result → failure comment with metrics |
| `TestE2ECommentPipeline::test_odin_system_comment_uses_harness_kit_email` | Odin system comment → odin@harness.kit |

### test_trace_logging.py — Trace files and JSON stream extraction

| Test | What it checks |
|---|---|
| `TestBuildCommandOutputFormat::test_claude_uses_stream_json_verbose` | --output-format stream-json --verbose |
| `TestBuildCommandOutputFormat::test_gemini_uses_stream_json` | --output-format stream-json |
| `TestBuildCommandOutputFormat::test_qwen_uses_stream_json` | --output-format stream-json |
| `TestBuildCommandOutputFormat::test_minimax_uses_format_json` | --format json |
| `TestBuildCommandOutputFormat::test_glm_uses_format_json` | --format json |
| `TestBuildCommandOutputFormat::test_codex_has_no_output_format` | No format flag |
| `TestExtractTextFromLine::test_claude_content_block_delta` | content_block_delta -> .delta.text |
| `TestExtractTextFromLine::test_claude_result` | result event -> .result |
| `TestExtractTextFromLine::test_gemini_text_event` | text event -> .text |
| `TestExtractTextFromLine::test_opencode_step_finish` | step_finish -> .content |
| `TestExtractTextFromLine::test_unknown_type_returns_empty` | Unknown type -> "" |
| `TestExtractTextFromLine::test_non_json_returns_line` | Non-JSON -> passthrough |
| `TestExtractTextFromLine::test_empty_line_returns_empty` | Empty -> "" |
| `TestExtractTextFromLine::test_content_block_delta_empty_text` | Empty text -> "" |
| `TestExtractTextFromStream::test_claude_stream` | Multi-line Claude JSON -> joined text |
| `TestExtractTextFromStream::test_gemini_stream` | Multi-line Gemini JSON -> joined text |
| `TestExtractTextFromStream::test_plain_text_passthrough` | Non-JSON -> unchanged |
| `TestExtractTextFromStream::test_empty_input` | Empty -> "" |
| `TestExtractTextFromStream::test_all_non_text_events_returns_raw` | Non-text events -> raw |
| `TestReadWithTrace::test_writes_trace_and_output_files` | Trace + output files written |
| `TestReadWithTrace::test_handles_non_text_events` | Non-text in trace, not output |
| `TestExecuteWithTrace::test_claude_execute_writes_trace` | Claude execute writes trace |
| `TestExecuteWithTrace::test_execute_without_trace_still_extracts_text` | No trace file still extracts |
| `TestExecuteWithTrace::test_codex_execute_unchanged` | Codex plain text passthrough |

### TaskIt DAG Executor (taskit-backend/tests/test_dag_executor.py)

These tests live in the taskit-backend, not in odin's test tree, but cover Odin-related DAG execution logic.

| Test | What it checks |
|---|---|
| `DepsSatisfiedTests::test_no_deps_always_satisfied` | No deps → always satisfied |
| `DepsSatisfiedTests::test_all_deps_done` | All DONE → satisfied |
| `DepsSatisfiedTests::test_review_counts_as_satisfied` | REVIEW counts as satisfied |
| `DepsSatisfiedTests::test_partial_deps_not_satisfied` | Mixed → not satisfied |
| `DepsSatisfiedTests::test_deps_in_todo_not_satisfied` | TODO dep → not satisfied |
| `DepsSatisfiedTests::test_deps_executing_not_satisfied` | EXECUTING dep → not satisfied |
| `DepsFailedTests::test_failed_dep_detected` | Failed dep detected |
| `DepsFailedTests::test_no_deps_not_failed` | No deps → not failed |
| `DepsFailedTests::test_done_dep_not_failed` | DONE dep → not failed |
| `PollAndExecuteTests::test_transitions_ready_task_to_executing` | Ready task → EXECUTING |
| `PollAndExecuteTests::test_skips_unassigned_tasks` | No assignee → skip |
| `PollAndExecuteTests::test_skips_unsatisfied_deps` | Unsatisfied deps → skip |
| `PollAndExecuteTests::test_skips_failed_deps` | Failed deps → skip |
| `PollAndExecuteTests::test_respects_concurrency_limit` | Max N executing |
| `PollAndExecuteTests::test_poll_does_nothing_when_no_candidates` | No candidates → no-op |
| `ExecuteSingleTaskTests::test_success_transitions_to_review` | Success → REVIEW |
| `ExecuteSingleTaskTests::test_failure_transitions_to_failed` | Failure → FAILED |
| `ExecuteSingleTaskTests::test_timeout_transitions_to_failed` | Timeout → FAILED |
| `ExecuteSingleTaskTests::test_skips_non_executing_task` | Non-EXECUTING → skip |
| `ExecuteSingleTaskTests::test_respects_odin_status_update` | Odin's update preserved |
| `ExecuteSingleTaskTests::test_nonexistent_task_handled` | Missing task → no crash |

### test_reflect_command.py — Reflection task orchestration (mocked HTTP + harness)

| Test | What it checks |
|---|---|
| `TestReflectTask::test_updates_report_to_running` | PATCHes report status to RUNNING |
| `TestReflectTask::test_gathers_context_from_api` | Fetches task detail from TaskIt API |
| `TestReflectTask::test_calls_harness_with_working_dir` | Harness receives working_dir from metadata |
| `TestReflectTask::test_submits_parsed_report` | PATCHes report with COMPLETED + parsed sections |
| `TestReflectTask::test_report_has_correct_sections` | Parsed result includes all 5 sections + verdict |
| `TestReflectTask::test_handles_harness_failure` | Harness error → FAILED report with error_message |
| `TestReflectTask::test_posts_failed_status_on_error` | HTTP error → FAILED status posted |
| `TestReflectTask::test_custom_model_override` | Custom model passed through to harness |
| `TestReflectTask::test_patches_assembled_prompt` | RUNNING patch includes assembled_prompt with full reviewer prompt |

---

## Integration Tests (integration/)

Excluded from default run (`addopts = "--ignore=tests/integration"` in pyproject.toml).

### test_taskit_mcp_live.py — MCP server against live TaskIt backend

Requires TaskIt running at `TASKIT_URL`. Loads credentials from `odin/temp_test_dir/.env`.

| Test | What it checks |
|---|---|
| `TestCommentViaMCP::test_post_status_update` | MCP tool posts comment, visible in TaskIt API |
| `TestCommentViaMCP::test_post_question_creates_question_comment` | Question creates comment with pending attachment |
| `TestCommentViaMCP::test_question_reply_roundtrip` | Question → human reply → poll finds reply |
| `TestAttachmentViaMCP::test_post_proof` | Proof attachment created with files metadata |
| `TestAttachmentViaMCP::test_post_file_attachment` | File attachment created |

### test_real.py — Real CLI agent integration tests

Requires `gemini`, `qwen`, `codex` CLIs on PATH.

```bash
python -m pytest tests/integration/ -v
```

| Test | What it checks |
|---|---|
| `TestHarnessAvailability::test_harness_is_available` | gemini/qwen/codex CLIs on PATH (x3) |
| `TestHarnessAvailability::test_all_expected_harnesses_registered` | All 6 harness names in registry |
| `TestSingleHarnessExecute::test_gemini_returns_output` | Real gemini call returns output |
| `TestSingleHarnessExecute::test_qwen_returns_output` | Real qwen call returns output |
| `TestDecomposition::test_decompose_returns_valid_subtasks` | Codex decomposes spec into subtasks |
| `TestFullPoemE2E::test_poem_html_generated` | Full pipeline produces poem.html |
| `TestPlanOnly::test_plan_creates_tasks_without_executing` | plan() creates tasks, no execution |
| `TestExecSingleTask::test_exec_single_task_by_id` | exec_task() completes one task |
| `TestAssembleSeparately::test_staged_plan_exec` | Staged plan->exec_task transitions status |
| `TestReassign::test_reassign_changes_agent` | assign_task() changes agent |
| `TestDiskWriteCapability::test_codex_can_write_file` | Codex creates file on disk |
| `TestDiskWriteCapability::test_gemini_can_write_file` | Gemini creates file on disk |
| `TestDiskWriteCapability::test_qwen_can_write_file` | Qwen creates file on disk |

### test_mobile_mcp_live.py — Mobile MCP + TaskIt integration

Requires Android emulator or iOS Simulator running, TaskIt backend healthy, npx available.

```bash
python -m pytest tests/integration/test_mobile_mcp_live.py -v
```

| Test | What it checks |
|---|---|
| `TestMobileListDevices::test_mobile_list_devices` | Mobile MCP lists running emulators |
| `TestMobileScreenshot::test_mobile_screenshot_saves_to_file` | Screenshot saved as PNG |
| `TestMobileScreenshotToTaskitProof::test_mobile_screenshot_to_taskit_proof` | Full flow: screenshot → TaskIt proof |
