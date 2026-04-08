# Orchestrator — Plan Internals

Trigger: `odin plan <spec_file>` from CLI, or `odin plan --direct <spec_file>` from web UI PTY
End state: Spec archive created, plan JSON on disk, tasks on board with suggested agent assignments

## CLI Flags

| Flag | Effect |
|------|--------|
| `--auto` | Skip interactive session, one-shot with streaming output |
| `--quiet` | Implies `--auto`, shows spinner instead of streaming |
| `--quick` | Instruct LLM to skip codebase exploration |
| `--direct` | Run agent as direct subprocess (no tmux). Used by web UI. |
| `--base-agent` | Override which agent does planning (e.g. `codex`) |
| `--base-model` | Override which model the planning agent uses |

## Flow

```
cli.py :: OdinCLI.plan()
  → validate input (spec_file xor prompt), load spec text
  → create Orchestrator(config)

orchestrator.py :: Orchestrator.plan()
  → generate spec_id: sp_YYYYMMDD_HHMMSS_slug
  → create SpecArchive(id, title, source, content, metadata)
  → save to .odin/specs/ AND TaskIt backend (if configured)
  → derive plan_path = .odin/plans/plan_<spec_id>.json

  → _fetch_quota() — {agent: {usage_pct, remaining_pct}} (graceful: {} on failure)
  → _fetch_routing_config() — GET /boards/{id}/routing-config/ (graceful: None on failure)
  → _build_available_agents(quota, routing_config)
  → _build_plan_prompt(spec, plan_path, agents, quota, quick)
     → available agents JSON with capabilities, cost_tier, quota data
     → routing priority section
     → task schema + dependency + artifact coordination rules
     → instruction: "Write your final plan JSON to <plan_path>"
     → if quick: "Do NOT explore or read the codebase"

  [interactive + direct]
  interactive.py :: InteractivePlanSession._run_direct()
    → writes system prompt to temp file
    → builds CLI command via harness.build_interactive_command()
    → expands __FILE__: markers via bash $(cat ...)
    → subprocess.run(["bash", "-c", cmd_str]) — inherits PTY stdin/stdout
    → agent writes plan JSON to plan_path
    → catches KeyboardInterrupt so orchestrator can continue

  [interactive + tmux (default CLI)]
  interactive.py :: InteractivePlanSession.run()
    → launches tmux session, user chats with agent
    → agent writes plan JSON to plan_path
    → blocks until user exits tmux

  [auto: --auto]
  orchestrator.py :: _decompose()
    → streaming subprocess, agent writes plan JSON to plan_path
    → stream chunks to stdout for visibility
    → TRACE NOT CAPTURED (no trace_file in context — known gap)

  [quiet: --quiet]
  orchestrator.py :: _decompose()
    → subprocess with spinner, agent writes plan JSON to plan_path

orchestrator.py :: plan() (continued)
  → read plan_path (file exists or clean RuntimeError)
  → _parse_json_array() — handles raw JSON, markdown-fenced, JSON within prose

orchestrator.py :: _create_tasks_from_plan()  [Pass 1]
  → for each sub-task:
    → _route_task(capabilities, complexity, suggested_agent, quota, routing_config)
      → Phase 1: honour LLM suggestion (try routes for suggested agent first)
      → Phase 2: walk config.model_routing priority list
      → Phase 3: fallback _pick_agent() + _pick_model()
      → each step checks: capabilities, agent enabled, model not banned, quota OK
    → task_mgr.create_task(title, description, metadata, spec_id)
    → task_mgr.assign_task(task.id, agent_name)
    → posts assumptions as initial comment
    → builds symbolic_to_real map: {"task_1": "a1b2c3d4e5f6"}

orchestrator.py :: _create_tasks_from_plan()  [Pass 2]
  → resolves depends_on: symbolic IDs → real UUIDs via map
  → missing deps logged as warning, silently skipped
  → updates each task with resolved dependency list

cli.py :: OdinCLI.plan() (return)
  → prints formatted task table
  → [--quick + taskit backend]: auto-move assigned tasks to IN_PROGRESS
```

## Key properties

- One LLM call per plan. No fallback second call.
- Structured data never flows through the terminal. Agent writes JSON to disk.
- Spec archive exists before the LLM runs. Agent knows the spec_id and plan_path.
- One prompt builder. All modes get the same rules, schema, and quota context.
- Modes only differ in UX wrapper (direct/tmux/streaming/spinner), not in intelligence.

## Data shape: sub-task (LLM output)

```json
{
  "id": "task_1",
  "title": "short title",
  "description": "self-contained prompt for executing agent",
  "required_capabilities": ["code"],
  "suggested_agent": "claude",
  "suggested_model": "claude-sonnet-4-5",
  "complexity": "low | medium | high",
  "depends_on": ["task_0"],
  "expected_outputs": ["file.py"],
  "assumptions": ["some assumption"],
  "reasoning": "why this agent"
}
```

## Known gap: planning trace not captured

Planning agent output (codebase exploration, reasoning) is ephemeral — lost after the session.
Task execution captures traces via `output_file` + `trace_file` in context; planning does not.
See DETAILS.md §5 for the gap analysis and §9 for the fix plan.
Cross-ref: `trace-data-pipeline/` for how task traces work.
