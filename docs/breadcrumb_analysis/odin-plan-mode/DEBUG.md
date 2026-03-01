# odin plan mode — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| CLI | terminal stdout/stderr | Plan table, streaming LLM output, error messages |
| Orchestrator | `.odin/logs/run_*.jsonl` | Structured events: plan_started, task_assigned, plan_completed |
| Interactive | `.odin/logs/interactive_plan_<session_id>.log` | Conversation transcript (debugging only, not data source) |
| Plan JSON | `.odin/plans/plan_<spec_id>.json` | The plan — written by the LLM agent directly |
| Spec archive | `.odin/specs/<spec_id>.json` | Spec metadata + content |
| Backend | `taskit/taskit-backend/logs/taskit.log` | Task creation API calls (if TaskIt backend) |

## What to search for

| Symptom | Where to look | Search term |
|---------|---------------|-------------|
| Plan file not created | `.odin/plans/` | Check if `plan_<spec_id>.json` exists — agent didn't write it |
| Plan file exists but empty/invalid | `.odin/plans/plan_<spec_id>.json` | Open it — malformed JSON or empty array |
| Plan produces 0 tasks | `.odin/logs/run_*.jsonl` | `"plan_completed"` — check `task_count` |
| Agent not found | terminal stderr | `"Base agent '...' not found in config"` |
| tmux not available | terminal stderr | `"tmux is required for interactive planning"` |
| Agent doesn't support interactive | terminal stderr | `"does not support interactive mode"` |
| Wrong agent assigned | `.odin/plans/plan_<spec_id>.json` | Check `suggested_agent` vs what `_route_task` selected |
| Dependency resolution failure | `.odin/logs/run_*.jsonl` | `"dep_warning"` — symbolic ID not in map |
| Quota affecting routing | `.odin/plans/plan_<spec_id>.json` | Check `quota_snapshot` in task metadata |
| Quick mode not applied | conversation transcript | Check if agent explored files despite `--quick` |
| Auto-queue not happening | terminal | `--quick` must be set AND `board_backend == "taskit"` |

## Quick commands

```bash
# Check what plans exist
ls -la .odin/plans/

# Read a plan's tasks
cat .odin/plans/plan_<spec_id>.json | python -m json.tool

# Check spec archive
cat .odin/specs/<spec_id>.json | python -m json.tool

# Check structured log events for a plan run
grep "plan_" .odin/logs/run_*.jsonl | python -m json.tool

# Check what agents are configured
python -c "from odin.config import load_config; c = load_config(); print([a for a in c.agents])"

# Check if an agent supports interactive mode
python -c "
from odin.config import load_config
from odin.harnesses import get_harness
c = load_config()
h = get_harness('claude', c.agents['claude'])
print(h.build_interactive_command('/dev/null', {}))
"

# Inspect tasks created by a spec (requires TaskIt backend)
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --brief

# Full task details for a specific task
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id>

# Check quota data (what the planner sees)
python -c "
import asyncio
from odin.config import load_config
from odin.orchestrator import Orchestrator
o = Orchestrator(load_config())
print(asyncio.run(o._fetch_quota()))
"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_ADMIN_USER` | Auth user for TaskIt backend API | None (no auth) |
| `ODIN_ADMIN_PASSWORD` | Auth password for TaskIt backend | None (no auth) |
| `ODIN_FIREBASE_API_KEY` | Firebase API key for TaskIt auth | None |

These are loaded from `.env` in the working directory via `load_dotenv()`.

## Config that affects this flow

Config file: `.odin/config.yaml` (project) or `~/.odin/config.yaml` (global)

| Config key | Effect |
|------------|--------|
| `base_agent` | Which agent does planning (default: `claude`) |
| `agents.<name>.enabled` | Whether agent is available for routing |
| `agents.<name>.capabilities` | What the agent can do (matched against `required_capabilities`) |
| `model_routing` | Priority-ordered list of (agent, model, complexity_range) routes |
| `quota_threshold` | Usage percentage above which agent is deprioritized (default: 80) |
| `board_backend` | `local` or `taskit` — affects auto-queue behavior with `--quick` |
| `log_dir` | Where interactive session logs go (default: `.odin/logs`) |
| `banned_models` | Models excluded from routing |

## Common breakpoints

- `cli.py:359` — Three-way UX branch (quiet/auto/interactive).
- `orchestrator.py` :: `plan()` — Right before harness dispatch. Inspect `spec`, `quota`, `plan_path`.
- `orchestrator.py` :: `_build_plan_prompt()` — Print to see exactly what the agent receives.
- `orchestrator.py` :: after harness returns — Check if `plan_path` exists on disk.
- `orchestrator.py:265` — Task routing. Check `agent_name` and `selected_model` from `_route_task()`.

## Known gotchas

1. **Cannot run from inside Claude Code** — `odin plan` invokes `claude -p` as subprocess. Nested Claude Code sessions fail silently. Always test from a regular terminal.
2. **Quick mode is an LLM instruction, not enforcement** — the agent is told not to explore, but nothing prevents it if the harness provides tools. Effectiveness depends on the agent following instructions.
3. **Agent must write the file** — if the agent doesn't write plan JSON to the given path, odin gets a clean "file not found" error. This is explicit and debuggable, unlike the old terminal truncation which was silent.
4. **Symbolic ID mismatch** — if the LLM outputs `"depends_on": ["task_3"]` but no task has `"id": "task_3"`, the dependency is silently dropped with a warning log. Check `dep_warning` events.
5. **Auto-queue only with --quick + taskit** — `--auto` alone does NOT auto-queue tasks. Requires BOTH `--quick` flag AND `board_backend == "taskit"`.
