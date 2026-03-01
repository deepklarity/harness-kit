# Spec-to-Task Planning Flow

Trigger: User runs `odin plan <spec_file>` (or `odin plan --prompt "..."`)
End state: Tasks created on TaskIt board, linked to spec. Spec visible in UI with debug view.

## Flow

```
cli.py :: OdinCLI.plan()
  → reads spec from file or --prompt, creates Orchestrator
  → calls orchestrator.plan(spec, mode="auto"|"interactive"|"quiet")

orchestrator.py :: Orchestrator.plan()
  → generates spec_id: sp_YYYYMMDD_HHMMSS_slug
  → creates SpecArchive(id, title, source, content, metadata)
  → saves to .odin/specs/sp_<id>.json AND TaskIt backend (if available)

orchestrator.py :: Orchestrator._fetch_quota()
  → queries harness_usage_status providers for each agent
  → returns {agent: {usage_pct, remaining_pct}} (graceful degradation)

orchestrator.py :: Orchestrator._fetch_routing_config()
  → calls backend.fetch_routing_config() → GET /boards/{id}/routing-config/
  → returns {agents: [{name, cost_tier, capabilities, models: [{name, enabled}]}]}
  → graceful degradation: returns None if API unavailable

orchestrator.py :: Orchestrator._build_available_agents(quota, routing_config)
  → if routing_config available: uses API data with per-model enabled state
  → fallback: checks each registered harness.is_available(), uses config data
  → returns [{name, capabilities, cost_tier, models, usage_pct}]

orchestrator.py :: Orchestrator._build_plan_prompt()
  → assembles unified prompt with: spec, agents, quotas, schema
  → embeds plan_path: .odin/plans/plan_sp_<id>.json (agent must write here)
  → planner can now suggest specific models via "suggested_model" field

  [mode=interactive]
  orchestrator.py :: _run_interactive_plan()
    → launches tmux session with harness CLI
    → user chats with planning agent
    → agent writes plan JSON to plan_path on disk
    → TRACE NOT CAPTURED (tmux session output is ephemeral)

  [mode=auto|quiet]
  orchestrator.py :: _decompose()
    → dispatches prompt to base agent harness
    → context = {"working_dir": wd}  ← NO output_file, NO trace_file
    → harness.execute_streaming() or harness.execute()
    → agent output streamed to terminal (auto) or discarded (quiet)
    → agent writes plan JSON to plan_path on disk
    → TRACE NOT CAPTURED (no trace_file in context)

orchestrator.py :: Orchestrator.plan() (continued)
  → reads plan_path from disk
  → parses JSON array of sub-tasks

orchestrator.py :: _create_tasks_from_plan()  [Pass 1]
  → for each sub-task:
    → _route_task(capabilities, complexity, suggested_agent, quota, routing_config)
      → if routing_config available: _route_task_api() uses API-sourced enabled models
      → fallback: _route_task_config() walks config.model_routing priority
      → returns (agent_name, selected_model, routing_reasoning)
    → task_mgr.create_task(title, description, metadata, spec_id)
      → routes to TaskIt backend API or local .odin/tasks/ disk
    → task_mgr.assign_task(task.id, agent_name)
      → status transitions BACKLOG → TODO
    → posts assumptions as initial comment
    → builds symbolic_to_real map: {"task_1": "a1b2c3d4e5f6"}

orchestrator.py :: _create_tasks_from_plan()  [Pass 2]
  → resolves depends_on: symbolic IDs → real UUIDs
  → updates each task with real dependency list
  → logs warnings for unresolvable deps

cli.py :: OdinCLI.plan() (return)
  → receives (spec_id, [Task, ...])
  → displays formatted table: task_id, title, agent, model, deps, status
```

## Comparison: Task Trace vs Plan Trace

```
TASK EXECUTION (trace captured):
  orchestrator.py :: _execute_task()
    → context = {
        "working_dir": wd,
        "output_file": ".odin/logs/task_{id}.out",       ← captures agent stdout
        "trace_file": ".odin/logs/task_{id}.trace.jsonl", ← captures structured JSONL
      }
    → harness writes agent output to both files via read_with_trace()
    → after execution:
      → raw JSONL read from trace_file
      → posted to backend via task_mgr.record_execution_result()
        → raw_output stored in execution_result payload
        → backend parses JSONL → extracts agent text → stores in task.metadata["full_output"]
      → also posted as TaskComment with attachments=["trace:execution_jsonl"]
    → frontend TaskDetailModal.tsx:
      → parseCommentBody() splits comment into summary + traceData
      → detects attachments.includes("trace:execution_jsonl")
      → renders trace in collapsible <pre> block with copy button

PLANNING (trace NOT captured):
  orchestrator.py :: _decompose()
    → context = {"working_dir": wd}  ← no output_file, no trace_file
    → harness streams output to terminal callback or returns result
    → output is ephemeral — displayed once, then lost
    → only plan JSON persisted to .odin/plans/plan_sp_<id>.json
    → NO execution_result posted to Spec
    → NO comment with trace posted to Spec
    → Spec model has no trace-related fields
    → SpecDebugView has no trace panel
```

## TaskIt Backend: Spec vs Task Models

```
tasks/models.py :: Spec
  → fields: odin_id, title, source, content, abandoned, board, metadata, created_at
  → NO execution trace fields
  → NO comments relation (unlike Task which has TaskComment FK)
  → metadata is generic JSON (stores working_dir only)

tasks/models.py :: Task
  → fields: spec (FK), depends_on (JSON), complexity, metadata, model_name, ...
  → has TaskComment relation (comments with trace:execution_jsonl)
  → metadata stores: full_output, effective_input, last_duration_ms, selected_model,
    last_failure_type, last_failure_reason, total_estimated_cost_usd
```

## TaskIt Frontend: Where Traces Show

```
TaskDetailModal.tsx (TASK trace — implemented):
  → comment with attachments=["trace:execution_jsonl"]
  → parseCommentBody() extracts traceData from JSONL content
  → shows "Execution trace" label + Show/Hide toggle + Copy button
  → renders raw JSONL in <pre> block

SpecDebugView.tsx (SPEC/PLAN trace — NOT implemented):
  → shows execution timeline (task status changes over time)
  → shows dependency DAG
  → shows problems detected
  → NO planning agent trace panel
  → NO equivalent of TaskComment trace for specs
```

## Gap: Planning Agent Trace Not Captured or Surfaced

The planning agent (claude/codex/etc.) generates significant output during `odin plan`:
codebase exploration, reasoning about decomposition, agent selection rationale.
This trace is equivalent to what tasks produce but is currently lost.

To surface the plan trace end-to-end, changes are needed at every layer:

```
1. orchestrator.py :: _decompose()
   → pass output_file + trace_file in context (same as _execute_task does)
   → after dispatch, read trace_file

2. orchestrator.py :: plan()
   → post trace to backend (new: record_plan_result on Spec, or SpecComment)

3. tasks/models.py
   → either: add SpecComment model (mirroring TaskComment)
   → or: add plan_trace field to Spec model
   → or: use Spec.metadata to store trace

4. tasks/serializers.py
   → expose trace in SpecDiagnosticSerializer

5. tasks/views.py
   → include trace in /specs/<id>/diagnostic/ response

6. SpecDebugView.tsx
   → add "Planning Trace" panel (collapsible <pre> with copy, like TaskDetailModal)
   → parse JSONL same as parseCommentBody() does
```
