# Orchestrator — Detailed Trace

## 1. CLI Entry Point

**File**: `odin/src/odin/cli.py`
**Function**: `OdinCLI.plan()`
**Called by**: Fire CLI dispatch (`odin plan ...`)
**Calls**: `Orchestrator.plan()`

Key logic:
- Input: `spec_file` xor `prompt` required. If both missing, exits with usage.
- `base_agent` / `base_model` overrides applied to config before creating Orchestrator.
- Mode selection: `--auto` → "auto", `--quiet` → "quiet" (implies auto), default → "interactive".
- `--direct` flag: passed through to `orchestrator.plan(direct=True)`. Used by web UI.
- All branches call the same `Orchestrator.plan()` — mode is a UX parameter, not a logic fork.
- Post-plan: `--quick` + TaskIt backend triggers auto-queue — moves assigned tasks to `IN_PROGRESS`.

---

## 2. Orchestrator.plan() — Coordinator

**File**: `odin/src/odin/orchestrator.py`
**Function**: `plan()`
**Called by**: CLI
**Calls**: `_save_spec()`, `_fetch_quota()`, `_fetch_routing_config()`, `_build_plan_prompt()`, harness dispatch, `_create_tasks_from_plan()`

Key logic:
- Spec archive created FIRST with `generate_spec_id(title)` — deterministic ID from title+timestamp.
- Saved to `.odin/specs/` and TaskIt backend if configured.
- Derives `plan_path = .odin/plans/plan_<spec_id>.json`.
- Builds unified prompt via `_build_plan_prompt()` with plan_path baked in.
- Dispatches to harness based on mode (see §4).
- After LLM completes: reads plan_path, parses JSON, creates tasks.

---

## 3. _build_plan_prompt() — Unified Prompt Builder

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_plan_prompt()`

Key logic:
- Single function used by all modes. No separate prompt for interactive vs auto.
- Includes: planning philosophy, available agents JSON (capabilities, cost_tier, models, quota data), routing priority, quota instruction, task schema, dependency rules, artifact coordination rules.
- Embeds `plan_path` as the exact filesystem path where agent must write plan JSON.
- Does NOT tell the agent to output JSON to the terminal.

---

## 4. Agent Dispatch — Three Paths

### 4a. Interactive + direct (web UI)

**File**: `odin/src/odin/interactive.py`
**Function**: `InteractivePlanSession._run_direct()`

Key logic:
- System prompt written to temp file.
- Command built via `harness.build_interactive_command()`.
- `__FILE__:` markers expanded via bash `$(cat ...)` to avoid exec arg length limit.
- `subprocess.run(["bash", "-c", cmd_str], cwd=working_dir)` — inherits PTY stdin/stdout.
- `KeyboardInterrupt` caught so orchestrator can still check for plan file and create tasks.
- Returns `None` (no transcript capture in direct mode).

### 4b. Interactive + tmux (default CLI)

**File**: `odin/src/odin/interactive.py`
**Function**: `InteractivePlanSession.run()`

Key logic:
- tmux required. Session ID is random 12-char hex.
- `launch_and_attach()` creates tmux session, blocks until user exits.
- Transcript captured via `tmux capture-pane` — for debugging only, plan data is NOT extracted from it.

### 4c. Auto / quiet

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_decompose()`

Key logic:
- Context: `{"working_dir": wd}` — NO `output_file`, NO `trace_file`.
- Auto mode: `execute_streaming()` with stream callback to terminal.
- Quiet mode: `execute()` blocking with spinner.
- Agent output is ephemeral — displayed once, then lost.
- **This is the trace gap.** See §9.

---

## 5. Plan Parsing

**File**: `odin/src/odin/orchestrator.py`
**Function**: `plan()` continuation

Key logic:
- Reads `plan_path.read_text()`.
- `_parse_json_array()` handles: raw JSON array, markdown-fenced JSON, JSON within prose.
- Validates each item has `title`, `description`.
- Raises `RuntimeError` if file missing or unparseable.

---

## 6. Task Creation — Pass 1: Create & Map

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()`

Key logic:
- For each sub-task:
  - `_route_task()` selects (agent_name, selected_model, routing_reasoning).
  - Metadata: required_capabilities, suggested_agent, complexity, selected_model, reasoning, quota_snapshot, expected_outputs, assumptions.
  - `task_mgr.create_task()` with `spec_id` tag.
  - `task_mgr.assign_task()` transitions BACKLOG → TODO.
  - Posts assumptions as initial comment.
  - Builds `symbolic_to_real` map: `"task_1" -> "a1b2c3d4..."`.

---

## 7. Task Creation — Pass 2: Resolve Dependencies

Key logic:
- Maps each symbolic dep → real UUID via `symbolic_to_real`.
- Unresolvable deps: logged as warning, posted as comment, silently skipped.
- Updates `task.depends_on = [real_uuids]`.

---

## 8. _route_task() — Agent/Model Selection

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_route_task()`

Key logic:
- **Phase 1** — Honour LLM suggestion: try routes for suggested agent first.
- **Phase 2** — Walk priority list: iterate `config.model_routing` in order. Each route checked: agent enabled, capabilities match, model not banned, quota OK.
- **Phase 3** — Fallback: `_pick_agent()` + `_pick_model()`.
- Availability cached per agent per plan run.
- If `routing_config` from API: uses `_route_task_api()` with API-sourced enabled models.
- Fallback: `_route_task_config()` walks config.

Cross-ref: `intelligent-agent-routing/` for routing algorithm depth.

---

## 9. Known Gap: Planning Trace Not Captured

### Task execution (trace captured — for comparison)

```
_execute_task()
  ├─ context["output_file"] = ".odin/logs/task_{id}.out"
  ├─ context["trace_file"] = ".odin/logs/task_{id}.trace.jsonl"
  ├─ harness captures via read_with_trace()
  ├─ after: posts raw JSONL to backend via record_execution_result()
  └─ also: TaskComment with attachments=["trace:execution_jsonl"]
```

### Planning (trace NOT captured)

```
_decompose()
  ├─ context = {"working_dir": wd}  ← no output_file, no trace_file
  ├─ harness output → terminal callback (ephemeral) or result.output (not persisted)
  └─ only plan JSON on disk survives
```

### Fix plan (changes needed per layer)

| Layer | File | Change |
|-------|------|--------|
| Odin: capture | `orchestrator.py::_decompose()` | Add `output_file` + `trace_file` to context |
| Odin: post | `orchestrator.py::plan()` | Post trace to backend after task creation |
| Backend: model | `tasks/models.py` | Add `SpecComment` model (mirrors TaskComment) |
| Backend: serializer | `tasks/serializers.py` | Include trace in `SpecDiagnosticSerializer` |
| Backend: endpoint | `tasks/views.py` | Accept trace via new endpoint |
| Frontend: type | `types/index.ts` | Add `planTrace` to Spec type |
| Frontend: display | `SpecDebugView.tsx` | Add "Planning Trace" panel |
