# Odin - Multi-Agent Orchestration CLI

Odin turns a spec (markdown or prompt) into a multi-agent plan: it decomposes into tasks, suggests an agent per task, and runs them via `odin plan`, `odin status`/`odin assign`, and `odin exec`. Results live in `.odin/` and can sync to the TaskIt board.

## Prerequisites

- Python 3.10+
- `pip`
- Agent CLIs on `PATH` as needed (see [Harnesses](#harnesses))
- Optional: TaskIt backend for authenticated sync flows
- Optional: `harness_usage_status` for quota-aware assignment

> Commands below are from the monorepo root unless noted.

## Install

Installation mode for now: source install from this monorepo (editable install). A standalone published package flow is not documented yet.

If the venv doesn’t exist, create it; if it does, just activate:

```bash
python3 -m venv ~/.venvs/harness-kit   # omit if already created
source ~/.venvs/harness-kit/bin/activate

# Install the Odin package in editable mode
pip install -e odin/
```

## Initialize a Project

```bash
cd your-project/
odin init
```

> **Note:** Have a `.env` file in the project when you run `odin init`.

## TaskIt auth (optional)

Only needed when the TaskIt backend has auth enabled. Full details: [TaskIt backend README](../taskit/taskit-backend/README_BACKEND.md).

**1. Create admin user** (run from TaskIt backend directory):

```bash
cd taskit/taskit-backend
python manage.py createadmin --email admin@test.com --password test123
```

**2. Return to your Odin project directory** (where you ran `odin init`) and copy env:

```bash
cp .env.example .env
```

In `.env` set:

```dotenv
ODIN_ADMIN_USER=admin@test.com
ODIN_ADMIN_PASSWORD=test123
ODIN_FIREBASE_API_KEY=your_firebase_api_key
```

## Quick Start

```bash
# Staged workflow (recommended)
odin plan sample_specs/poem_spec.md
odin specs
odin status
odin assign <task_id> gemini
odin exec
odin show <task_id>

# Or all-in-one
odin run sample_specs/poem_spec.md
```

**Sample spec** (`sample_specs/poem_spec.md`):

```markdown
# Poem Task

Write a collaborative poem. Each agent writes one paragraph about technology.
Each paragraph must include the agent's name highlighted in HTML: <mark>AgentName</mark>
The final output is poem.html combining all paragraphs.
Use the cheapest available agents.
```

See [CLI Commands Reference](#cli-commands-reference) for all options.

## Quick Verification

Run these after setup to confirm Odin is wired correctly:

```bash
odin -- --help
odin config
odin status
```

## MCP (TaskIt live updates)

Lets agents post status and questions to the board during execution.

Install the MCP extra (installs `taskit-mcp` on `PATH`):

```bash
pip install -e "odin/[mcp]"
```

- `odin init` creates the per-CLI MCP config files.
- Regenerate for a task: `odin mcp_config [task_id]`

Details: [docs/harness.md](docs/harness.md), [docs/mcp.md](docs/mcp.md).

## Configure Agents

Copy the sample config and edit to enable/disable agents and set CLI/API settings:

```bash
mkdir -p .odin
cp config/config.sample.yaml .odin/config.yaml
# Edit .odin/config.yaml
```

Minimal config snippet:

```yaml
base_agent: claude

agents:
  claude:
    enabled: true
    cli_command: claude
    capabilities: [reasoning, planning, coding, writing]
  gemini:
    enabled: true
    cli_command: gemini
    capabilities: [coding, writing, research]
```

See [config/config.sample.yaml](config/config.sample.yaml) for the full template.

## CLI Commands Reference

```bash
# Planning
odin plan <spec_file>
odin plan --prompt "..."
odin specs

# Review
odin status
odin status --spec <spec_id>
odin status --agent <name>
odin status --status <status>
odin show <task_id>
odin assign <task_id> <agent>

# Execution
odin exec
odin exec --fg
odin exec <task_id>
odin exec --spec <spec_id>
odin logs                       # last 50 lines of latest run log
odin logs <task_id>             # run log filtered to task
odin logs debug                 # odin_detail.log (tracebacks)
odin logs -f                    # follow all running tasks
odin logs <task_id> -f          # follow a specific task
odin logs debug -f              # tail odin_detail.log
odin logs -n 100                # control line count
odin logs -b <board_id>         # resolve project via board registry
odin tail [<task_id>]           # (deprecated → odin logs -f)
odin stop [<task_id>]
odin stop --force
odin watch

# Spec management
odin spec show <spec_id>
odin spec abandon <spec_id>

# Utilities
odin run <spec_file>
odin run --prompt "..."
odin tasks
odin config
odin -- --help
```

All `task_id` and `spec_id` support prefix matching.

## Quota and Assignment

- If `harness_usage_status` is available, planning uses live quota data.
- If quota lookup fails, Odin continues with capability/cost/model-based assignment.
- `odin plan` stores assignment reasoning in task metadata.

## Harnesses

| Type | Agent | CLI / Notes |
| :--- | :--- | :--- |
| CLI | Claude | `claude` |
| CLI | Codex | `codex` |
| CLI | Gemini | `gemini` |
| CLI | Qwen | `qwen` |
| API | MiniMax | Requires `MINIMAX_API_KEY` |
| API | GLM | Requires `ZAI_API_KEY` |

Install details: [docs/harness.md](docs/harness.md), [docs/mcp.md](docs/mcp.md).

## Testing

```bash
pip install -e "odin/[dev]"
```

```bash
# Full integration suite (requires gemini, qwen, codex CLIs on PATH)
python -m pytest odin/tests/test_real.py -v

# Incremental runs
python -m pytest odin/tests/test_real.py::TestHarnessAvailability -v
python -m pytest odin/tests/test_real.py::TestSingleHarnessExecute -v
python -m pytest odin/tests/test_real.py::TestDecomposition -v
python -m pytest odin/tests/test_real.py::TestFullPoemE2E -v -s
python -m pytest odin/tests/test_real.py::TestPlanOnly -v -s
python -m pytest odin/tests/test_real.py::TestExecSingleTask -v -s
python -m pytest odin/tests/test_real.py::TestAssembleSeparately -v -s
python -m pytest odin/tests/test_real.py::TestReassign -v -s
python -m pytest odin/tests/test_real.py -k "Spec or MultiSpec or DeriveSpec or ShortTag" -v
```

## Architecture (High Level)

- CLI entrypoint: `src/odin/cli.py`
- Orchestration engine: `src/odin/orchestrator.py`
- Spec archive model: `src/odin/specs.py`
- Harness registry and implementations: `src/odin/harnesses/`
- Task backend: `src/odin/taskit/`
- Structured logs: `.odin/logs/`

## Open Source Metadata

- Contributing guide: [../CONTRIBUTING.md](../CONTRIBUTING.md)
- Security policy: [../SECURITY.md](../SECURITY.md)
- License status: [../README.md#license](../README.md#license)

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `401 Unauthorized` on `odin plan` or `odin exec` | TaskIt auth enabled, credentials missing | Check TaskIt auth mode and `.env` credentials |
| Agent CLI not found | CLI binary not on `PATH` | Install required harness and ensure it's on `PATH` |
| Quota data unavailable | `harness_usage_status` not installed or failing | Planning continues with fallback; see [Quota and Assignment](#quota-and-assignment) |
| Execution mode confusion | Background vs foreground | `odin exec` is background; use `odin exec --fg` for blocking |

## Further Reading

- [docs/specs_and_hierarchy.md](docs/specs_and_hierarchy.md) — Spec format and hierarchy
- [docs/execution.md](docs/execution.md) — Execution model
- [docs/project_understanding.md](docs/project_understanding.md) — Architecture deep dive
- [AGENTS.md](AGENTS.md) — Agent-specific notes
- [config/config.sample.yaml](config/config.sample.yaml) — Full config template
