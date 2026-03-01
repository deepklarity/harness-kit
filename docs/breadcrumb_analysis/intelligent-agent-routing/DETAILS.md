# Intelligent Agent Routing — Detailed Trace

## 1. Agent Metadata Seeding

**File**: `taskit/taskit-backend/seedmodels/agent_models.json`
**Migration**: `taskit/taskit-backend/boards/management/commands/seed_agents.py`
**Triggered by**: `python manage.py seed_agents` (run once on DB setup)

Key logic:
- JSON defines agents with metadata: name, display_name, cost_tier, capabilities, default_model, premium_model, etc.
- `seed_agents` command creates User records (username = agent name, is_agent=True)
- Creates AgentProfile for each agent with metadata from JSON
- Board creation flow calls `boards.models.Board.create_memberships()` to create BoardMembership records for all enabled agents

Data schema (agent_models.json):
```json
{
  "agents": [
    {
      "name": "qwen",
      "display_name": "Qwen Agent",
      "cost_tier": "low",
      "default_model": "qwen3-coder",
      "premium_model": "qwen3-coder",
      "capabilities": ["code"],
      "enabled": true
    },
    ...
  ]
}
```

Data out: DB records (User, AgentProfile, BoardMembership per board)

---

## 2. Routing Config API Endpoint

**File**: `taskit/taskit-backend/boards/views.py`
**Endpoint**: `GET /boards/{id}/routing-config/`
**Function**: `RoutingConfigView.get()` (line ~TBD)
**Called by**: `odin/src/odin/orchestrator.py :: _fetch_routing_config()`

Key logic:
- Fetches board → filters BoardMembership where `is_active=True`
- For each active membership, reads agent.profile metadata (cost_tier, capabilities, models, etc.)
- Builds `agents` list with full metadata from DB
- Builds `model_routing` list from BoardMembership.preferred_model (or agent default)
- Returns `{agents: [...], model_routing: [...]}`

Data out:
```json
{
  "agents": [
    {
      "name": "qwen",
      "display_name": "Qwen Agent",
      "cost_tier": "low",
      "default_model": "qwen3-coder",
      "premium_model": "qwen3-coder",
      "capabilities": ["code"],
      "usage_pct": 45.2
    },
    ...
  ],
  "model_routing": [
    {"agent": "qwen", "model": "qwen3-coder"},
    {"agent": "gemini", "model": "gemini-2.5-flash"},
    ...
  ]
}
```

---

## 3. Odin Fetches Routing Config

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_fetch_routing_config()` (line ~TBD)
**Called by**: `plan()` early in execution

Key logic:
- Calls `self.backend.get_routing_config(board_id)`
- TaskIt backend: HTTP GET to `/boards/{id}/routing-config/`
- Caches result in `self.routing_config` for session
- Graceful degradation: if API fails, falls back to config.yaml model_routing

Data in: board_id (from spec or CLI)
Data out: Dict with `agents` and `model_routing` keys

---

## 4. Plan Prompt Construction

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_available_agents()` (line ~TBD)
**Called by**: `plan()` after fetching routing config

Key logic:
- If `routing_config` available, uses `routing_config["agents"]` (from API, includes DB metadata)
- Otherwise falls back to `self.config.agents` (from config.yaml)
- Merges quota data if available (usage_pct per agent)
- Returns list of dicts with: name, display_name, cost_tier, capabilities, models, usage_pct

Data in: quota dict (optional), routing_config dict (optional)
Data out: List of agent metadata dicts

---

**Function**: `_build_plan_prompt()` (line ~418)
**Called by**: `plan()` after `_build_available_agents()`

Key logic:
- Embeds `available_agents` JSON (now includes DB metadata + quota)
- Appends tier distribution guidance (embedded in prompt, not from `_build_routing_section`)
- Includes instruction: "You MAY suggest specific models via `suggested_model` field if you have a strong rationale"
- Tells agent to write plan JSON to disk at `plan_path`

Data in: spec text, plan_path, available_agents list, quota dict, quick flag
Data out: Complete prompt string

---

## 5. Task Creation & Routing

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()` (line ~494)
**Called by**: `plan()` after reading plan JSON

Key logic:
- Two-pass: Pass 1 creates tasks + routes them; Pass 2 resolves symbolic dependency IDs
- For each sub-task, calls `_route_task()` → `(agent, model, routing_reasoning)`
- Stores `routing_reasoning` in `task_metadata` alongside `selected_model`, `complexity`, `suggested_agent`, `suggested_model`

Data in: `sub_tasks` (list of dicts from plan JSON), `spec_id`, `quota`, `routing_config`
Data out: List of created `Task` objects

---

## 6. Route Selection (Core)

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_route_task()` (line ~TBD)
**Called by**: `_create_tasks_from_plan()` for each sub-task
**Returns**: `Tuple[str, Optional[str], str]` — `(agent, model, routing_reasoning)`

### Phase 1: Planner-Suggested Model (new)

- If `suggested_model` in task dict, extract agent name and model
- Check if viable+available via `_route_viable()` + `_is_available_cached()`
- Reasoning: `"Routed to {agent}/{model} (suggested model)"`
- Returns immediately if successful — later phases are skipped

### Phase 2: Planner-Suggested Agent

- If `suggested_agent` in task dict (but no `suggested_model`), try `_try_routes_for_agent(suggested_agent, ...)`
- Uses agent's default_model from routing_config
- Applies `_maybe_upgrade_model()` for high complexity
- Reasoning: `"Routed to {agent}/{model} (suggested agent{upgrade_suffix})"`
- Returns immediately if successful

### Phase 3: Tier Distribution (API-backed primary path)

- Calls `_route_task_api(routing_config, ...)` if routing_config available
- Walks `routing_config["model_routing"]` (from DB via API)
- For each route, checks:
  1. `_route_viable()` — enabled in DB, has required caps, model not banned, quota OK
  2. `_is_available_cached()` — harness responds to health check
- Collects viable routes as `(agent, model, tier)` tuples
- Groups by `CostTier`, picks cheapest tier
- `random.choice(tier_candidates)` — distributes among routes in that tier
- Applies `_maybe_upgrade_model()` for high complexity
- Reasoning: `"Routed to {agent}/{model} (LOW tier, chosen from N viable routes)"`

### Phase 3 Fallback: Config-based routing

- Calls `_route_task_config(...)` if routing_config unavailable (API down, local mode)
- Uses `self.config.model_routing` from .odin/config.yaml
- Same tier distribution logic as API path
- Reasoning includes "(config fallback)" suffix

Error path: `RuntimeError` if no viable routes found (lists all tried routes in message)

---

## 7. Viability Check

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_route_viable()` (line ~TBD)
**Called by**: `_route_task_api()`, `_route_task_config()`, `_try_routes_for_agent()`

Checks (in order, short-circuits on first failure):
1. Agent exists in routing_config (API path) or config.agents (fallback)
2. Agent enabled (from DB or config)
3. All `required_caps` present in agent's `capabilities` list
4. Model not on `banned_models` (substring match via `_is_banned()`)
5. Quota: if `usage_pct > quota_threshold` AND complexity is NOT "high" → reject

Side effects: None (pure check)

---

## 8. Premium Model Upgrade

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_maybe_upgrade_model()` (line ~TBD)
**Called by**: `_route_task()` Phase 2 and Phase 3

Key logic:
- Only activates when `complexity == "high"`
- Reads `premium_model` from routing_config agent metadata (API path) or config (fallback)
- Skips if premium_model equals current model (no-op upgrade)
- Skips if premium_model is on the ban list
- Returns `(premium_model, ", upgraded to premium for high complexity")` or `(model, "")`

---

## 9. Board Creation & Agent Membership

**File**: `taskit/taskit-backend/boards/views.py`
**Endpoint**: `POST /boards/`
**Function**: `BoardViewSet.create()` (line ~TBD)

Key logic:
- Creates Board record
- Calls `board.create_memberships()` → queries all agent Users where `is_agent=True` and `profile.enabled=True`
- Creates BoardMembership for each enabled agent (is_active=True by default, preferred_model=None uses agent default)
- No config.yaml read — all agent metadata from DB

---

## 10. Agent/Model Toggle (DB-backed)

**File**: `taskit/taskit-backend/boards/views.py`
**Endpoint**: `PATCH /boards/{id}/memberships/{membership_id}/`
**Function**: `BoardMembershipViewSet.partial_update()` (line ~TBD)

Key logic:
- Updates BoardMembership.is_active (agent on/off for this board)
- Updates BoardMembership.preferred_model (override agent default)
- Changes immediately visible via `GET /boards/{id}/routing-config/`
- No config.yaml writes — state lives in DB only

---

## 11. Frontend — Task Routing Reasoning

**File**: `taskit/taskit-frontend/src/components/TaskDetailModal.tsx`
**Location**: Inside "Execution Context" `CollapsibleSection`

Key logic:
- Conditionally renders `CompactRow` labeled "Routing" if `task.metadata?.routing_reasoning` exists
- Shows what the orchestrator decided and why
- If `task.metadata?.suggested_model` exists, shows separate row: "Suggested Model: {value}"
- Icon: `GitBranch`
- Text style: `text-[10px] font-mono text-muted-foreground/80 leading-snug`

Data in: `task.metadata.routing_reasoning`, `task.metadata.suggested_model`

---

## 12. Frontend — Spec Routing Config

**File**: `taskit/taskit-frontend/src/components/SpecDetailView.tsx`

### Routing Config Section

Key logic:
- Fetches spec.board → GET `/boards/{id}/routing-config/`
- Collapsible `Card` with `Route` icon
- Iterates `routing_config.model_routing` (array of `{agent, model}`)
- For each route, looks up `routing_config.agents` for tier badge
- Displays numbered list: `1. qwen / qwen3-coder [LOW]`

Data in: `routing_config` from board API (not spec.metadata)

### Model Badge on Task Cards

Key logic:
- On each task card, shows `agent/model` badge
- Format: `{assignees[0]}/{selected_model}` (e.g., "qwen/qwen3-coder")
- Style: `text-[9px] font-mono text-muted-foreground/60 ml-auto`
- Only renders if `task.metadata?.selected_model` exists

Data in: `task.assignees[0]`, `task.metadata.selected_model`

---

## 13. Frontend — Board Settings (Agent/Model Toggle)

**File**: `taskit/taskit-frontend/src/components/BoardSettingsModal.tsx`

Key logic:
- Fetches board memberships via GET `/boards/{id}/memberships/`
- Renders toggle per agent: `is_active` (enable/disable agent for this board)
- Renders model dropdown: `preferred_model` (override agent default)
- On change, PATCHes `/boards/{id}/memberships/{membership_id}/`
- Changes immediately propagate to next `GET /boards/{id}/routing-config/` call

Data in: BoardMembership list
Data out: PATCH requests to update is_active or preferred_model
