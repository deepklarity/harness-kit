# Spec-to-Task Planning — Detailed Trace

## 1. CLI Entry Point

**File**: `odin/src/odin/cli.py`
**Function**: `OdinCLI.plan()` (lines ~297-453)
**Called by**: `odin plan` CLI command
**Calls**: `Orchestrator.plan()`

Key logic:
- Accepts `spec_file` (path) OR `--prompt` (inline text), mutually exclusive
- Mode selection: `--auto` → "auto", `--quiet` → "quiet" (implies auto), default → "interactive"
- `--quick` flag skips codebase exploration during planning
- `--base-agent` overrides which agent does the planning
- Creates `Orchestrator(config)` and calls `plan()` async
- On return, prints formatted table of created tasks

Data in: spec text (str), mode (str), quick (bool)
Data out: (spec_id: str, tasks: List[Task])

---

## 2. Spec Archive Creation

**File**: `odin/src/odin/orchestrator.py`
**Function**: `Orchestrator.plan()` (lines ~206-216)
**Called by**: CLI
**Calls**: `_save_spec()` → TaskIt backend or disk

Key logic:
- Title extracted from spec file path or `_extract_title(spec)` (first heading or first line)
- Spec ID format: `sp_YYYYMMDD_HHMMSS[_slug]` via `generate_spec_id(title)`
- `SpecArchive` is a Pydantic model: id, title, source, content, metadata
- `_save_spec()` routes to backend (`POST /api/specs/`) if backend is configured, else local JSON
- Content is stored as-is (immutable snapshot of the spec at planning time)
- `metadata.working_dir` records where the plan was invoked from

Data in: spec text, spec_file path
Data out: SpecArchive persisted, spec_id string

---

## 3. Quota & Agent Discovery

**File**: `odin/src/odin/orchestrator.py`
**Functions**: `_fetch_quota()`, `_build_available_agents()`
**Called by**: `plan()`
**Calls**: `harness_usage_status` providers, harness `is_available()` methods

Key logic:
- `_fetch_quota()` imports `harness_usage_status.providers.registry` dynamically
- Maps agent names to provider names via `QUOTA_PROVIDER_MAP`
- Returns `{agent: {usage_pct: float, remaining_pct: float}}` or `None` on failure
- `_build_available_agents()` iterates registered harnesses, calls `is_available()` on each
- Only agents with `enabled=True` and `is_available()=True` are included

Side effects: None (read-only)
Error handling: Quota fetch fails silently, agent availability is cached

---

## 4. Unified Prompt Construction

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_plan_prompt()` (lines ~284-354)
**Called by**: `plan()`

Key logic:
- Assembles prompt with: planning philosophy, available agents (JSON), model routing priority, quota guidance, spec content, output JSON schema, dependency rules, artifact coordination
- Embeds `plan_path` (exact filesystem path for agent to write plan JSON)
- Agent is instructed to write JSON to this path, NOT to stdout

Data in: spec, available_agents, quota, routing, plan_path
Data out: unified prompt string

---

## 5. Harness Dispatch — THE GAP

**File**: `odin/src/odin/orchestrator.py`

### 5a. Planning dispatch: `_decompose()` (lines ~1179-1221)

```python
context = {"working_dir": working_dir}  # ← ONLY working_dir
```

- Gets base agent harness (usually claude)
- Context has NO `output_file`, NO `trace_file`
- If `stream_callback`: calls `execute_streaming()`, yields chunks to terminal
- Else: calls `execute()`, checks `result.success`
- Agent output is ephemeral — streamed to callback or returned in `result.output`
- After return, only the plan JSON file on disk is used
- **The planning agent's exploration, reasoning, and codebase analysis is lost**

### 5b. Task dispatch (for comparison): `_execute_task()` (lines ~1766-1980)

```python
context = {
    "working_dir": wd,
    "output_file": str(log_dir / f"task_{task_id}.out"),        # ← captured
    "trace_file": str(log_dir / f"task_{task_id}.trace.jsonl"),  # ← captured
    "timeout_seconds": ...,
    "model": selected_model,
}
```

- Harness writes agent stdout to both files via `read_with_trace()`
- After execution, reads trace_file content
- Posts raw JSONL to backend in two ways:
  1. `task_mgr.record_execution_result()` — includes `raw_output` in payload
  2. `task_mgr.add_comment()` — posts as comment with `attachments=["trace:execution_jsonl"]`
- Backend `execution_result()` endpoint parses JSONL, extracts agent text, stores in `task.metadata["full_output"]`

### What the planning dispatch SHOULD do (to match task dispatch):

1. Create `output_file` and `trace_file` paths for the plan run
2. Pass them in context so harness captures agent output
3. After dispatch, read trace content from disk
4. Post trace to Spec on the backend (new mechanism needed — see section 9)

---

## 6. Plan Parsing

**File**: `odin/src/odin/orchestrator.py`
**Function**: `plan()` continuation (lines ~255-268)

Key logic:
- Reads `plan_path.read_text()`
- `_parse_json_array()` handles: raw JSON array, markdown-fenced JSON, JSON within prose
- Validates each item has `title`, `description`

Error handling: Raises RuntimeError if file missing or unparseable

---

## 7. Task Creation — Pass 1: Create & Map

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()` (lines ~376-435)

Key logic:
- For each sub-task:
  - `_route_task()` determines (agent_name, selected_model)
  - Builds metadata: required_capabilities, suggested_agent, complexity, selected_model, reasoning, quota_snapshot, expected_outputs, assumptions
  - `task_mgr.create_task()` creates Task with UUID, links to spec_id
  - `task_mgr.assign_task()` transitions BACKLOG → TODO
  - Posts assumptions as initial comment
  - Maps symbolic ID → real UUID in `symbolic_to_real`

---

## 8. Task Creation — Pass 2: Resolve Dependencies

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()` (lines ~438-461)

Key logic:
- Maps each symbolic dep → real UUID via `symbolic_to_real`
- Unresolvable deps: logged, posted as comment
- Updates `task.depends_on = [real_uuids]`

---

## 9. Backend — Spec Model (Missing Trace Support)

**File**: `taskit/taskit-backend/tasks/models.py` (lines ~88-104)

Current fields:
- `odin_id`, `title`, `source`, `content`, `abandoned`, `board`, `metadata`, `created_at`

What's missing for trace parity with tasks:
- No `SpecComment` model (Task has TaskComment with trace:execution_jsonl)
- No `execution_result` equivalent for specs
- No trace-related fields in metadata schema
- The Spec model has no FK to comments — unlike Task which has `TaskComment` with `related_name="comments"`

Options for adding trace support:
1. **SpecComment model** (mirrors TaskComment): cleanest, supports multiple plan runs per spec
2. **Spec.plan_trace field** (TextField): simpler, but only stores latest trace
3. **Spec.metadata["plan_trace"]** (JSON): no migration needed, but unstructured

---

## 10. Backend — Execution Result Endpoint (Task Only)

**File**: `taskit/taskit-backend/tasks/views.py` (lines ~1005-1124)
**Endpoint**: `POST /tasks/{id}/execution_result/`

How it processes task traces:
1. Receives `execution_result.raw_output` (raw JSONL)
2. `extract_agent_text()` parses JSONL → clean human-readable text
3. `parse_envelope()` extracts ODIN-STATUS block
4. Stores in `task.metadata`: `full_output`, `effective_input`, `last_duration_ms`, `selected_model`, failure fields, cost
5. Creates TaskComment with metrics summary

No equivalent endpoint exists for specs.

---

## 11. Frontend — Task Trace Display (Implemented)

**File**: `taskit/taskit-frontend/src/components/TaskDetailModal.tsx`

How task traces are rendered:
1. `parseCommentBody(raw)` splits comment content into `{summary, traceData}`
   - Scans lines; first valid JSON object starts the trace section
   - Returns summary text (top) + JSONL trace (bottom)
2. Detects `comment.attachments.includes("trace:execution_jsonl")`
3. Renders: "Execution trace" label + Show/Hide toggle + Copy button
4. Trace shown in collapsible `<pre>` block (monospace)
5. Also shows `failureDetails` from task.metadata (failureType, failureReason, failureDebug)

---

## 12. Frontend — Spec Debug View (No Trace)

**File**: `taskit/taskit-frontend/src/components/SpecDebugView.tsx`

What it currently shows:
1. Summary metrics: done/in-progress/stuck/failed counts, duration, tokens
2. Execution timeline (task status transitions, chronological)
3. Dependency DAG (directed graph, color-coded by status)
4. Problems detected (stuck, failed chains, unmet deps)

What it doesn't show:
- Planning agent trace (the JSONL output from codebase exploration + reasoning)
- Planning agent identity (which agent/model did the planning)
- Planning duration and token usage
- Planning decisions (why tasks were structured this way)

---

## Summary: Trace Data Lifecycle Comparison

| Step | Task Execution | Planning |
|------|---------------|----------|
| Context passed to harness | `output_file` + `trace_file` | `working_dir` only |
| Agent output captured to disk | `.odin/logs/task_{id}.trace.jsonl` | Not captured |
| Raw output posted to backend | `record_execution_result(raw_output=...)` | Not posted |
| Backend parses JSONL | `extract_agent_text()` → `task.metadata["full_output"]` | N/A |
| Comment with trace created | `TaskComment(attachments=["trace:execution_jsonl"])` | N/A |
| Frontend renders trace | `TaskDetailModal` collapsible `<pre>` | N/A |
| Frontend detects trace | `attachments.includes("trace:execution_jsonl")` | N/A |
