# Intelligent Agent Routing

Trigger: `odin plan <spec>` (any mode: interactive, auto, quiet)
End state: Tasks created with distributed agent/model assignments; routing config and per-task reasoning visible in frontend

## Flow

```
[Bootstrap: agent metadata seeding]
taskit/taskit-backend/seedmodels/agent_models.json
  → python manage.py seed_agents (run once on DB setup)
  → creates User records for each agent + AgentProfile with metadata
  → board creation (POST /boards/) → creates BoardMembership for all enabled agents

odin/src/odin/orchestrator.py :: plan()
  → calls _fetch_routing_config() → GET /boards/{board_id}/routing-config/
    → returns routing_config: {agents: [...], model_routing: [...]}
  → fetches quota data (graceful degradation if unavailable)
  → calls _build_available_agents(quota, routing_config) → agent roster with DB metadata + quota
  → calls _build_plan_prompt() → unified prompt with agent roster + tier distribution guidance
  → dispatches to planning harness (_decompose or interactive tmux)
  → reads plan JSON from disk (agent wrote it to plan_path)

odin/src/odin/orchestrator.py :: _create_tasks_from_plan()
  → for each sub-task in plan:

    odin/src/odin/orchestrator.py :: _route_task()
      [Phase 1: planner-suggested model]
      → if planner suggested a specific model (suggested_model field):
        → use it if viable+available
        → reasoning: "Routed to X/Y (suggested model)"

      [Phase 2: planner-suggested agent]
      → if planner suggested an agent (suggested_agent field) AND it's viable+available:
        → use agent's default model, apply premium upgrade if high complexity
        → reasoning: "Routed to X/Y (suggested agent)"

      [Phase 3: tier distribution — when no suggestion or suggestion invalid]
        → _route_task_api() (primary path, uses routing_config from API)
          → collect all viable routes from routing_config.model_routing
          → group by cost tier (LOW < MEDIUM < HIGH)
          → pick cheapest tier with viable routes
          → random.choice() among routes in that tier
          → apply premium upgrade if high complexity
          → reasoning: "Routed to X/Y (LOW tier, chosen from N viable routes)"

        → _route_task_config() (fallback, if routing_config unavailable)
          → uses config.model_routing from .odin/config.yaml
          → same tier distribution logic as _route_task_api()

    → stores routing_reasoning + selected_model in task_metadata
    → creates task via task_mgr.create_task()
    → assigns task to chosen agent via task_mgr.assign_task()

[No longer persisted to spec metadata — routing config lives in Board/BoardMembership]
```

### Frontend display (read path)

```
taskit-frontend/src/components/SpecDetailView.tsx
  → fetches spec.board → GET /boards/{id}/routing-config/ → displays routing config
  → reads task.metadata.selected_model → shows agent/model badge on each task card

taskit-frontend/src/components/TaskDetailModal.tsx
  → reads task.metadata.routing_reasoning → renders "Routing" CompactRow in Execution Context
  → reads task.metadata.suggested_model → shows what planner requested (if any)

taskit-frontend/src/components/BoardSettingsModal.tsx
  → toggle agent availability via BoardMembership.is_active
  → toggle model preference via BoardMembership.preferred_model
```

## Traceability chain

```
seedmodels/agent_models.json
  → DB (User + AgentProfile + BoardMembership)
    → GET /boards/{id}/routing-config/ API response
      → odin routing_config (passed to _route_task)
        → Per-task routing_reasoning (visible in TaskDetailModal)
          → "Routed to glm/glm-4.7 (LOW tier, chosen from 4 viable routes)"
```
