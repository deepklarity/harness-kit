# odin plan mode — Detailed Trace

## 1. CLI Entry Point

**File**: `odin/src/odin/cli.py`
**Function**: `OdinCLI.plan()` (line 296)
**Called by**: Fire CLI dispatch (`odin plan ...`)
**Calls**: `Orchestrator.plan()`

Key logic:
- Input: `spec_file` xor `prompt` required. If both missing, exits with usage.
- Spec file is read to string; inline prompt used as-is.
- `base_agent` override applied to config before creating Orchestrator.
- Three-way UX branch: quiet (spinner) / auto (streaming) / default (tmux).
- All three branches call the same `Orchestrator.plan()` — mode is a UX parameter, not a logic fork.
- Post-plan: `--quick` + TaskIt backend triggers auto-queue — moves assigned tasks to `IN_PROGRESS`.

Data in: `spec_file: str`, `prompt: str`, `auto: bool`, `quiet: bool`, `quick: bool`, `base_agent: str`
Data out: prints task table to console

---

## 2. Orchestrator.plan()

**File**: `odin/src/odin/orchestrator.py`
**Function**: `plan()`
**Called by**: CLI (all modes)
**Calls**: `_save_spec()`, `_fetch_quota()`, `_build_plan_prompt()`, harness execute, `_create_tasks_from_plan()`

Key logic:
- Spec archive created FIRST with `generate_spec_id(title)` — deterministic hash. Saved to `.odin/specs/` and TaskIt backend if configured.
- Quota fetch is async, returns `{agent_name: {"usage_pct": float, "remaining_pct": float}}` or `{}`.
- Derives `plan_path = .odin/plans/plan_<spec_id>.json`.
- Builds unified prompt via `_build_plan_prompt()` with plan_path baked in.
- Dispatches to harness based on mode:
  - Interactive: tmux session (blocking), agent writes plan to plan_path.
  - Auto: `execute_streaming()` with stream callback, agent writes plan to plan_path.
  - Quiet: `execute()` (blocking), agent writes plan to plan_path.
- After LLM completes: reads plan_path, parses JSON, creates tasks.

Data in: `spec: str`, `working_dir: str`, `spec_file: str`, `mode: str`, `quick: bool`
Data out: `(spec_id: str, tasks: List[Task])`

---

## 3. _build_plan_prompt() — Unified Prompt Builder

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_build_plan_prompt()`
**Called by**: `plan()`

Key logic:
- Single function used by all modes. No separate prompt for interactive vs auto.
- Includes:
  - Available agents JSON (capabilities, cost_tier, models, quota data)
  - Routing priority section
  - Quota instruction (avoid agents with usage > threshold%)
  - Quick mode instruction (skip codebase exploration) if applicable
  - Task schema (id, title, description, required_capabilities, suggested_agent, complexity, depends_on, expected_outputs, assumptions, reasoning)
  - Dependency rules (parallel vs sequential, assembly must depend on all inputs)
  - Artifact coordination rules (parallel tasks must agree on filenames)
  - Output instruction: "Write your final plan as a JSON array to: `<plan_path>`"
- Does NOT tell the agent to output JSON in the terminal.

Data in: `spec: str`, `plan_path: str`, `available_agents: list`, `routing_section: str`, `quota: dict`, `quick: bool`
Data out: `str` — the complete prompt

---

## 4. InteractivePlanSession — tmux Path

**File**: `odin/src/odin/interactive.py`
**Function**: `InteractivePlanSession.run()`
**Called by**: `Orchestrator.plan()` (interactive mode)
**Calls**: `harness.build_interactive_command()`, `tmux.launch_and_attach()`

Key logic:
- tmux availability is a hard requirement. Without tmux, raises with install instructions.
- Session ID is random 12-char hex. Conversation logged to `.odin/logs/interactive_plan_<session_id>.log`.
- System prompt is the unified plan prompt from `_build_plan_prompt()`.
- `build_interactive_command()` returns agent-specific CLI invocation. Returns `None` if agent doesn't support interactive → error.
- `launch_and_attach()` uses `script(1)` for conversation capture. BLOCKS until user exits.
- Transcript is for debugging only — plan data is NOT extracted from it.
- After session: plan JSON is on disk at plan_path (or not — clean error).

Data in: harness, prompt, context, working_dir
Data out: nothing (plan is on disk)

---

## 5. _create_tasks_from_plan() — Two-Pass Task Creation

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_create_tasks_from_plan()`
**Called by**: `plan()`

Key logic:
- **Pass 1**: For each sub-task:
  - `_route_task()` selects agent + model based on capabilities, complexity, quota, LLM suggestion.
  - Task metadata assembled: `required_capabilities`, `suggested_agent`, `complexity`, `expected_outputs`, `assumptions`, `selected_model`, `reasoning`, `quota_snapshot`.
  - `create_task()` with `spec_id` tag.
  - `assign_task()` sets suggested agent.
  - If assumptions exist, posts as initial comment via `add_comment()`.
  - Builds `symbolic_to_real` map: `"task_1" -> "a1b2c3d4..."`.

- **Pass 2**: For each sub-task with `depends_on`:
  - Resolves symbolic IDs to real UUIDs via the map.
  - Missing dependencies logged as warning, silently skipped.
  - Updates task with resolved `depends_on` list.

Data in: `sub_tasks: List[Dict]`, `spec_id: str`, `quota: dict`
Data out: `List[Task]` — fully created tasks with real IDs and resolved dependencies

---

## 6. _route_task() — Agent/Model Selection

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_route_task()`
**Called by**: `_create_tasks_from_plan()`

Key logic:
- **Phase 1** — Honour LLM suggestion: If planner suggested an agent, try matching routes for that agent first.
- **Phase 2** — Walk priority list: Iterate `config.model_routing` in configured order. Each route checked for viability: agent enabled, capabilities match, model not banned, quota within threshold.
- **Phase 3** — Fallback: `_pick_agent()` finds cheapest capable available agent. `_pick_model()` selects based on complexity and quota.
- Availability checking is cached per agent per plan run.

Data in: `required_caps: List[str]`, `complexity: str`, `suggested: str`, `quota: dict`
Data out: `(agent_name: str, model: Optional[str])`

---

## 7. Spec Archive

**File**: `odin/src/odin/orchestrator.py`
**Functions**: `_save_spec()`, `generate_spec_id()`

Key logic:
- `generate_spec_id()` creates deterministic ID: `sp_<hash_of_title>`.
- Created BEFORE the LLM call so spec_id is available for plan_path.
- Saved to `.odin/specs/<spec_id>.json` with metadata: `working_dir`, source file.
- If TaskIt backend configured, also posted to backend API (visible in dashboard during planning).

Data: `SpecArchive(id, title, source, content, metadata)`
