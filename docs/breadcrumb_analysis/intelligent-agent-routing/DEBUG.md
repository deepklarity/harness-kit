# Intelligent Agent Routing — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Odin CLI | `.odin/logs/run_*.jsonl` | `decompose_dispatched`, `decomposition_complete`, `plan_completed` events |
| Odin orchestrator | stderr (when `--auto`) | Streaming output from planning agent |
| TaskIt backend | `taskit/taskit-backend/logs/taskit.log` | PATCH requests for spec metadata updates |
| Frontend | browser console | API fetch errors for spec/task data |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| All tasks assigned to one agent | Task metadata in TaskIt | Check `routing_reasoning` — if all say "suggested model" or "suggested agent", planner is over-suggesting; if tier distribution, check if only one agent is available in BoardMembership |
| `RuntimeError: No viable route` | Odin stderr / run log | `No viable route for task` — lists all tried routes; check BoardMembership is_active status |
| Premium model not upgrading | Task metadata | `routing_reasoning` should contain "upgraded to premium" for high-complexity tasks; if absent, check `premium_model` in AgentProfile and `banned_models` in config |
| Routing config missing from spec page | TaskIt API | Check if `GET /boards/{id}/routing-config/` returns data; verify BoardMembership records exist for the board |
| Wrong tier selected | Task metadata `routing_reasoning` | Check that `cost_tier` values in AgentProfile match expectations; verify DB seed data from agent_models.json |
| Agent marked unavailable | Odin debug log | `_is_available_cached` caches per session — restart `odin plan` to refresh |
| Quota bypass for high complexity | Task metadata | High-complexity tasks skip quota threshold check by design — check `complexity` field |
| Agent not appearing in routing | BoardMembership table | Check `is_active=True` for the board; verify agent User has `is_agent=True` and AgentProfile exists |
| Model override not working | BoardMembership.preferred_model | Check if preferred_model is set correctly; verify it overrides agent default in routing-config API response |
| Planner suggesting unavailable models | Plan JSON + routing config | Compare `suggested_model` in plan to routing_config.model_routing; planner may hallucinate models not in the roster |

## Quick commands

```bash
# Check routing distribution for a spec (shows all task assignees)
cd taskit/taskit-backend
python testing_tools/spec_trace.py <spec_id> --json --sections tasks | python -c "
import json, sys, collections
data = json.load(sys.stdin)
agents = [t.get('assignee', 'unassigned') for t in data.get('tasks', [])]
print(dict(collections.Counter(agents)))
"

# Check routing reasoning for a specific task
python testing_tools/task_inspect.py <task_id> --json --sections basic | python -c "
import json, sys
data = json.load(sys.stdin)
meta = data.get('metadata', {})
print('Routing:', meta.get('routing_reasoning', 'N/A'))
print('Model:', meta.get('selected_model', 'N/A'))
print('Complexity:', meta.get('complexity', 'N/A'))
print('Suggested agent:', meta.get('suggested_agent', 'N/A'))
print('Suggested model:', meta.get('suggested_model', 'N/A'))
"

# Check routing config from board API
curl -s http://localhost:8765/api/boards/<board_id>/routing-config/ | python -m json.tool

# Check BoardMembership for a board (agent on/off state)
cd taskit/taskit-backend
python manage.py shell -c "
from boards.models import BoardMembership
for m in BoardMembership.objects.filter(board_id=<board_id>):
    print(f'{m.user.username}: active={m.is_active}, preferred={m.preferred_model}')
"

# Check agent metadata from DB
python manage.py shell -c "
from django.contrib.auth import get_user_model
from boards.models import AgentProfile
User = get_user_model()
for user in User.objects.filter(is_agent=True):
    profile = user.profile
    print(f'{user.username}: tier={profile.cost_tier}, default={profile.default_model}, premium={profile.premium_model}, enabled={profile.enabled}')
"

# Re-seed agents from JSON (if schema changed)
python manage.py seed_agents

# Run routing unit tests
cd odin && python -m pytest tests/unit/test_routing.py -v

# Check config fallback routing (used when API unavailable)
cd odin && python -c "
from odin.config import load_config
cfg = load_config()
for name, agent in cfg.agents.items():
    if agent.enabled:
        print(f'{name}: tier={agent.cost_tier.value}, default={agent.default_model}, premium={agent.premium_model}')
print()
print('Model routing (config fallback):')
for i, r in enumerate(cfg.model_routing, 1):
    print(f'  {i}. {r.agent}/{r.model}')
"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| Config file `.odin/config.yaml` | Fallback routing when API unavailable; defines `banned_models`, `quota_threshold` | Built-in defaults in `config.py` |
| `seedmodels/agent_models.json` | Source of truth for agent metadata seeded into DB | Checked into repo |
| `ODIN_ADMIN_USER` / `ODIN_ADMIN_PASSWORD` | Required for TaskIt backend routing-config API calls | None (connects without auth for local dev) |
| BoardMembership.is_active | Per-board agent on/off toggle (DB-backed) | True for all enabled agents on board creation |
| BoardMembership.preferred_model | Per-board model override (DB-backed) | NULL (uses agent default_model) |

## Common breakpoints

- `orchestrator.py:_route_task()` Phase 3 (after `viable_routes` list built) — check what routes passed viability
- `orchestrator.py:_route_task_api()` vs `_route_task_config()` — verify which path is being used
- `orchestrator.py:_fetch_routing_config()` — check API response from TaskIt
- `orchestrator.py:_route_viable()` — add print for each route to see which check fails
- `orchestrator.py:_maybe_upgrade_model()` — verify premium upgrade logic triggers correctly
- `boards/views.py:RoutingConfigView.get()` — verify BoardMembership query and agent metadata serialization
- `boards/views.py:BoardViewSet.create()` — verify create_memberships() is called
- `boards/models.py:Board.create_memberships()` — verify enabled agents are being added
- `seedmodels/agent_models.json` + `seed_agents.py` — verify agent metadata matches DB
