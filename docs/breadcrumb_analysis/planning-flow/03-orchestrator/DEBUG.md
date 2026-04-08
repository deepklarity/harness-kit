# Orchestrator — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| CLI | terminal stdout/stderr | Plan table, streaming LLM output, error messages |
| Orchestrator | `.odin/logs/run_*.jsonl` | Structured events: plan_started, task_assigned, plan_completed |
| Interactive (tmux) | `.odin/logs/interactive_plan_<session_id>.log` | Conversation transcript (debugging only) |
| Plan JSON | `.odin/plans/plan_<spec_id>.json` | The plan — written by the LLM agent directly |
| Spec archive | `.odin/specs/<spec_id>.json` | Spec metadata + content |
| Planning agent trace | **NOT CAPTURED** (gap) | Would be at `.odin/logs/plan_<spec_id>.trace.jsonl` |
| Django | `taskit/taskit-backend/logs/taskit.log` | Task creation API calls (if TaskIt backend) |
| Django detail | `taskit/taskit-backend/logs/taskit_detail.log` | Verbose request/response logging |

## What to search for

| Symptom | Where to look | Search term / action |
|---------|--------------|---------------------|
| Plan file not created | `.odin/plans/` | Check if `plan_<spec_id>.json` exists — agent didn't write it |
| Plan file exists but empty/invalid | `.odin/plans/plan_<spec_id>.json` | `python -m json.tool .odin/plans/plan_sp_<id>.json` |
| Plan produces 0 tasks | `.odin/logs/run_*.jsonl` | `"plan_completed"` — check `task_count` |
| Agent not found | terminal stderr | `"Base agent '...' not found in config"` |
| tmux not available | terminal stderr | `"tmux is required for interactive planning"` |
| Agent doesn't support interactive | terminal stderr | `"does not support interactive mode"` |
| Wrong agent assigned | `.odin/plans/plan_<spec_id>.json` | Check `suggested_agent` vs what `_route_task` selected |
| Dependency resolution failure | `.odin/logs/run_*.jsonl` | `"dep_warning"` — symbolic ID not in map |
| Quota affecting routing | `.odin/plans/plan_<spec_id>.json` | Check `quota_snapshot` in task metadata |
| Quick mode not applied | conversation transcript | Check if agent explored files despite `--quick` |
| Auto-queue not happening | terminal | `--quick` must be set AND `board_backend == "taskit"` |
| Tasks created but wrong agent | `.odin/logs/run_<ts>.jsonl` | Search `"action":"task_assigned"` — shows routing per task |
| Dependencies missing | `.odin/plans/plan_sp_<id>.json` | Check `depends_on` in raw plan JSON |
| Spec not visible in UI | `taskit/taskit-backend/logs/taskit.log` | Search `POST /api/specs/` |
| Planning agent output lost | **Known gap** | `_decompose()` doesn't capture trace |

## Quick commands

```bash
# Check what plans exist
ls -la .odin/plans/

# Read a plan's tasks
python -m json.tool .odin/plans/plan_<spec_id>.json

# Check spec archive
python -m json.tool .odin/specs/<spec_id>.json

# Check structured log events for a plan run
grep "plan_" .odin/logs/run_*.jsonl | python -m json.tool

# See which agents got assigned
python -c "
import json
for line in open('.odin/logs/run_latest.jsonl'):
    e = json.loads(line)
    if e.get('action') == 'task_assigned':
        md = e.get('metadata', {})
        print(f\"{e.get('task_id', '?')}: {e.get('agent', '?')} — {md.get('title', '?')}\")
"

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

# Inspect tasks created by a spec (TaskIt backend)
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --brief

# Full task details
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id>

# Inspect task metadata (routing decisions)
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic,metadata

# Check quota data
python -c "
import asyncio
from odin.config import load_config
from odin.orchestrator import Orchestrator
o = Orchestrator(load_config())
print(asyncio.run(o._fetch_quota()))
"

# Check agent availability
python -c "
from odin.harnesses.registry import get_all_harnesses
from odin.config import load_config
cfg = load_config()
for name, h in get_all_harnesses(cfg.agents).items():
    print(f'{name}: available={h.is_available()}')
"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_ADMIN_USER` | Auth user for TaskIt backend API | None (no auth) |
| `ODIN_ADMIN_PASSWORD` | Auth password for TaskIt backend | None |
| `ODIN_FIREBASE_API_KEY` | Firebase API key for TaskIt auth | None |
| `ODIN_BASE_AGENT` | Override planning agent | First available from config |
| `TASKIT_URL` | TaskIt backend base URL | `http://localhost:8000` |

## Config keys

| Config key | Effect |
|------------|--------|
| `base_agent` | Which agent does planning (default: `claude`) |
| `base_model` | Which model the planning agent uses |
| `agents.<name>.enabled` | Whether agent is available for routing |
| `agents.<name>.capabilities` | Matched against `required_capabilities` |
| `model_routing` | Priority-ordered list of (agent, model, complexity_range) routes |
| `quota_threshold` | Usage % above which agent is deprioritized (default: 80) |
| `board_backend` | `local` or `taskit` — affects auto-queue with `--quick` |
| `log_dir` | Where session logs go (default: `.odin/logs`) |
| `banned_models` | Models excluded from routing |

## Common breakpoints

- `cli.py` :: `plan()` — Three-way UX branch (quiet/auto/interactive + direct flag)
- `orchestrator.py` :: `plan()` — Right before harness dispatch. Inspect `spec`, `quota`, `plan_path`.
- `orchestrator.py` :: `_build_plan_prompt()` — Print to see what agent receives.
- `orchestrator.py` :: after harness returns — Check if `plan_path` exists on disk.
- `orchestrator.py` :: `_create_tasks_from_plan()` — Check routing per task.
- `orchestrator.py` :: `_decompose()` — THE GAP: context dict has no output_file/trace_file.
- `interactive.py` :: `_run_direct()` — Direct mode subprocess. Check `cmd_str` before bash execution.

## Known gotchas

1. **Cannot run from inside Claude Code** — `odin plan` invokes `claude -p` as subprocess. Nested sessions fail.
2. **Quick mode is an LLM instruction, not enforcement** — nothing prevents agent from exploring if harness provides tools.
3. **Agent must write the file** — if agent doesn't write plan JSON to plan_path, clean "file not found" error.
4. **Symbolic ID mismatch** — `"depends_on": ["task_3"]` but no `"id": "task_3"` → dependency silently dropped. Check `dep_warning` events.
5. **Auto-queue needs --quick + taskit** — `--auto` alone does NOT auto-queue. Requires BOTH flags.
