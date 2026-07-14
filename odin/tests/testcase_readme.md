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
    test_doctor.py         # `odin doctor`: service/agent/sandbox/host probes, capability matrix
    test_cost_estimator.py # Pricing table loading, cost estimation from tokens
    test_dag.py            # DAG validation (cycle detection), wave grouping, envelope parsing
    test_merge_agent.py    # Merge-conflict classifier, in-worktree resolution, comment formatters, additive-non-overlapping auto-resolve gate (task 254)
    test_reflection.py     # Reflection prompt builder and report parser
    test_agent_routing.py  # Pure suggester: cheapest-capable ranks, escalation, thin-history fallback
    test_route_task_suggester.py # Orchestrator wiring of the suggester into _route_task tier distribution
    test_fetch_agent_stats.py # TaskIt client for /boards/{id}/agent-stats/ REST endpoint
    test_routing.py        # Agent routing: suggestion respected/fallback, quota awareness
    test_self_audit_script.py # scripts/self_audit_diff.sh replay vs task-170 fixture (local git)
    test_specs.py          # derive_spec_status, spec_short_tag

  disk/                    # Disk I/O only — no network, no subprocesses
    test_cost_tracking.py  # CostStore/CostTracker persistence and summarization
    test_logging.py        # StructuredLogger JSONL output
    test_specs_io.py       # SpecStore CRUD, multi-spec coexistence
    test_taskit.py         # TaskManager CRUD, lifecycle, prefix resolution, filtering
    test_merge_agent_disk.py # Real-git replay of task 249's additive conflict (auto-merges) + modify-vs-add (still parks)

  mock/                    # Mocked subprocesses/HTTP — no real services
    test_comments.py       # Comment bridge, actor identity, metrics composition
    test_context_injection.py # Upstream context injection in exec_task()
    test_e2e_comments.py   # End-to-end comment pipeline (mock harness → TaskIt)
    test_execution_logging.py # Execution I/O debug comments
    test_harness_subprocess_errors.py # Harness timeout, non-zero exit, HTTP errors (all 6 harnesses)
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
    test_real.py           # Real CLI agent integration tests (gemini, codex)
```

---

## Unit Tests (unit/)

### test_config.py — Configuration system

| Test | What it checks |
|---|---|
| `TestDefaultConfig::test_default_config_has_agents` | Built-in defaults include claude, gemini |
| `TestDefaultConfig::test_default_config_base_agent` | Default base agent is "claude" |
| `TestDefaultConfig::test_default_config_model_routing` | Default config has ModelRoute entries |
| `TestDefaultConfig::test_default_config_source` | config_source field set from source argument |
| `TestDefaultConfig::test_default_agent_cost_tiers` | Claude=HIGH, gemini=LOW |
| `TestDefaultConfig::test_default_cli_agents_enabled` | minimax and glm agents enabled by default |
| `TestYAMLLoading::test_load_from_yaml` | YAML with base_agent and agents loads correctly |
| `TestYAMLLoading::test_empty_yaml_returns_defaults` | Empty YAML falls back to defaults |
| `TestYAMLLoading::test_max_turns_defaults_none` | Unset max_turns leaves the agent loop unbounded |
| `TestYAMLLoading::test_max_turns_parsed_from_yaml` | max_turns step budget parsed from config |
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
| `TestLoadPricingTable::test_unknown_model_has_none_prices` | coder-model has None pricing |
| `TestLoadPricingTable::test_loads_from_minimal_json` | Custom JSON loads correctly |
| `TestEstimateCost::test_known_model` | 1000 in / 500 out on claude-sonnet-4-5 → $0.0105 |
| `TestEstimateCost::test_unknown_model` | Null pricing returns None |
| `TestEstimateCost::test_missing_model` | Model not in table returns None |
| `TestEstimateCost::test_zero_tokens` | 0/0 tokens → $0.00 |
| `TestEstimateCost::test_null_tokens` | None tokens → None |
| `TestEstimateCost::test_partial_null_tokens` | One None token → None |
| `TestEstimateCost::test_large_token_count` | 100k/50k on gemini-2.5-flash → $0.045 |

### test_project_notes.py — Durable per-project notes reader

| Test | What it checks |
|---|---|
| `TestReadProjectNotes::test_returns_empty_when_no_working_dir` | Empty string when no working dir |
| `TestReadProjectNotes::test_returns_empty_when_file_absent` | Empty when PROJECT_NOTES.md missing |
| `TestReadProjectNotes::test_reads_default_path` | Reads default PROJECT_NOTES.md at project root |
| `TestReadProjectNotes::test_respects_configured_relative_path` | Honors config-relative path (e.g. docs/PROJECT_NOTES.md) |
| `TestReadProjectNotes::test_returns_empty_for_empty_file` | Whitespace-only file yields empty |
| `TestReadProjectNotes::test_strips_surrounding_whitespace` | Leading/trailing whitespace stripped |
| `TestReadProjectNotes::test_caps_at_max_chars_keeping_newest` | Over-cap content keeps newest entries (bottom) |
| `TestReadProjectNotes::test_truncation_adds_consolidate_note` | Truncation prepends a "consolidate" note |
| `TestReadProjectNotes::test_under_cap_passes_through_unchanged` | Under-cap content passes through verbatim |

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
| `TestParseEnvelope::test_wrap_prompt_working_dir_includes_prebaked_python_env_hint` | Pre-baked python env hint injected with working_dir (forbids venv/pip for suites) |
| `TestParseEnvelope::test_wrap_prompt_no_env_hint_without_working_dir` | No env hint when working_dir absent |
| `TestOrientationBlock::test_efficiency_block_always_present_by_default` | Tool-batching/efficiency guidance rides on every wrapped prompt |
| `TestOrientationBlock::test_orient_false_omits_block` | orient=False restores the bare pre-fix prompt |
| `TestOrientationBlock::test_orientation_references_claude_md_when_present` | CLAUDE.md surfaced when it exists in working dir |
| `TestOrientationBlock::test_orientation_references_breadcrumb_index_when_present` | Breadcrumb index surfaced only when it exists |
| `TestOrientationBlock::test_no_orientation_section_when_docs_absent` | Never points agent at docs that aren't there |
| `TestOrientationBlock::test_orientation_ordered_before_task_and_envelope` | Orientation precedes task text and ODIN-STATUS |
| `TestSelfAuditGate::test_gate_section_always_present` | Pre-completion self-audit gate rides on every wrapped prompt (no MCP/working-dir needed) |
| `TestSelfAuditGate::test_gate_present_with_mcp_and_working_dir` | Gate survives full assembly (preamble + MCP + envelope) |
| `TestSelfAuditGate::test_gate_names_both_defect_classes` | Gate names duplicate definitions + commented-out dead code (task-170 failure modes) |
| `TestSelfAuditGate::test_gate_references_mechanical_assist_script` | Gate points at scripts/self_audit_diff.sh |
| `TestSelfAuditGate::test_gate_runs_before_odin_status` | Gate heading says it runs BEFORE ODIN-STATUS |
| `TestSelfAuditGate::test_gate_ordered_after_task_and_before_envelope` | Gate sits after task body, before the ODIN-STATUS envelope separator |
| `TestSelfAuditGate::test_gate_ordered_before_envelope_with_mcp` | Ordering holds across the MCP-laden assembly |
| `TestProjectNotesInjection::test_omitted_when_no_notes` | No notes section when project_notes is empty |
| `TestProjectNotesInjection::test_present_when_notes_provided` | Notes injected when provided |
| `TestProjectNotesInjection::test_section_is_labeled` | Notes section carries a `## Project Notes` label |
| `TestProjectNotesInjection::test_notes_placed_after_brief_before_envelope` | Notes sit after the brief, before ODIN-STATUS |
| `TestProjectNotesInjection::test_notes_before_mcp_section` | Notes precede the MCP/proof block |
| `TestProjectNotesInjection::test_notes_compose_with_working_dir` | Notes compose with working_dir preamble |

### test_self_audit_script.py — scripts/self_audit_diff.sh replay against task-170 fixture

| Test | What it checks |
|---|---|
| `TestScriptExists::test_script_is_present_and_executable` | The mechanical assist script exists on disk |
| `TestReplayTask170Bad::test_exits_nonzero` | Replaying fe766f45 (known-bad) exits 1 — issues found |
| `TestReplayTask170Bad::test_flags_duplicate_extract_agent` | Flags the duplicate `_extract_agent` definition (headline task-170 defect) |
| `TestReplayTask170Bad::test_flags_commented_out_code` | Flags the commented-out contextStats dead-code blocks |
| `TestReplayTask170Bad::test_machine_scannable_summary_line` | Last line is `SELF_AUDIT: duplicates=N commented_blocks=N ...` |
| `TestReplayTask170Clean::test_exits_zero` | Replaying e0a8e187 (cleanup) exits 0 — no false positives |
| `TestReplayTask170Clean::test_does_not_flag_extract_agent` | `_extract_agent` not flagged after cleanup |
| `TestUsageErrors::test_missing_commit_exits_two` | Bad ref → exit 2 (usage error distinct from 'issues found') |

### test_agent_routing.py — Pure suggester

The pure suggestion logic that ranks viable candidates using measured
history. Independent of Django / HTTP / the orchestrator. Pairs with
`agent_routing.suggest_routing()` consumed by `_route_task` via
`_pick_from_tier_candidates`.

| Test | What it checks |
|---|---|
| `TestSuggestRoutingCheapestClearsThreshold::test_clear_winner_in_cheapest_tier` | Cheap-tier agent with high success_rate wins |
| `TestSuggestRoutingCheapestClearsThreshold::test_lower_median_cost_wins_among_qualifying_candidates` | Tiebreaker = lower median_tokens |
| `TestSuggestRoutingEscalation::test_no_cheapest_tier_qualifier_escalates` | Cross-tier, only expensive clears threshold |
| `TestSuggestRoutingEscalation::test_multi_tier_partial_qualifiers_promote_cheapest` | Cheapest qualifying tier wins |
| `TestSuggestRoutingEscalation::test_no_qualifier_anywhere_returns_static` | StaticFallback when nobody qualifies |
| `TestSuggestRoutingThinHistory::test_all_candidates_below_min_samples_static` | < min_samples → static |
| `TestSuggestRoutingThinHistory::test_empty_history_static` | {} → static |
| `TestSuggestRoutingThinHistory::test_partial_thin_only_eligible_agents_count` | Thin agents excluded from rank, eligible survivors compete |
| `TestSuggestRoutingThresholdDisqualification::test_below_threshold_disqualified` | Below threshold dropped from rank |
| `TestSuggestRoutingThresholdDisqualification::test_threshold_exact_boundary_passes` | success_rate >= threshold is inclusive |
| `TestDecisionShape::test_history_driven_decision_records_inputs` | RoutingDecision records threshold/min_samples/reason |
| `TestDecisionShape::test_static_fallback_records_inputs` | StaticFallback records threshold/min_samples/reason |
| `TestDecisionShape::test_history_driving_inputs_dataclass` | SuggestionInput dataclass round-trip |

### test_route_task_suggester.py — Orchestrator + suggester integration

The orchestrator's tier-distribution phase replaced random.choice with
the history-driven suggester. These tests pin the wiring: suggester
win/lose paths, planner-override semantics, defensive degradation.

| Test | What it checks |
|---|---|
| `TestRouteTaskHistoryDriven::test_clear_winner_in_cheapest_tier_picked` | Suggester beats random.distribute |
| `TestRouteTaskHistoryDriven::test_static_config_keeps_higher_cost_winner_in_cheap_tier` | Lower median_tokens within qualifiers |
| `TestRouteTaskHistoryDriven::test_cheap_tier_clear_winner_picked_consistently` | Deterministic with thick history |
| `TestRouteTaskThinHistoryFallback::test_thin_history_distributes_across_cheap_tier` | Static fallback path still randomizes |
| `TestRouteTaskThinHistoryFallback::test_thin_history_reasoning_marks_static` | Reasoning states the rule that fired |
| `TestRouteTaskSuggestionOverridesSuggester::test_suggested_agent_used_even_if_history_disagrees` | Planner's suggested_agent overrides history |
| `TestRouteTaskFetchesAgentStats::test_fetches_stats_once_per_route_call` | Fetcher is invoked per route call |
| `TestRouteTaskFetchesAgentStats::test_exception_in_fetch_does_not_break_routing` | Default First: backend blip → static fallback |

### test_fetch_agent_stats.py — TaskIt client for /boards/{id}/agent-stats/

| Test | What it checks |
|---|---|
| `test_fetch_agent_stats_parses_rows` | URL + parse happy path |
| `test_fetch_agent_stats_forwards_spec_id_param` | ?spec= forwarded as query param |
| `test_fetch_agent_stats_exception_returns_empty` | Backend error → empty rows |
| `test_fetch_agent_stats_non_dict_payload_returns_empty` | Garbage JSON → empty rows |

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
| `TestClaudeHarnessMcpConfig::test_max_turns_flag_added_when_set` | Step budget becomes --max-turns N |
| `TestClaudeHarnessMcpConfig::test_no_max_turns_flag_when_absent` | No --max-turns unless a budget is set |
| `TestClaudeHarnessMcpConfig::test_no_max_turns_flag_when_zero_or_none` | 0/None treated as unbounded |
| `TestGeminiHarnessMcpConfig::test_adds_mcp_config_flag` | Gemini adds --mcp-config flag |
| `TestGeminiHarnessMcpConfig::test_no_mcp_config_no_flag` | No flag without config |
| `TestGeminiHarnessMcpConfig::test_mcp_config_in_interactive_command` | Interactive mode gets MCP flag |
| `TestCodexHarnessNoMcp::test_no_mcp_flag_even_with_config` | Codex ignores MCP config |
| `TestCodexChromeDevtoolsFlags::test_chrome_devtools_browser_url_flags` | Codex emits `--browserUrl` (host CDP), drops launch flags |
| `TestMicrosandboxBrowserUrl::test_helper_default_when_unset` | `_microsandbox_browser_url` default is host :9222 |
| `TestMicrosandboxBrowserUrl::test_helper_honors_override` | `chrome_devtools.browser_url` override wins |
| `TestMicrosandboxBrowserUrl::test_generated_claude_config_has_browser_url` | Generated claude config uses `--browserUrl`, no launch flags |
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
| `TestBuildReflectionPrompt::test_prompt_contains_efficiency_guidance` | Batching/no-re-read efficiency lever injected into audit prompt |
| `TestBuildReflectionPrompt::test_prompt_includes_task_title_and_description` | Task title and description in prompt |
| `TestBuildReflectionPrompt::test_prompt_includes_execution_output` | Execution output section populated |
| `TestBuildReflectionPrompt::test_prompt_includes_dependent_tasks` | Dependent tasks listed |
| `TestBuildReflectionPrompt::test_prompt_includes_custom_prompt_when_provided` | Custom prompt injected |
| `TestBuildReflectionPrompt::test_prompt_omits_custom_prompt_section_when_empty` | No ADDITIONAL INSTRUCTIONS when empty |
| `TestBuildReflectionPrompt::test_prompt_includes_agent_and_model_info` | Agent and model in context |
| `TestBuildReflectionPrompt::test_prompt_includes_section_headers` | All 5 report section headers present |
| `TestBuildReflectionPrompt::test_prompt_guides_durable_project_notes_capture` | Reviewer checks durable facts were appended to PROJECT_NOTES.md (improvement, not verdict) |
| `TestParseReflectionReport::test_parse_extracts_all_five_sections` | All sections extracted from well-formed output |
| `TestParseReflectionReport::test_parse_extracts_verdict_pass` | PASS verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_needs_work` | NEEDS_WORK verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_fail` | FAIL verdict parsed |
| `TestParseReflectionReport::test_parse_extracts_verdict_summary` | Summary text after verdict enum extracted |
| `TestParseReflectionReport::test_parse_handles_missing_sections_gracefully` | Missing sections → empty strings |
| `TestParseReflectionReport::test_parse_handles_empty_output` | Empty string → all empty fields |
| `TestParseReflectionReport::test_parse_handles_no_headers` | Plain text → empty structured fields |
| `TestStripTrustWarning::test_strips_trust_warning_line` | Claude CLI trust warning removed from output |
| `TestStripTrustWarning::test_preserves_text_without_warning` | No-op when warning absent |
| `TestStripTrustWarning::test_strips_warning_mid_stream` | Warning stripped mid-JSONL stream |
| `TestParseJsonReviewBlock::test_extracts_json_fenced_block` | ```json ... ``` fence extracted and parsed |
| `TestParseJsonReviewBlock::test_extracts_bare_fenced_block` | ``` ... ``` (no lang) fence also works |
| `TestParseJsonReviewBlock::test_extracts_block_with_surrounding_markdown` | Fence found in mixed prose |
| `TestParseJsonReviewBlock::test_returns_none_for_no_fence` | No fence → None (caller falls through) |
| `TestParseJsonReviewBlock::test_returns_none_for_invalid_json` | Malformed JSON → None |
| `TestParseJsonReviewBlock::test_returns_none_for_empty_fence` | Empty fence → None |
| `TestParseJsonReviewBlock::test_picks_last_block_when_multiple` | Last parseable JSON wins (stray example ignored) |
| `TestParseJsonReviewBlock::test_strips_code_fence_inside_json_string` | ``` inside JSON string doesn't break fence parser |
| `TestParseReflectionJsonContract::test_json_only_no_markdown` | JSON-only review populates all fields |
| `TestParseReflectionJsonContract::test_json_with_markdown_rendering_after` | JSON wins over following markdown sections |
| `TestParseReflectionJsonContract::test_json_with_minimal_fields` | Minimal JSON (verdict+summary) parses; missing → "" |
| `TestParseReflectionJsonContract::test_json_with_unrecognized_verdict_value` | JSON verdict "MAYBE" → ERROR |
| `TestParseReflectionJsonContract::test_json_with_non_string_verdict` | JSON verdict=42 → ERROR |
| `TestParseReflectionJsonContract::test_json_takes_precedence_over_markdown_sections` | JSON is source of truth when both present |
| `TestCapturedTask159BadOutputs::test_haiku_trust_warning_plus_jsonl_noise_errors` | Task 159 haiku noise → ERROR (not contentless NEEDS_WORK) |
| `TestCapturedTask159BadOutputs::test_sonnet_trust_warning_only_errors` | Task 159 sonnet trust warning only → ERROR |
| `TestCapturedTask159BadOutputs::test_trust_warning_alone_with_no_keyword_errors` | Init JSONL alone, no verdict → ERROR with raw head |
| `TestBareKeywordLaunderingHardErrors::test_bare_pass_alone_is_honored` | Bare "PASS" → PASS (safe lenient) |
| `TestBareKeywordLaunderingHardErrors::test_bare_needs_work_alone_errors` | Bare "NEEDS_WORK" → ERROR (no fix list = no rework) |
| `TestBareKeywordLaunderingHardErrors::test_bare_fail_alone_errors` | Bare "FAIL" → ERROR (same gate) |
| `TestBareKeywordLaunderingHardErrors::test_bare_keyword_buried_in_unrelated_text_errors` | "rate_limit_failure" word → ERROR |
| `TestBareKeywordLaunderingHardErrors::test_bare_pass_buried_in_unrelated_text_is_honored` | PASS buried in noise still honored |
| `TestBareKeywordLaunderingHardErrors::test_error_includes_raw_head_for_debuggability` | ERROR summary embeds raw head for operator triage |
| `TestPromptRequiresJsonContract::test_prompt_requires_fenced_json_block` | Prompt names `verdict` and `fix_list` keys |
| `TestPromptRequiresJsonContract::test_prompt_warns_against_bare_keyword` | Prompt warns against bare keyword laundering |
| `TestPromptRequiresJsonContract::test_prompt_explains_markdown_rendering_is_optional` | Prompt says markdown rendering is optional |

### test_claude_harness.py — claude CLI invocation contract

| Test | What it checks |
|---|---|
| `TestClaudeRegistration::test_registered_in_registry` | `claude` harness discoverable |
| `TestClaudeRegistration::test_registry_class_is_claude_harness` | Registry class is `ClaudeHarness` |
| `TestClaudeBuildExecuteCommand::test_uses_configured_cli_binary` | First arg is `claude` (or override) |
| `TestClaudeBuildExecuteCommand::test_prompt_passed_via_dash_p` | `-p <prompt>` is in the command |
| `TestClaudeBuildExecuteCommand::test_stream_json_output_format` | `--output-format stream-json` is set |
| `TestClaudeBuildExecuteCommand::test_no_setting_sources_by_default` | Regular task execution does NOT emit `--setting-sources` — preserves project-level `.claude/settings.local.json` safety hooks (task 165 review feedback: prior version disabled safety hooks for every regular task) |
| `TestClaudeBuildExecuteCommand::test_setting_sources_from_context_emitted` | Reviewer sets `context["setting_sources"]="user"` → `--setting-sources user` emitted |
| `TestClaudeBuildExecuteCommand::test_setting_sources_comma_list_preserved` | `context["setting_sources"]="user,project"` → verbatim forwarded |
| `TestClaudeBuildExecuteCommand::test_setting_sources_false_disables_flag` | Explicit `False` opts out |
| `TestClaudeBuildExecuteCommand::test_setting_sources_none_disables_flag` | `None` opts out |
| `TestClaudeBuildExecuteCommand::test_setting_sources_empty_string_disables_flag` | Empty string opts out |
| `TestClaudeBuildExecuteCommand::test_setting_sources_non_string_disables_flag` | Non-string value (list/dict/int) opts out |
| `TestClaudeBuildExecuteCommand::test_setting_sources_already_in_execute_args_idempotent` | Operator-supplied `--setting-sources` in `execute_args` wins; no duplicate flag |
| `TestClaudeBuildExecuteCommand::test_model_appended_when_in_context` | `--model` only when context provides one |
| `TestClaudeBuildExecuteCommand::test_no_model_flag_when_not_in_context` | No `--model` flag without context |
| `TestClaudeBuildExecuteCommand::test_setting_sources_emitted_with_model` | Reviewer path: `--setting-sources` and `--model` coexist |
| `TestClaudeBuildExecuteCommand::test_setting_sources_strip_whitespace` | Leading/trailing whitespace stripped |
| `TestClaudeBuildExecuteCommand::test_setting_sources_whitespace_only_no_flag` | Whitespace-only treated as no flag |
| `TestClaudeBuildInteractiveCommand::test_no_setting_sources_by_default` | Interactive path default: no `--setting-sources` |
| `TestClaudeBuildInteractiveCommand::test_setting_sources_from_context_emitted` | Interactive path honors context flag |
| `TestClaudeBuildInteractiveCommand::test_setting_sources_already_in_execute_args_idempotent` | Interactive path: operator's flag wins |
| `TestClaudeBuildInteractiveCommand::test_model_passes_through` | Interactive path: `--model` from context |
| `TestClaudeBuildInteractiveCommand::test_setting_sources_with_model` | Interactive path: parity with one-shot |

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

### test_warm_start.py — Task-brief → doc suggestions injected into the prompt `[disk]`

| Test | What it checks |
|---|---|
| `TestBriefMatching::test_planning_failed_brief_picks_planning_flow_debug` | A planning-decomposition brief's top match is `planning-flow/.../DEBUG.md` |
| `TestBriefMatching::test_token_count_brief_picks_trace_data_pipeline` | A trace/token brief matches the `trace-data-pipeline` breadcrumb |
| `TestBriefMatching::test_worktree_isolation_brief_picks_worktree_breadcrumb` | A merge-conflict brief matches the `git-worktree-isolation` breadcrumb |
| `TestBriefMatching::test_known_similar_outranks_unrelated` | Parity with twins scorer: topically-close doc outranks unrelated |
| `TestNoMatch::test_nonsense_brief_yields_nothing` | A gibberish brief returns no suggestions |
| `TestNoMatch::test_no_section_rendered_on_no_match` | `format_warm_start_section([])` is the empty string |
| `TestNoMatch::test_missing_docs_root_returns_empty` | Missing docs dir → `[]` |
| `TestNoMatch::test_none_docs_root_returns_empty` | `None` docs root → `[]` |
| `TestOutputShape::test_caps_at_three` | Never more than `MAX_SUGGESTIONS` |
| `TestOutputShape::test_paths_are_repo_relative` | All emitted paths start with `docs/` and carry a reason |
| `TestOutputShape::test_results_sorted_descending` | Suggestions sorted by score, descending |
| `TestOutputShape::test_format_section_renders_bullets` | Section header + every path rendered as bullets |
| `TestMetadataRecording::test_records_suggestions_onto_task_metadata` | `_record_warm_start_docs` writes `metadata["warm_start_docs"]` |
| `TestMetadataRecording::test_skipped_under_mock` | No metadata write under `mock=True` |
| `TestMetadataRecording::test_empty_suggestions_writes_nothing` | Empty suggestion list writes nothing |
| `TestMetadataRecording::test_end_to_end_suggest_then_record` | suggest → record round-trip on a real-shaped brief |

### test_warm_start_corpus.py — Broadened corpus + configurable floor (task #242) `[disk]`

| Test | What it checks |
|---|---|
| `TestBreadcrumbFlowHeadings::test_flow_file_heading_enriches_match` | A brief phrased after a FLOW.md summary matches that FLOW.md (file's own headings indexed) |
| `TestBreadcrumbFlowHeadings::test_unindexed_details_file_is_discovered` | A DETAILS.md referenced nowhere in _INDEX is found by the file walk |
| `TestBreadcrumbFlowHeadings::test_breadcrumb_paths_repo_relative` | Breadcrumb paths start with `docs/breadcrumb_analysis/` |
| `TestWikiToc::test_wiki_entry_matched_on_toc_summary` | A wiki TOC one-liner matches on its title/summary/tags |
| `TestWikiToc::test_wiki_paths_repo_relative` | Wiki paths start with `docs/wiki/` and carry a reason |
| `TestPatternsH2::test_pattern_h2_heading_text_indexed` | A brief matching an H2-only term reaches the pattern |
| `TestFloorConfig::test_min_score_override_respected` | Lower floor surfaces ≥ matches than a 0.99 floor; default omitted |
| `TestFloorConfig::test_default_floor_uses_module_constant` | Omitting min_score falls back to MIN_SCORE (backward compatible) |
| `TestFloorConfig::test_all_scores_unfiltered` | `score_all` returns every entry sorted desc, below the default floor |

### test_warm_start_replay.py — Offline replay harness for floor tuning (task #242) `[disk]`

| Test | What it checks |
|---|---|
| `TestCoverageCurve::test_curve_is_monotonic_nonincreasing` | Coverage never rises as the floor goes up |
| `TestCoverageCurve::test_zero_floor_matches_everything_with_overlap` | Floor 0 matches briefs sharing tokens; gibberish still matches nothing |
| `TestCoverageCurve::test_high_floor_matches_subset_of_low` | A 0.5 floor matches ≤ a 0.0 floor |
| `TestCoverageCurve::test_results_carry_top_match` | Each result has its top suggestion; no-overlap briefs have `top=None` |
| `TestCoverageCurve::test_relevance_sample_picks_k` | `relevance_sample` returns up to k matched briefs with reasons |
| `TestCoverageCurve::test_empty_briefs_curve` | Empty briefs → zero coverage, no crash |


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

### test_worktree_disk.py — WorktreeManager merge lifecycle (real git)

| Test | What it checks |
|---|---|
| `TestProvenanceTrailers::test_auto_commit_message_has_trailers` | Auto-commit carries Task-Id/Spec-Id trailers |
| `TestProvenanceTrailers::test_merge_commit_message_has_trailers` | Merge commit carries Task-Id/Spec-Id trailers |
| `TestProvenanceTrailers::test_trailers_extractable_by_key` | `git log --format=%(trailers:key=…)` returns the value |
| `TestProvenanceTrailers::test_trailers_present_without_title` | Merge with no title still gets trailers |

### test_why.py — `testing_tools/why.py` provenance walker

| Test | What it checks |
|---|---|
| `TestWhySingleLine::test_line_with_trailers` | `file:LINE` resolves blame → Task-Id/Spec-Id |
| `TestWhySingleLine::test_line_without_trailers` | Trailerless commit degrades gracefully (—) |
| `TestWhyRange::test_range_resolves_each_line` | `file:START-END` resolves the span |
| `TestWhyRange::test_comma_lines` | `file:N,M` resolves each listed line |
| `TestWhyFileSummary::test_whole_file_summary` | Bare `file` shows distinct commit provenances |
| `TestWhyErrors::test_nonexistent_file` | Missing file exits non-zero |
| `TestWhyErrors::test_nonexistent_line` | Bad line exits non-zero, no traceback |
| `TestWhyErrors::test_no_arg_prints_usage` | No arg prints usage hint |

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

### test_harness_subprocess_errors.py — Harness subprocess error paths (all 6 harnesses)

| Test | What it checks |
|---|---|
| `TestHarnessTimeoutKillsSubprocess::test_timeout_kills_subprocess_and_returns_failed` | Timeout context kills subprocess via terminate_subprocess() → TaskResult.success=False, error contains "timed out" (x6 harnesses) |
| `TestHarnessTimeoutKillsSubprocess::test_timeout_kills_subprocess_when_communicate_times_out` | asyncio.TimeoutError from wait_for → subprocess killed, TaskResult.success=False (x6 harnesses) |
| `TestHarnessNonZeroExit::test_non_zero_exit_returns_failed_with_stderr` | returncode=1 → TaskResult.success=False, error=stderr (x6 harnesses) |
| `TestHarnessNonZeroExit::test_stderr_only_output_returns_failed` | returncode=2 with stderr-only → TaskResult.success=False (x6 harnesses) |
| `TestHarnessNonZeroExit::test_non_zero_exit_with_partial_stdout_returns_failed` | returncode=137 (OOM kill) with partial stdout → success=False (x6 harnesses) |
| `TestApiHarnessHttpErrors::test_http_500_server_error_returns_failed` | HTTP 500 from CLI → success=False (minimax, glm) |
| `TestApiHarnessHttpErrors::test_http_401_auth_error_returns_failed` | HTTP 401 → success=False (minimax, glm) |
| `TestApiHarnessHttpErrors::test_http_429_rate_limit_returns_failed` | HTTP 429 → success=False (minimax, glm) |
| `TestApiHarnessHttpErrors::test_network_connection_error_returns_failed` | Connection refused → success=False (minimax, glm) |
| `TestApiHarnessHttpErrors::test_http_error_includes_partial_stdout_for_debugging` | Partial stdout preserved alongside error (minimax, glm) |

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
| `DepsSatisfiedTests::test_review_counts_as_satisfied` | REVIEW counts as satisfied (removed in fable task 214 — REVIEW does NOT satisfy; TESTING does) |
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
| `TestReflectTask::test_passes_setting_sources_user` | Reviewer passes `setting_sources="user"` so the claude harness emits `--setting-sources user` (opt-in, scoped to reviewer only — preserves safety hooks for regular task execution, task 165 review feedback) |
| `TestReflectTask::test_submits_parsed_report` | PATCHes report with COMPLETED + parsed sections |
| `TestReflectTask::test_round_trips_json_review` | JSON contract review (fenced ```json block```) round-trips through parser into the completed-report PATCH |
| `TestReflectTask::test_captures_captured_bad_output_as_error` | Task 159 captured haiku noise (trust warning + JSONL thinking_tokens + bare "NEEDS_WORK" keyword) → ERROR, not laundered into a contentless NEEDS_WORK verdict |
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

Requires `gemini`, `codex` CLIs on PATH.

```bash
python -m pytest tests/integration/ -v
```

| Test | What it checks |
|---|---|
| `TestHarnessAvailability::test_harness_is_available` | gemini/codex CLIs on PATH (x2) |
| `TestHarnessAvailability::test_all_expected_harnesses_registered` | All 5 harness names in registry |
| `TestSingleHarnessExecute::test_gemini_returns_output` | Real gemini call returns output |
| `TestDecomposition::test_decompose_returns_valid_subtasks` | Codex decomposes spec into subtasks |
| `TestFullPoemE2E::test_poem_html_generated` | Full pipeline produces poem.html |
| `TestPlanOnly::test_plan_creates_tasks_without_executing` | plan() creates tasks, no execution |
| `TestExecSingleTask::test_exec_single_task_by_id` | exec_task() completes one task |
| `TestAssembleSeparately::test_staged_plan_exec` | Staged plan->exec_task transitions status |
| `TestReassign::test_reassign_changes_agent` | assign_task() changes agent |
| `TestDiskWriteCapability::test_codex_can_write_file` | Codex creates file on disk |
| `TestDiskWriteCapability::test_gemini_can_write_file` | Gemini creates file on disk |

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

### test_microsandbox_harness.py (mock) — MicrosandboxHarness contract

Unit tests for the microsandbox sandbox decorator harness (no real VM). Covers the
config model, registry dispatch (incl. `run_in_forkd` back-compat), `msb run` command
construction, and `TaskResult` mapping. 16 tests.

| Test | What it checks |
|---|---|
| `test_registry_wraps_when_sandbox_mode_microsandbox` | `sandbox_mode=microsandbox` → `MicrosandboxHarness`; `build_execute_command` is None |
| `test_run_in_forkd_backcompat_still_wraps_forkd` | legacy `run_in_forkd: true` still → `ForkdHarness` |
| `test_build_msb_command_structure` | `msb run` argv: worktree mount, workdir, timeout, memory, inner cmd after `--` |
| `test_build_msb_command_applies_net_policy` | `--net-default` / `--net-rule` egress allowlist wired through |
| `test_execute_success_writes_trace_and_metadata` | `execute()` → `TaskResult` (sandbox=microsandbox) + trace/out files |
| `test_execute_missing_status_fails` | missing ODIN-STATUS block → `success=False` |
| `test_execute_sync_invokes_msb_with_worktree_mount` | `_execute_sync` builds+runs `msb` with the worktree mounted |
| `test_is_available_*` | availability = `msb` present AND inner yields a command |
| `test_supports_system_prompt_flag_delegates_to_inner` | wrapper delegates the flag to inner (planning read it and crashed with AttributeError before) |

### test_microsandbox_cleanup.py (mock) — run-end sandbox lifecycle

Pins the leak fix: every `msb run` carries a deterministic `--name odin-msb-*`
so the run's finally block can `msb sandbox remove` it — across success,
non-zero exit, host timeout, and exception paths. Plus the safety nets: named
sandboxes (`odinbuild`) and snapshots (`odin-agents`) never appear in any
removal set; the tempdir used for MCP staging is also always cleaned. Pure
mock — no `msb` required. 17 tests.

| Test | What it checks |
|---|---|
| `test_run_command_includes_named_sandbox_flag` | every `msb run` carries `--name odin-msb-…` |
| `test_success_path_removes_sandbox` | success path issues `msb sandbox remove <name>` |
| `test_success_path_remove_runs_after_run` | cleanup call ordered AFTER the run call |
| `test_nonzero_exit_path_removes_sandbox` | non-zero msb exit still removes the sandbox |
| `test_timeout_path_removes_sandbox` | host TimeoutExpired still removes the sandbox |
| `test_exception_path_removes_sandbox` | unexpected exception still removes the sandbox |
| `test_temp_dir_removed_alongside_sandbox` | the `odin-msb-*` tempdir is rmtree'd too |
| `test_named_sandboxes_never_in_removal_set` | refuses anything not starting with `odin-msb-` |
| `test_snapshots_dir_never_touched` | never addresses `~/.microsandbox/snapshots/` |
| `test_cleanup_runs_in_finally_not_only_on_success` | finally-style guarantee, parameterized over 4 control-flow cases |
| `TestEphemeralSandboxNameFilter::*` | the prefix filter rejects odinbuild/odin-agents/etc. |
| `TestOrphanListingParser::*` | parsing both JSON and plain `msb list` keeps ephemeral-only |

### test_microsandbox_gc.py (mock) — `odin gc` + startup sweep

`odin gc` reports sandboxes / snapshots / worktrees (with `node_modules`
breakdown), refuses to prune named sandboxes or snapshots, and the
`MicrosandboxHarness.sweep_startup_orphans()` backstop also only removes
`odin-msb-*` orphans. Pure mock. 16 tests.

| Test | What it checks |
|---|---|
| `TestPartitionOrphans::*` | the single safety net for any remove list |
| `TestStartupOrphanSweep::*` | backstop removes only ephemeral; dry-run never issues `msb remove` |
| `TestGcReportShape::*` | the report's four keys + totals consistency |
| `TestGcPrune::*` | `--prune` removes only ephemeral sandboxes, never touches snapshots |
| `TestGcCliContract::*` | `OdinCLI.gc` exists, default is dry-run |
| `TestSizeBreakdownEdgeCases::*` | missing paths return 0; node_modules kept separate from rest |


### test_clarification_gate.py (mock) — Pre-planning clarification gate

The clarification gate runs before task breakdown: the planner surfaces
questions, a summary, and an HTML preview, then waits for a human nod.
Tests cover prompt construction, orchestrator dispatch order, answer
injection, preview-path surfacing, and the mandatory confirmation. 19 tests.

| Test | What it checks |
|---|---|
| `TestClarificationPrompt::*` | Prompt includes spec text, output paths, questions/summary keywords, quick-mode instruction |
| `TestGateAutoMode::test_gate_runs_before_decomposition` | Order is clarification → gate_callback → decomposition |
| `TestGateAutoMode::test_gate_disabled_skips_clarification` | `gate=False` → no clarification file, only decomposition runs |
| `TestGateAutoMode::test_answers_appended_to_decomposition_prompt` | Callback answers appear in the decomposition prompt |
| `TestGateAutoMode::test_clarification_files_written` | Both JSON + HTML files exist with correct content |
| `TestGateAutoMode::test_gate_callback_none_proceeds_without_answers` | No callback → files written, planning proceeds |
| `TestGateAutoMode::test_gate_callback_returns_none_aborts` | Callback `None` → `RuntimeError`, no plan file |
| `TestGateAutoMode::test_orchestrator_injects_preview_path` | Clarification dict includes `preview_path` + `preview_exists` |
| `TestInteractivePromptGate::*` | Interactive prompt includes/excludes gate section based on `gate` flag |
| `TestGateInteraction::test_surfaces_preview_path` | CLI gate prints the preview file path to the human |
| `TestGateInteraction::test_requires_nod_even_with_no_questions` | No questions + "n" → aborts (no auto-proceed) |
| `TestGateInteraction::test_proceeds_on_yes_with_no_questions` | No questions + "y" → proceeds with empty answers |
| `TestGateInteraction::test_requires_nod_after_answering_questions` | Questions answered + "n" → still aborts |
| `TestGateInteraction::test_proceeds_with_answers_on_yes` | Questions answered + "y" → returns answers |
| `TestGateInteraction::test_eof_on_confirm_aborts` | EOFError on confirmation → aborts (non-interactive safe) |
| `TestGateInteraction::test_no_preview_path_does_not_crash` | Missing preview_path key handled gracefully |



### test_microsandbox_real.py (integration) — real microVM end-to-end

Requires `msb` (microsandbox) installed. Boots a real libkrun microVM.
Run: `python -m pytest tests/integration/test_microsandbox_real.py -o addopts="" -v`

| Test | What it checks |
|---|---|
| `test_microsandbox_executes_inner_command_in_real_vm` | full `execute()` path in a live VM → `TaskResult` success, ODIN-STATUS validated |
| `test_microsandbox_workspace_edits_persist_to_host` | guest writes to `/workspace` persist back to the host worktree |
