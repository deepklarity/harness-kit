# Trace Data Pipeline -- Detailed Trace

## 1. Trace File Setup

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_execute_task()` (~line 2208)
**Called by**: DAG executor or direct spec execution
**Calls**: harness.execute()

Key logic:
- Creates two file paths per task:
  - `output_file = .odin/logs/task_{task_id}.out`
  - `trace_file = .odin/logs/task_{task_id}.trace.jsonl`
- Both paths passed via `context` dict to harness
- If task has `mcp_config`, also passes `mcp_config`, `mcp_allowed_tools`, `mcp_env`

Data in: task_id, agent_name, prompt, working_dir
Data out: context dict with `output_file`, `trace_file`, `timeout_seconds`, `model`, etc.

---

## 2. Subprocess Execution and Stream Capture

**File**: `odin/src/odin/harnesses/base.py`
**Function**: `read_with_trace(proc, output_file, trace_file)` (line 115)
**Called by**: Each harness's `execute()` method
**Calls**: `extract_text_from_line()` per line

Key logic:
- Reads `proc.stdout` line by line (async-compatible)
- Each line written raw to `trace_file` (preserves exact protocol JSON)
- Each line passed through `extract_text_from_line()` for text extraction
- Extracted text written to `output_file`
- Both files flushed after each write (enables real-time `tail -f`)
- Returns accumulated plain text string

Data in: subprocess with stream-json stdout
Data out: two files on disk + plain text string

---

## 3. Per-Line Text Extraction

**File**: `odin/src/odin/harnesses/base.py`
**Function**: `extract_text_from_line(line)` (line 147)
**Called by**: `read_with_trace()`
**Calls**: nothing

Key logic -- format dispatch (checked in this order):
1. Non-JSON lines: returned as-is
2. `{"type": "content_block_delta", "delta": {"text": ...}}` -- Claude streaming delta
3. `{"type": "result", "result": ...}` -- Claude final result
4. `{"type": "text", "text": ...}` -- Gemini/GLM direct text
5. `{"type": "message", "content": ...}` -- Gemini message wrapper
6. `{"type": "assistant", "message": {"content": [{"type": "text", "text": ...}]}}` -- Qwen nested content array
7. `{"type": "text", "content": ...}` -- opencode/kilo text event
8. `{"type": "step_finish", "content": ...}` -- opencode/kilo finish event
9. `{"type": "item.completed", "item": {"text": ...}}` -- Codex agent message

- Returns empty string for lines that don't match (protocol framing, metadata, etc.)
- This is the polymorphism layer -- all harness JSON dialects normalize here

Data in: single line from stdout (string)
Data out: extracted text or empty string

---

## 4. Token Usage Extraction (Claude Only)

**File**: `odin/src/odin/harnesses/claude.py`
**Function**: `_extract_token_usage(raw_output)` (line 16)
**Called by**: `claude.execute()` after `read_with_trace()` completes
**Calls**: nothing

Key logic:
- Reads the trace file content (raw JSONL)
- **Preferred path**: Finds `modelUsage` event (final line aggregate)
  ```json
  {"modelUsage": {"claude-sonnet-4-5": {"inputTokens": N, "outputTokens": M, "cacheReadInputTokens": X, "cacheCreationInputTokens": Y}}}
  ```
  Sums across all model entries in `modelUsage`
- **Fallback path**: Sums `step_finish` events
  ```json
  {"type": "step_finish", "part": {"tokens": {"input": N, "output": M, "cache": {"read": X, "write": Y}}}}
  ```
- Returns dict: `{input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens}`
- Returns `{}` if no token data found

**Why only Claude?** Other harness CLIs don't emit token usage in their stream-json output. Gemini, Qwen, MiniMax, GLM, Codex -- none provide token counts in stdout.

Data in: raw JSONL string from trace file
Data out: usage dict or empty dict

---

## 5. Post-Execution Trace Collection

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_execute_task()` (~line 2367)
**Called by**: (continuation of step 1)
**Calls**: `task_mgr.record_execution_result()`, `task_mgr.add_comment()`

Key logic:
- Reads trace file content for backend submission (fallback chain):
  1. `trace_file` exists -> read it
  2. `output_file` exists -> read it
  3. `result.output` -> use in-memory output
- Applies `_truncate_trace()` if content exceeds limit:
  - Keeps first N chars + last 2000 chars (TRACE_TAIL_PRESERVE)
  - Preserves tail because `modelUsage` / `step_finish` events are at the end
- Stores `trace_file` path in `task.metadata["trace_file"]` for later reference

Two backend submissions:
1. `record_execution_result()` -- structured payload with raw_output, duration, metadata
2. `add_comment()` -- raw JSONL as comment content, tagged `trace:execution_jsonl`

Constants:
- `PAYLOAD_EFFECTIVE_INPUT_LIMIT = 5000`
- `PAYLOAD_ERROR_MESSAGE_LIMIT = 2000`
- `TRACE_TAIL_PRESERVE = 2000`

Data in: TaskResult from harness, trace/output files on disk
Data out: POST to backend API (execution_result endpoint + comment)

---

## 6. Backend Execution Result Processing

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `execution_result()` (~line 1214)
**Called by**: POST `/tasks/:id/execution_result/`
**Calls**: `extract_agent_text()`, `parse_envelope()`, `compose_comment()`

Key logic:
- **Stale result guard** (lines 1235-1243): Checks `ignore_execution_results` and `stopped_run_token` in metadata. If the result belongs to a stopped run, it's discarded.
- `extract_agent_text(raw_output)` -- strips CLI wrapper text, returns `(agent_text, extracted_usage)`
- `parse_envelope(agent_text)` -- parses `ODIN-STATUS SUCCESS/FAIL` envelope, extracts summary
- `compose_comment(verb, duration_ms, metadata, summary_text)` -- formats inline metrics comment like `"Completed in 45.2s . 12,345 tokens . $0.03"`

Metadata stored on task:
- `last_duration_ms` -- execution duration
- `selected_model` -- model used
- `full_output` -- filtered agent text
- `effective_input` -- first 5000 chars of prompt
- `total_estimated_cost_usd` -- accumulated across retries (+=)
- `last_failure_type`, `last_failure_reason`, `last_failure_origin` -- failure classification
- Clears `active_execution` when transitioning from EXECUTING

**Important**: `last_usage` is NOT stored anymore. Usage is computed on-the-fly from comments by `compute_usage_from_trace()`.

Data in: ExecutionResultPayloadSerializer validated data
Data out: Updated task in DB + TaskComment with metrics

---

## 7. Usage Computation from Comments

**File**: `taskit/taskit-backend/tasks/serializers.py`
**Function**: `TaskSerializer.get_usage()` (line 48)
**Called by**: API serialization when reading task data
**Calls**: `compute_usage_from_trace(task)` from `execution_processing.py`

Key logic:
- Not a stored field -- computed every time the task is serialized
- `compute_usage_from_trace()` parses task comments to extract token counts
- Returns `{"input_tokens": int, "output_tokens": int, "total_tokens": int}` or `None`
- This is the **authoritative source** for token usage displayed in UI

Data in: task instance
Data out: usage dict or None

---

## 8. Cost Computation

**File**: `taskit/taskit-backend/tasks/serializers.py`
**Function**: `TaskSerializer.get_estimated_cost_usd()` (line 31)
**Called by**: API serialization
**Calls**: `compute_task_estimated_cost()` from `pricing.py`

Key logic:
- Uses model name + usage data to compute cost
- Model pricing stored per-user in `available_models` (populated by `_ensure_model_on_user()`)
- Cost = (input_tokens * input_price) + (output_tokens * output_price)
- For specs: `SpecSerializer.get_cost_summary()` aggregates across all tasks

Data in: task with usage + model_name
Data out: float cost in USD

---

## 9. Frontend Trace Rendering

**File**: `taskit/taskit-frontend/src/components/TraceViewer.tsx`
**Function**: `parseTrace(raw)` (line 53)
**Called by**: TraceViewer component mount/update
**Calls**: `detectTraceFormat()`, `extractTokenSummary()`, timeline builders

Key logic:
- Splits raw JSONL string by newlines
- Parses each line as JSON into `TraceEvent[]`
- Detects format:
  - `claude_code`: events have `content` arrays, `step_finish`, `modelUsage`
  - `odin`: events have `action` + `run_id` fields
- Extracts token summary:
  - Claude: reads `modelUsage` aggregate event
  - Fallback: sums `step_finish` token events
- Builds timeline:
  - Claude: `tool_use` / `tool_result` / `text` items
  - Odin: phase headers, task lifecycle events, durations

Renders:
- Token summary bar: input/output tokens, cache stats, model names
- Timeline view: chronological events with expandable details
- Raw view: raw JSONL with copy button

Data in: raw JSONL string from comment or execution_trace
Data out: rendered React component

---

## 10. Frontend Metrics Extraction from Comments

**File**: `taskit/taskit-frontend/src/utils/diagnostics.ts`
**Function**: `parseMetricsFromComment(content)` (line 176)
**Called by**: `buildAttemptMetrics()`
**Calls**: nothing

Key logic:
- Regex-based extraction from comment text (e.g. `"Completed in 45.2s . 12,345 tokens"`)
- Duration: matches `in\s+([\d.]+)(s|ms)` -> converts to ms
- Tokens: matches `([\d,]+)\s+tokens` -> removes commas, parses int
- Fallback for when structured `usage` data is unavailable

`buildAttemptMetrics()` priority chain:
1. Duration: `task.metadata.last_duration_ms` > parsed from comments > inferred from mutation timestamps
2. Tokens: `task.usage.total_tokens` > `task.metadata.last_usage` (deprecated) > parsed from comments
3. Cost: `task.estimatedCostUsd`

Data in: comment content string
Data out: `{durationMs?: number, tokens?: number}`

---

## 11. E2E Snapshot Capture

**File**: `taskit/taskit-backend/testing_tools/snapshot_extractor.py`
**Function**: `build_snapshot(spec_id, output_dir, slim)`
**Called by**: CLI invocation
**Calls**: Django ORM queries

Key logic:
- Extracts spec, tasks, comments, history from DB into JSON files
- Computes summary stats: task_count, token totals, duration, harnesses_used, dependency info
- `--slim` mode: excludes `description`, `content`, `full_output`, `effective_input`
- Writes 6 JSON files + README.md to output directory

**What's captured vs. not**:
- Captured: task metadata (usage, duration, cost, model), comments (including trace comments), history mutations
- NOT captured: raw `.trace.jsonl` files from `.odin/logs/` (ephemeral, not in DB)
- Trace data is available indirectly through comments tagged `trace:execution_jsonl`

Data in: spec_id
Data out: 6 JSON files + README in output directory

---

## 12. Snapshot Test Fixtures

**File**: `tests/e2e_snapshots/conftest.py`
**Function**: `load_snapshot(name)` + `full_harness_smoke()` fixture
**Called by**: pytest test collection
**Calls**: json.load() per file

Key logic:
- Discovers all `*.json` files in snapshot directory
- Returns dict keyed by filename stem: `{"tasks": [...], "summary": {...}, ...}`
- Tests access via `snapshot["tasks"]`, `snapshot["comments"]`, etc.
- Golden snapshot: `full_harness_smoke/` (spec #38, 7 tasks, 6 harnesses)

Data in: snapshot directory path
Data out: dict of parsed JSON data
