# Board → Project Lifecycle

Trigger: User creates a board (UI) or configures `board_id` in `.odin/config.yaml` (CLI)
End state: Board has specs, tasks execute against a working directory, tasks reach DONE

## Current Flow

### Board Creation

**Path A: CLI-first (current primary path)**

```
User runs: odin init (in project directory)
  → creates .odin/ directory structure (config.yaml, tasks/, logs/, specs/, MCP configs)
  → user manually sets taskit.board_id in .odin/config.yaml
  → user manually creates board via UI (or it was pre-existing)
  → board_id is a loose reference — no validation that it exists

User runs: odin plan <spec-file>
  → orchestrator.py :: plan_spec()
    → reads board_id from config.yaml
    → archives spec to TaskIt backend with board_id
    → creates tasks on that board
```

**Path B: UI-first (secondary, incomplete)**

```
User clicks "+ Board" in AppHeader.tsx
  → opens CreateBoardModal.tsx (name + description only)
  → POST /api/boards/ {name, description}
  → tasks/views.py :: BoardViewSet.create()
    → saves Board(name, description) to DB
    → returns board with id
  → App.tsx :: handleCreateBoard()
    → triggers loadShellData() refresh
    → user stays on "All Boards" view (no auto-select)
  → Board exists but has no working directory, no odin init, no connection to a project
```

### Board Selection (UI)

```
AppHeader.tsx :: board selector dropdown
  → first option always: "All Boards" (value: "__ALL__")
  → separator
  → boards.map(board => board.name)
  → onBoardChange → updates URL ?board=<id>

App.tsx :: selectedBoard
  → searchParams.get('board') || ALL_BOARDS_ID
  → boardFilter = selectedBoard === ALL_BOARDS_ID ? undefined : selectedBoard
  → all page components receive boardFilter for API filtering
```

### Spec Creation → Task Execution

```
odin plan <spec-file> (from CLI, in project directory)
  → orchestrator.py :: plan_spec()
    → spec archived to backend (board_id from config.yaml)
    → LLM decomposes spec into task DAG
    → tasks created on board with depends_on relationships
    → each task gets: assigned_agent, complexity, routing_reasoning

odin exec <task_id> (or DAG executor polls automatically)
  → orchestrator.py :: exec_task() / dag_executor.py :: poll_and_execute()
    → resolves working_dir: task.metadata > spec.metadata > ODIN_WORKING_DIR env var
    → checks dependencies (READY/WAITING/BLOCKED)
    → spawns: odin exec <task_id> with cwd=working_dir
    → agent runs, produces output
    → task transitions: IN_PROGRESS → EXECUTING → REVIEW → DONE
```

### Working Directory Resolution (current — scattered)

```
execution/local.py :: trigger()
  → working_dir = task.metadata.get("working_dir")         # priority 1
  → if not: spec.metadata.get("working_dir")               # priority 2
  → if not: settings.ODIN_WORKING_DIR                      # priority 3 (env var)
  → if not: None (execution will fail or use process CWD)
```

## Key Problems with Current Flow

1. Board has no working directory — project location is fragmented across task/spec metadata and env vars
2. odin init is CLI-only — no way to initialize a project from the UI
3. "All Boards" is the default — mixes unrelated projects together
4. Board creation doesn't connect to any project — it's just a name/description
5. No validation that odin is initialized in the working directory
6. Fresh install shows empty "All Boards" with no guidance to create a board
7. board_id in .odin/config.yaml is a loose reference — no bidirectional link
