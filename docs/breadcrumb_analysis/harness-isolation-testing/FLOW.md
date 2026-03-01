# Harness Isolation Testing

Trigger: `python -m pytest tests/unit/ tests/mock/ tests/integration/` (selective)
End state: Each harness builds correct CLI commands, sees MCP tools, extracts tokens, streams output

## Test Layers

Three concerns tested in isolation, all rooted in harness behavior:

1. **MCP visibility** — config generation, tool approval, flag injection
2. **Execution & streaming** — CLI command construction, subprocess I/O, text extraction
3. **Token/cost extraction** — parsing harness trace output for usage metrics

## Flow: MCP Visibility (unit tests)

```
OdinConfig.mcps
  → ["taskit"] or ["taskit", "mobile"]

orchestrator.py :: _generate_mcp_config()
  → reads self.config.mcps
  → calls taskit_mcp/config.py :: server_entry(agent_name, env)
  → calls mobile_mcp/config.py :: server_fragment(agent_name)  [if mobile]
  → merges dicts into per-CLI format

  [claude]
  → JSON {"mcpServers": {taskit: {...}, mobile: {...}}}
  → writes to log_dir/mcp_<task_id>.json
  → returns path (for --mcp-config flag)

  [gemini, qwen]
  → JSON {"mcpServers": {...}} with trust: true
  → writes to working_dir/.gemini/settings.json or .qwen/settings.json
  → returns None (auto-discovery)

  [codex]
  → TOML [mcp_servers.taskit] + [mcp_servers.mobile]
  → writes to working_dir/.codex/config.toml
  → returns None (also -c flag injection via context["mcp_env"])

  [minimax, glm]
  → JSON {"permission": {...}, "mcp": {...}}
  → writes to working_dir/opencode.json
  → returns None (auto-discovery)

orchestrator.py :: _execute_task()
  → context["mcp_config"] = path          (claude only)
  → context["mcp_env"] = env_dict         (codex -c flags)
  → context["mcp_allowed_tools"] = [...]  (claude --allowedTools)
  → context["mobile_mcp_enabled"] = True  (codex mobile -c flags)
```

## Flow: Harness Command Construction (unit + mock tests)

```
harness :: build_execute_command(prompt, context)

  [claude]
  → [claude, -p, <prompt>, --output-format, stream-json, --verbose,
     --mcp-config, <path>, --allowedTools, mcp__taskit__...,mcp__mobile__...]

  [gemini]
  → [gemini, -p, <prompt>, --output-format, stream-json, --yolo]
  → (no MCP flags — auto-discovery from .gemini/settings.json)

  [qwen]
  → [qwen, -p, <prompt>, --output-format, stream-json, --yolo]
  → (no MCP flags — auto-discovery from .qwen/settings.json)

  [codex]
  → [codex, exec, --skip-git-repo-check, --json, --full-auto,
     -c, mcp_servers.taskit.command="taskit-mcp",
     -c, mcp_servers.taskit.env.TASKIT_URL="...", ...,
     -c, mcp_servers.mobile.command="npx",       (if mobile)
     -c, mcp_servers.mobile.args=[...],          (if mobile)
     <prompt>]

  [minimax (kilo), glm (opencode)]
  → [kilo/opencode, -p, <prompt>, --format, json, --auto]
  → (no MCP flags — auto-discovery from opencode.json)
```

## Flow: Token Extraction (mock tests)

```
harness :: execute(prompt, context) → TaskResult

  subprocess stdout → raw stream-json lines

  base.py :: extract_text_from_line(line)
    [claude content_block_delta] → delta.text
    [claude result]              → result string
    [gemini text]                → text field
    [opencode step_finish]       → content field
    [codex item.completed]       → item.text
    [plain text]                 → passthrough

  base.py :: extract_text_from_stream(raw) → clean text

  claude.py :: _extract_token_usage(raw)
    → parses modelUsage (Claude CLI) or step_finish events
    → returns {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens}

  TaskResult.metadata["usage"] = token_dict

  cost_tracking.py :: CostTracker.record_task()
    → reads metadata["usage"]
    → creates TaskCostRecord
    → stores to costs_sp_<spec_id>.json
```

## Flow: Streaming Verification (mock tests)

```
harness :: execute_streaming(prompt, context) → AsyncIterator[str]

  subprocess stdout → line-by-line yield
    → chunks arrive incrementally (not all at once)
    → chunk order preserved
    → callback fires per chunk with timing data

  base.py :: read_with_trace(proc, output_file, trace_file)
    → trace_file: raw JSON lines (for post-mortem)
    → output_file: extracted text (for odin tail)
    → both flushed incrementally
```

## Flow: Init-Time Config Generation (odin init)

```
cli.py :: init()
  → cfg = self._get_config()
  → _generate_all_mcp_configs(cwd, taskit_env, mcps=cfg.mcps)
    → iterates MCP_CONFIG_MAP (6 agents)
    → if mobile in mcps: _merge_mcp_config() adds mobile entries
    → writes per-CLI config files to working dir
  → _generate_claude_settings(cwd, mcps=cfg.mcps)
    → format_claude_settings(mcps=mcps)
    → includes mobile tool names if mobile in mcps
    → merges into .claude/settings.local.json
```

## Test File Map

| Concern | Test File | Type | Count |
|---------|-----------|------|-------|
| MCP config generation | `unit/test_mcp_harness_integration.py` | unit | 69 |
| MCP tool names (taskit) | `unit/test_taskit_mcp.py` | unit | 17 |
| MCP tool names (mobile) | `unit/test_mobile_mcp_config.py` | unit | 17 |
| Config parsing (mcps) | `unit/test_config.py` | unit | 6 |
| Mock harness metrics | `mock/test_mock_harness.py` | mock | 3 |
| Streaming behavior | `mock/test_streaming.py` | mock | ~20 |
| Trace/text extraction | `mock/test_trace_logging.py` | mock | ~15 |
| Token extraction | `unit/test_cost_estimator.py` | unit | 4 |
| Cost record persistence | `disk/test_cost_tracking.py` | disk | ~15 |
| Live harness execution | `integration/test_real.py` | integration | ~13 |
| Live mobile MCP | `integration/test_mobile_mcp_live.py` | integration | 3 |
