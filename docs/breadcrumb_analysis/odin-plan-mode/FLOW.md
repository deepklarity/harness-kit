# odin plan mode

Trigger: `odin plan <spec_file>` or `odin plan --prompt "..."` from terminal
End state: Spec archive created, plan JSON on disk, tasks created on board with suggested agent assignments

## Flags

| Flag | Effect |
|------|--------|
| `--auto` | Skip interactive tmux, one-shot with streaming output |
| `--quiet` | Implies `--auto`, shows spinner instead of streaming |
| `--quick` | Instruct LLM to skip codebase exploration |
| `--base-agent` | Override which agent does planning (e.g. `codex`) |

## Flow

cli.py :: OdinCLI.plan()
  -> validate input (spec_file xor prompt), load spec text
  -> create Orchestrator
  -> create spec archive FIRST (get spec_id, save to .odin/specs/ and TaskIt if configured)
  -> fetch quota via _fetch_quota() (graceful: {} on failure)
  -> build unified plan prompt via _build_plan_prompt()
     -> available agents with capabilities, cost_tier, quota data
     -> routing priority
     -> schema + dependency + artifact coordination rules
     -> plan_path = .odin/plans/plan_<spec_id>.json
     -> if quick: "Do NOT explore or read the codebase"
     -> instruction: "Write your final plan JSON to <plan_path>"

  [interactive: default]
  -> launch tmux session, user chats with agent
  -> agent writes plan JSON to plan_path on disk
  -> session ends

  [auto: --auto]
  -> streaming subprocess, agent writes plan JSON to plan_path on disk
  -> stream chunks to stdout for visibility

  [quiet: --quiet]
  -> subprocess with spinner, agent writes plan JSON to plan_path on disk

  -> read plan_path (file exists or clean error)
  -> parse JSON array
  -> _create_tasks_from_plan(sub_tasks, spec_id, quota)
     -> Pass 1: for each sub-task, _route_task() -> create_task() -> assign_task()
     -> Pass 2: resolve symbolic depends_on (task_1 -> real UUID)
  -> print task table

  [--quick + taskit backend]
  -> auto-move assigned tasks to IN_PROGRESS for DAG executor

  [otherwise]
  -> print guidance: odin assign / odin exec

## Key properties

- One LLM call per plan. No fallback second call.
- Structured data never flows through the terminal. Agent writes JSON to disk.
- Spec archive exists before the LLM runs. Agent knows the spec_id and plan_path.
- One prompt builder. All modes get the same rules, schema, and quota context.
- Modes only differ in UX wrapper (tmux vs streaming vs spinner), not in intelligence.

## Data shape: sub-task (LLM output)

```json
{
  "id": "task_1",
  "title": "short title",
  "description": "self-contained prompt for executing agent",
  "required_capabilities": ["code"],
  "suggested_agent": "claude",
  "complexity": "low | medium | high",
  "depends_on": ["task_0"],
  "expected_outputs": ["file.py"],
  "assumptions": ["some assumption"],
  "reasoning": "why this agent"
}
```

## Agent/model routing

orchestrator.py :: _route_task()
  1. Honour LLM suggestion: try routes for suggested agent first
  2. Walk priority list: iterate config.model_routing in order
  3. Legacy fallback: _pick_agent() + _pick_model()

Each step checks: capabilities match, agent enabled, model not banned, quota OK, agent available (cached).
