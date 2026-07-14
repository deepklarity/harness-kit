# Board Agent Roster — who the planner is allowed to use

Why this exists: a freshly scaffolded board (e.g. `odin new-project`) has
only the base agent enabled, so every planned task lands on claude and the
operator asks "why weren't the others used?". The roster, not the project's
`.odin/config.yaml`, decides what the planner sees.

## The model

"Agent enabled on board X" = a `BoardMembership` row linking the board to
the agent's `User` record (`role="AGENT"`, email `{agent}@odin.agent`).
No row → the agent exists in the system but is invisible to that board.

Per-model disable is a list on the membership: `BoardMembership.disabled_models`.

Retired agents (qwen, gemini as of task #135) keep their `User` rows for FK
integrity but have `is_active=False` — they can't be toggled onto any board.

## How it reaches planning

```
GET /api/boards/{id}/agents/            tasks/views.py :: agents()
  → all active AGENT users, enabled = membership exists
odin plan → orchestrator fetches routing_config from that endpoint
  → _build_available_agents() keeps only enabled + is_available() harnesses
  → "Available agents: [...]" block in the planner's system prompt
```

The planner literally cannot suggest an agent that isn't in that block.
The router's tier distribution ("spread across cheapest viable tier") only
kicks in when the roster has more than one entry.

## Toggling

```bash
# Enable / disable one agent on a board (the ONLY supported write path —
# it also unassigns the agent's tasks on disable; don't poke BoardMembership raw)
curl -X PATCH http://localhost:9100/api/boards/{board_id}/agents/{name}/ \
  -H 'Content-Type: application/json' -d '{"enabled": true}'

# Current roster
curl -s http://localhost:9100/api/boards/{board_id}/agents/
```

UI: board settings page uses the same endpoints. Making new-board defaults
configurable from the UI is a fable-roadmap backlog item.

## Gotchas

- Disabling an agent **unassigns all its tasks on that board** (history
  entries by `system@taskit`), then deletes the membership. Not symmetric
  with enable.
- The project's `.odin/config.yaml` `agents:` section controls how a
  harness *runs* (sandbox, CLI, keys) — it does not grant board presence.
  Both are needed: config entry + membership + `is_available()` (CLI on
  PATH, auth present).
- 404 on toggle means the agent is retired (`is_active=False`) or the name
  is wrong; it is not created on the fly.
