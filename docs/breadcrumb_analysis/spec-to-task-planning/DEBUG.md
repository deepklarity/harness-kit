# Spec-to-Task Planning — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Odin structured | `.odin/logs/run_<timestamp>.jsonl` | Planning events: plan_started, quota_fetched, decompose_dispatched, task_assigned, plan_completed |
| Odin plan JSON | `.odin/plans/plan_sp_<id>.json` | Raw planning agent JSON output (symbolic tasks, reasoning, suggestions) |
| Odin spec archive | `.odin/specs/sp_<id>.json` | Immutable spec snapshot |
| Planning agent trace | **NOT CAPTURED** (gap) | Would be at `.odin/logs/plan_<spec_id>.trace.jsonl` if captured |
| Task execution trace | `.odin/logs/task_<id>.trace.jsonl` | Structured JSONL from agent harness (for comparison) |
| Task execution output | `.odin/logs/task_<id>.out` | Agent stdout captured by tmux (for comparison) |
| Django | `taskit/taskit-backend/logs/taskit.log` | API requests, spec/task creation |
| Django detail | `taskit/taskit-backend/logs/taskit_detail.log` | Verbose request/response logging |

## What to search for

| Symptom | Where to look | Search term / action |
|---------|--------------|---------------------|
| `odin plan` creates spec but no tasks | `.odin/plans/plan_sp_<id>.json` | Check if file exists; if missing, agent didn't write output |
| Plan file exists but is empty or malformed | `.odin/plans/plan_sp_<id>.json` | `python -m json.tool .odin/plans/plan_sp_<id>.json` |
| Tasks created but wrong agent assigned | `.odin/logs/run_<ts>.jsonl` | Search `"action":"task_assigned"` — shows routing decision per task |
| Dependencies missing on tasks | `.odin/plans/plan_sp_<id>.json` | Check `depends_on` fields in raw plan JSON |
| "Dependency X could not be resolved" | Task comments in TaskIt UI | Symbolic ID mismatch — check `id` fields in plan JSON |
| Spec not visible in UI | `taskit/taskit-backend/logs/taskit.log` | Search for `POST /api/specs/` — did create call succeed? |
| Tasks not linked to spec | `GET /api/specs/<id>/` | Check `tasks` array is empty → spec_id not passed during creation |
| Agent not available during routing | `.odin/logs/run_<ts>.jsonl` | Search `"action":"agent_unavailable"` |
| Planning agent output/reasoning lost | **This is the known gap** | `_decompose()` doesn't capture trace; no output_file/trace_file in context |
| Want to see what planning agent explored | Not possible currently | Would need trace capture in `_decompose()` + backend storage on Spec |
| Plan trace not in UI debug view | Expected behavior (gap) | `SpecDebugView` only shows execution timeline + DAG, not planning trace |
| Task trace visible but plan trace not | By design (gap) | Task uses `record_execution_result()` + comment with `trace:execution_jsonl`; Spec has no equivalent |

## Quick commands

```bash
# Check if a spec was created and its current state
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_odin_id> --brief

# View the raw plan JSON (agent's final output, NOT the trace)
python -m json.tool .odin/plans/plan_sp_<id>.json

# View spec archive on disk
python -m json.tool .odin/specs/sp_<id>.json

# Check structured log for planning events
python -c "
import sys, json
for line in open('.odin/logs/run_latest.jsonl'):
    e = json.loads(line)
    if 'plan' in e.get('action', ''):
        print(json.dumps(e, indent=2))
"

# See which agents got assigned during planning
python -c "
import sys, json
for line in open('.odin/logs/run_latest.jsonl'):
    e = json.loads(line)
    if e.get('action') == 'task_assigned':
        md = e.get('metadata', {})
        print(f\"{e.get('task_id', '?')}: {e.get('agent', '?')} — {md.get('title', '?')}\")
"

# Compare: view a TASK trace (this is what planning should produce too)
cat .odin/logs/task_<task_id>.trace.jsonl | head -20

# Check agent availability
cd odin && python -c "
from odin.harnesses.registry import get_all_harnesses
from odin.config import load_config
cfg = load_config()
for name, h in get_all_harnesses(cfg.agents).items():
    print(f'{name}: available={h.is_available()}')
"

# Inspect a task created by planning (with metadata showing routing decisions)
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic,metadata

# View spec diagnostic (what the frontend debug view shows)
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_odin_id> --sections tasks,problems
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_ADMIN_USER` | Admin email for TaskIt API auth | None (required for backend sync) |
| `ODIN_ADMIN_PASSWORD` | Admin password for TaskIt API auth | None (required for backend sync) |
| `TASKIT_URL` | TaskIt backend base URL | `http://localhost:8000` |
| `ODIN_BASE_AGENT` | Which agent does planning (override) | First available from config |

## Common breakpoints

- `orchestrator.py::_decompose()` — THE GAP: context dict has no output_file/trace_file; this is where trace capture would be added
- `orchestrator.py::plan()` line ~206 — Spec creation: verify spec_id and archive
- `orchestrator.py::_build_plan_prompt()` — Inspect what the planning agent receives
- `orchestrator.py::_create_tasks_from_plan()` — Task creation loop: check routing per task
- `orchestrator.py::_execute_task()` — COMPARISON POINT: see how task trace is captured with output_file + trace_file in context
- `orchestrator.py` lines ~1923-1980 — COMPARISON POINT: see how task trace is posted to backend (record_execution_result + add_comment with trace:execution_jsonl)
- `views.py::SpecViewSet.diagnostic()` — Where spec diagnostic data is assembled (would need to include trace)
- `TaskDetailModal.tsx::parseCommentBody()` — How task traces are parsed and rendered (pattern to replicate for specs)

## The gap in detail: why planning trace is lost

### What happens during task execution (trace captured)

```
_execute_task()
  ├─ context["output_file"] = ".odin/logs/task_{id}.out"
  ├─ context["trace_file"] = ".odin/logs/task_{id}.trace.jsonl"
  ├─ harness captures agent output via read_with_trace(proc, output_file, trace_file)
  ├─ after execution:
  │   ├─ reads trace_file content
  │   ├─ task_mgr.record_execution_result(raw_output=trace_content)
  │   │   └─ POST /tasks/{id}/execution_result/ → backend stores in metadata["full_output"]
  │   └─ task_mgr.add_comment(content=trace_content, attachments=["trace:execution_jsonl"])
  │       └─ POST /tasks/{id}/comments/ → TaskComment row
  └─ frontend: TaskDetailModal finds comment with trace:execution_jsonl
      └─ parseCommentBody() → shows collapsible <pre> with copy button
```

### What happens during planning (trace NOT captured)

```
_decompose()
  ├─ context = {"working_dir": wd}  ← no output_file, no trace_file
  ├─ harness.execute_streaming() → chunks sent to terminal callback (ephemeral)
  │   OR harness.execute() → result.output (not persisted)
  ├─ after execution:
  │   ├─ reads plan JSON from plan_path (the structured output, NOT the trace)
  │   └─ nothing else — trace is gone
  └─ frontend: SpecDebugView has no trace panel, no mechanism to show it
```

### To close this gap, changes needed at each layer

| Layer | File | Change |
|-------|------|--------|
| **Odin: capture** | `orchestrator.py::_decompose()` | Add `output_file` + `trace_file` to context, read after dispatch |
| **Odin: post** | `orchestrator.py::plan()` | Post trace to backend after task creation (new method on task_mgr or direct API call) |
| **Backend: model** | `tasks/models.py` | Add `SpecComment` model (mirrors TaskComment) or add trace field to Spec |
| **Backend: serializer** | `tasks/serializers.py` | Include trace in `SpecDiagnosticSerializer` |
| **Backend: endpoint** | `tasks/views.py` | Accept trace via new endpoint or extend existing Spec endpoints |
| **Frontend: type** | `types/index.ts` | Add `planTrace` field to Spec type |
| **Frontend: display** | `SpecDebugView.tsx` | Add "Planning Trace" panel (reuse `parseCommentBody()` pattern from TaskDetailModal) |
