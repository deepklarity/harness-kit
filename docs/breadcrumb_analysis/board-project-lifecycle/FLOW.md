# Board → Project Lifecycle

Trigger: User creates a board (UI) or runs `odin init` (CLI) in a project directory
End state: Board linked to project directory, git repo initialized, worktree isolation ready, specs can be planned and executed

## Flow 1: CLI-first (primary path)

```
User runs: odin init [--board-id 42 --base-url http://localhost:9101]
  cli.py :: init()
    → creates .odin/ directory (config.yaml from sample template)
    → if --board-id/--base-url: overlays into config.yaml (both root and taskit section)
    → if --board-id: registers in ~/.odin/boards.json (global board registry)
    → creates subdirs: .odin/tasks/, .odin/logs/, .odin/specs/
    → if no .git/: git init + .gitignore + initial commit (NEW)
    → generates MCP configs for 5 agent CLIs (.mcp.json, .gemini/, .codex/, .kilocode/, opencode.json; qwen retired in #102)
    → generates .claude/settings.local.json (Claude Code permissions)
    → generates .env.example (auth template)

User runs: odin plan <spec-file>
  orchestrator.py :: plan()
    → reads board_id from config.yaml
    → creates spec archive in .odin/specs/ with metadata.working_dir = CWD
    → if worktree_enabled + git repo: creates spec/<spec_id> branch, stores in spec.metadata.branch
    → LLM decomposes spec into task DAG
    → tasks created on board with depends_on relationships
```

## Flow 2: UI-first (secondary, incomplete)

```
User clicks "+ Board" in AppHeader.tsx
  → CreateBoardModal.tsx (name + description only)
  → POST /api/boards/ {name, description}
  → Board saved to DB — no working directory, no odin init, no git repo
  → User must manually: odin init --board-id <id> in a project directory
```

## Flow 3: Existing directory (re-init / new board on existing project)

```
User has project with existing .git/ and code
  → odin init --board-id <new_id> --base-url http://localhost:9101
  → config.yaml created (or skipped if exists — use --force to overwrite)
  → board_id/base_url overlaid even if config exists
  → git repo already exists — skipped
  → MCP configs regenerated
```

## Working Directory Resolution (execution time)

```
dag_executor.py / orchestrator.py :: exec_task()
  → working_dir = task.metadata.working_dir         # priority 1
  → if not: spec.metadata.working_dir               # priority 2
  → if not: settings.ODIN_WORKING_DIR               # priority 3 (env var)
  → if not: None (execution fails or uses process CWD)
```

## Worktree Integration (plan time → execution time)

```
odin plan:
  → spec.metadata.branch = "spec/<spec_id>" (if git repo exists)

dag_executor.py :: poll_and_execute():
  → if spec has branch: creates task worktree at .odin/worktrees/<spec_id>/<task_id>
  → task.metadata.working_dir = worktree path
  → task.metadata.branch = "task/<spec_id>/<task_id>"
  → task.metadata.merge_status = "pending"

On task completion:
  → auto-merge task branch into spec branch
  → task.metadata.merge_status = "merged" | "conflict"
```

## Key invariants

- `odin init` is idempotent: re-running skips existing config (unless --force), skips existing git repo
- board_id overlay works even when config exists (no --force needed for just changing board)
- Git repo must exist for worktree isolation — `odin init` creates it, `odin plan` does not
- Spec branch creation is best-effort: fails silently if no git repo, plan continues without worktree isolation

## See also

- `git-worktree-isolation/` — worktree branch model, merge serialization, conflict handling
- `planning-flow/03-orchestrator/` — planning dispatch, LLM decomposition, task creation
