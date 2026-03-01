# Odin - Technical Design Document

## Overview

Odin is a task-board orchestration system that leverages multiple AI agent CLIs to collaboratively complete tasks. It takes a task specification, uses a configurable base agent to decompose it into tasks on a board (like Trello/Asana cards), dispatches those tasks to appropriate agents based on capabilities and cost, and tracks their progress.

Tasks evolve — they get reassigned, their purpose shifts, and comments track history. The single-owner model is correct; the owner just changes over time.

TaskIt is the isolated task-management backend. Odin communicates with it to create, assign, and track tasks — the same way an application talks to Trello's API.

## Agent Harnesses

Six agent harnesses are supported, split into CLI-based and API-based:

### CLI-based (subprocess execution)

These agents are invoked as subprocesses in non-interactive/pipe mode:

| Agent  | Binary  | Non-interactive invocation                        | Cost Tier |
|--------|---------|---------------------------------------------------|-----------|
| Claude | `claude`| `claude -p "prompt" --output-format text`         | high      |
| Codex  | `codex` | `codex exec --skip-git-repo-check "prompt"`       | medium    |
| Gemini | `gemini`| `gemini -p "prompt"`                              | low       |
| Qwen   | `qwen`  | `qwen -p "prompt"`                                | low       |

### API-based (HTTP calls via httpx)

| Agent   | Endpoint                                         | Auth            | Cost Tier |
|---------|--------------------------------------------------|-----------------|-----------|
| MiniMax | `api.minimax.io/v1/text/chatcompletion_v2`       | Bearer API key  | low       |
| GLM     | `open.bigmodel.cn/api/paas/v4/chat/completions`  | Bearer API key  | low       |

### Harness Interface

All harnesses implement `BaseHarness`:

```python
class BaseHarness(ABC):
    async def execute(self, prompt: str, context: dict) -> TaskResult
    async def is_available(self) -> bool
```

New harnesses are added via the `@register_harness("name")` decorator and auto-imported in `registry.py`.

## Orchestration Model

Odin follows a task-board model, not a rigid pipeline:

```
Spec Input → Decompose into tasks → Assign agents → Execute tasks → Done
```

1. **Decompose**: Base agent receives the spec + list of available agents and outputs a JSON array of tasks with `title`, `description`, `required_capabilities`, `suggested_agent`. The planner creates whatever tasks the project needs — implementation, assembly, review, testing, etc.
2. **Assign**: Each task is matched to the cheapest available agent that satisfies the required capabilities. Assignments are suggestive defaults that can be overridden.
3. **Execute**: Up to 4 tasks run concurrently via `asyncio.gather` with a semaphore

There is no hardcoded assembly stage. If a project needs assembly, the planner creates an assembly task on the board — it's just another task assigned to an agent.

## Task Management (TaskIt)

TaskIt is an isolated task-management application. Odin communicates with it but does not own it. Current implementation uses a temporary disk-based system in `.odin/tasks/`:

- `task_{uuid}.json` — individual task files with full state
- `index.json` — quick-lookup index mapping task IDs to titles/statuses
- Tasks support comments (for inter-agent communication) and attachments (file paths)
- Status lifecycle: `pending` → `assigned` → `in_progress` → `completed` / `failed`
- Will be replaced by the TaskIt API when that system is ready

## Structured Logging

Each run produces `.odin/logs/run_{timestamp}.jsonl` with entries:

```json
{"timestamp": "...", "action": "task_assigned", "task_id": "abc123", "agent": "gemini", "metadata": {"title": "..."}}
{"timestamp": "...", "action": "task_completed", "task_id": "abc123", "agent": "gemini", "output": "...", "duration_ms": 14230}
```

Actions: `run_started`, `decompose_started`, `decompose_completed`, `decomposition_complete`, `task_assigned`, `task_started`, `task_completed`, `task_failed`, `run_completed`

## Configuration

Hierarchical YAML config with env var substitution for API keys:

```
Search order: --config flag → .odin/config.yaml (CWD) → ~/.odin/config.yaml → built-in defaults
```

API keys use `${ENV_VAR}` syntax in YAML, resolved at load time.

## Key Design Decisions

1. **Task-board model**: Tasks are first-class entities that evolve — they get created, assigned, reassigned, executed, and accumulate comments. No hardcoded pipeline stages beyond plan and execute.
2. **TaskIt isolation**: TaskIt is an independent task-management system. Odin is a consumer, not the owner. This separation allows TaskIt to be used by other systems.
3. **Cheapest-first routing**: Agents are sorted by cost tier and the cheapest capable agent is selected for each task
4. **CLI subprocess model**: Agent CLIs are invoked as one-shot subprocesses in non-interactive mode, keeping the integration simple and stateless
5. **Decorator-based registry**: Same `@register_harness` pattern as `harness_usage_status` providers, making it trivial to add new agents
6. **Project-local storage**: `.odin/tasks/` and `.odin/logs/` live in the working directory, keeping runs isolated per project

## Dependencies

- `fire` — CLI framework (auto-generates help from docstrings)
- `rich` — Terminal tables, spinners, colored output
- `pydantic>=2` — Data validation and JSON serialization
- `httpx` — Async HTTP client for API-based harnesses
- `pyyaml` — Config file parsing
- `python-dotenv` — `.env` file loading

## Future Work

- Interactive CLI mode (streaming agent output, live progress)
- TaskIt API integration (replace disk-based storage)
- Quota-aware routing via `harness_usage_status` integration
- Agent retry and reassignment on failure
- Learning from past runs (log analysis for optimal agent selection)
