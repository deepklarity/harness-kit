# Harness Isolation Testing — Detailed Trace

## 1. BaseHarness Contract

**File**: `odin/src/odin/harnesses/base.py`
**Purpose**: Abstract base defining what every harness must implement

Required overrides:
- `execute(prompt, context) → TaskResult` — run prompt, return structured result
- `is_available() → bool` — check if CLI/API is reachable

Optional overrides:
- `execute_streaming(prompt, context) → AsyncIterator[str]` — yield chunks
- `execute_conversation_turn(messages, context)` — multi-turn (API harnesses)
- `build_execute_command(prompt, context) → list[str] | None` — for CLI subprocess
- `build_interactive_command(system_prompt_file, context) → list[str] | None` — for tmux mode

Key utilities (static, tested directly):
- `extract_text_from_line(line)` — parse one JSON line from stream-json
- `extract_text_from_stream(raw)` — parse full stream-json output
- `read_with_tee(proc, output_file)` — subprocess → file + return text
- `read_with_trace(proc, output_file, trace_file)` — subprocess → trace file (raw JSON) + output file (extracted text)

Data in: `prompt: str`, `context: dict`
Data out: `TaskResult(success, output, error, duration_ms, agent, metadata)`

---

## 2. Harness Registry

**File**: `odin/src/odin/harnesses/registry.py`
**Purpose**: Decorator-based registration, factory instantiation

Key logic:
- `@register_harness("claude")` on class definition adds to `HARNESS_REGISTRY`
- `get_harness("claude", config)` → instantiates `ClaudeHarness(config)`
- `_import_all_harnesses()` bootstraps imports: claude, codex, gemini, glm, minimax, mock, qwen
- Bootstrap runs on first `get_harness()` call (not at import time)

Registered harnesses: claude, codex, gemini, glm, minimax, mock, qwen (7 total)

---

## 3. MCP Config Generation (Orchestrator)

**File**: `odin/src/odin/orchestrator.py :: _generate_mcp_config()`
**Called by**: `_execute_task()` during task execution setup
**Calls**: `taskit_mcp/config.py :: server_entry()`, `mobile_mcp/config.py :: server_fragment()`

Key logic:
- `mcps = self.config.mcps` determines which servers to include
- `has_taskit = "taskit" in mcps and self.config.taskit` — needs both opt-in AND backend configured
- `has_mobile = "mobile" in mcps` — no backend dependency (external npm)
- Returns early if neither taskit nor mobile configured
- Codex handled separately (TOML format, `-c` flags)
- OpenCode agents handled separately (`mcp`/`permission` keys instead of `mcpServers`)
- All others: merge server dicts into `{"mcpServers": {...}}`

Error paths:
- Unknown agent → falls back to Claude-style JSON in log_dir
- No taskit config but mobile configured → mobile-only config (no `_get_mcp_env` call)
- Auth token fetch failure → empty string token (graceful degradation)

Data in: `task_id, agent_name, log_dir, working_dir, model`
Data out: `Optional[str]` — config file path (Claude) or None (auto-discovery agents)

---

## 4. MCP Config Modules

### 4a. TaskIt MCP Config

**File**: `odin/src/odin/mcps/taskit_mcp/config.py`

Tool name source: FastMCP server introspection (`mcp.list_tools()`)
- `tool_names()` — cached, introspects at first call, sorted
- `claude_tool_names()` — prefixed as `mcp__taskit__<name>`
- `server_entry(agent_name, env)` — dict dispatcher, returns `{"taskit": {...}}`

Per-agent dict differences:
- Claude: `{"command": "taskit-mcp", "env": env}`
- Gemini/Qwen: same + `"trust": True`
- KiloCode: same + `"alwaysAllow": [tool_names]`
- OpenCode: `{"type": "local", "command": ["taskit-mcp"], "environment": env}`
- Codex: same as Claude (dict only, TOML formatting done by orchestrator/formatter)

`format_claude_settings(mcps=None)` — generates `.claude/settings.local.json`:
- Always includes taskit tools in permissions.allow
- If `mcps` contains "mobile": extends with mobile tool names, adds "mobile" to enabledMcpjsonServers

### 4b. Mobile MCP Config

**File**: `odin/src/odin/mcps/mobile_mcp/config.py`

Tool name source: hardcoded list (external npm package, not introspectable)
- `MOBILE_TOOL_NAMES` — 19 tools, sorted, from `@mobilenext/mobile-mcp` v0.1.x
- `mobile_tool_names()` — returns copy of list
- `claude_mobile_tool_names()` — prefixed as `mcp__mobile__<name>`
- `server_fragment(agent_name)` — dict dispatcher

Per-agent fragment differences:
- Claude: `{"mobile": {"command": "npx", "args": [...]}}`
- Gemini/Qwen: same + `"trust": True`
- Codex: returns `-c` flag list (not a dict)
- KiloCode: same + `"alwaysAllow": [19 tools]`
- OpenCode: `{"mobile": {"type": "local", "command": ["npx", ...]}}`

No env vars — mobile-mcp doesn't need auth, task IDs, or backend URLs.

---

## 5. Claude Harness (Reference Implementation)

**File**: `odin/src/odin/harnesses/claude.py`
**Purpose**: Most feature-complete harness — MCP flags, token extraction, trace files

`build_execute_command()`:
- Adds `--output-format stream-json --verbose`
- Adds `--mcp-config <path>` if `context["mcp_config"]`
- Adds `--allowedTools <comma-list>` if `context["mcp_allowed_tools"]`
- Adds `--model <model>` if `context["model"]`
- Adds extra args from `config.execute_args` (default: `-p`)

`_extract_token_usage(raw_output)`:
- Parses `modelUsage` from Claude CLI (final JSON line with aggregate stats)
- Falls back to `step_finish` events (opencode/kilo format)
- Returns `{"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"}`
- Returns empty dict if no usage data found

`execute()` three code paths:
1. `trace_file + output_file` → `read_with_trace()` → tokens from trace file
2. `output_file` only → `read_with_tee()` → tokens from raw output
3. No files → `communicate()` → tokens from stdout

---

## 6. Codex Harness (MCP Flag Injection)

**File**: `odin/src/odin/harnesses/codex.py`

`build_execute_command()`:
- Adds `--skip-git-repo-check --json`
- Injects taskit MCP via `-c` flags from `context["mcp_env"]` dict
- Injects mobile MCP via `-c` flags when `context["mobile_mcp_enabled"]`
- No `--output-format` flag (Codex uses plain text)

Key difference: Codex has DUAL injection — both config file (`.codex/config.toml` from orchestrator) AND `-c` flags (from harness). The `-c` flags override the config file. This is because `-c` bypasses project trust checks.

---

## 7. Auto-Discovery Harnesses (Gemini, Qwen, MiniMax, GLM)

**Files**: `gemini.py`, `qwen.py`, `minimax.py`, `glm.py`

These harnesses do NOT inject MCP via CLI flags. They rely on:
1. Orchestrator writes config file to working_dir
2. CLI discovers config at standard path on startup

The harnesses' `build_execute_command()` has no MCP-related logic. MCP visibility is entirely dependent on the config file being in the right place with the right format.

---

## 8. Token/Cost Extraction Pipeline

**Files**: `claude.py :: _extract_token_usage()`, `cost_tracking.py :: CostTracker`

Pipeline:
1. Harness runs subprocess → raw stream-json output
2. Claude harness parses `modelUsage` or `step_finish` for token counts
3. Token dict stored in `TaskResult.metadata["usage"]`
4. `CostTracker.record_task()` reads metadata, creates `TaskCostRecord`
5. Record written to `costs_sp_<spec_id>.json`

Two token formats supported by CostTracker:
- Claude style: `{"input_tokens": N, "output_tokens": M, "total_tokens": T}`
- OpenAI style: `{"prompt_tokens": N, "completion_tokens": M}`

Cost estimation: `(tokens / 1M) * price_per_million` using `agent_models.json` pricing table.

Only Claude harness currently extracts tokens. Gemini, Qwen, Codex, etc. return empty metadata. Token extraction for non-Claude harnesses is a coverage gap.

---

## 9. Streaming Verification

**File**: `odin/tests/mock/test_streaming.py`

Tests use mocked subprocesses that emit lines with controlled timing.

Key assertions:
- Chunks arrive incrementally (time span > 0.05s between first and last)
- Chunk order preserved (sequential numbering)
- Callback fires per chunk (not batched)
- CLI-not-found yields error message (not exception)
- Empty output handled gracefully

Parametrized across: ClaudeHarness, GeminiHarness, CodexHarness, QwenHarness.

---

## 10. Mock Harness

**File**: `odin/src/odin/harnesses/mock.py`

Returns canned `TaskResult` without subprocess:
- `success: True` always
- `duration_ms`: random 500-3000
- `metadata["usage"]`: random `{input_tokens, output_tokens, total_tokens}`
- Output includes ODIN-STATUS/ODIN-SUMMARY envelope

Used by: `odin exec --mock`, mock tests, pipeline tests that don't need real agents.
`build_execute_command()` returns None (no CLI).
`is_available()` always True.

---

## 11. Live Integration Tests

**File**: `odin/tests/integration/test_real.py`

Prerequisites: gemini, qwen, codex CLIs on PATH.

Tests:
- `TestHarnessAvailability` — CLIs findable, all 6 names in registry
- `TestSingleHarnessExecute` — real gemini/qwen calls return output + timing
- `TestDecomposition` — codex decomposes spec into valid JSON subtasks
- `TestFullPoemE2E` — full pipeline: plan → exec → assemble → HTML output
- `TestDiskWriteCapability` — each agent writes a file to disk (sandbox verification)

Coverage gap: No live MCP visibility test (does the agent actually see and call MCP tools?). The `test_mobile_mcp_live.py` stubs exist but are skip-marked.

---

## 12. E2E Snapshot Tests

**Status**: Infrastructure described in CLAUDE.md but directory `tests/e2e_snapshots/` does not exist yet.

Intended workflow:
1. Run spec, capture output via `testing_tools/snapshot_extractor.py`
2. Store as JSON golden files
3. Validate structural invariants (not exact values) in regression tests
4. Re-capture after model/serializer/harness changes

Static trace data from real runs could be used for:
- Token extraction verification (known stream-json → known usage dict)
- Cost estimation accuracy (known tokens + known model → expected cost)
- Text extraction correctness (known stream-json → expected clean text)
