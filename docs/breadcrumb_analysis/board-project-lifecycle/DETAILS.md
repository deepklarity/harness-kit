# Board → Project Lifecycle — Detailed Trace

## 1. odin init (CLI entry point)

**File**: `odin/src/odin/cli.py` :: `init()` (line 180)
**Args**: `--force`, `--board-id <int>`, `--base-url <str>`
**Working directory**: CWD where command is run

Steps in order:

1. **Config creation** (lines 196-226)
   - If `.odin/config.yaml` exists and no `--force`: skip with warning
   - Otherwise: copy `config/config.sample.yaml` → `.odin/config.yaml`
   - Fallback: create minimal config if sample not found

2. **Board/URL overlay** (lines 228-247)
   - Only runs if `--board-id` or `--base-url` provided
   - Loads existing config.yaml, merges values into both root and `taskit` section
   - Works even if config already existed (no --force needed)

3. **Board registry** (lines 249-253)
   - If `--board-id` provided: registers `{board_id: path}` in `~/.odin/boards.json`
   - Enables `odin logs -b <id>` from any directory

4. **Subdirectories** (lines 255-259)
   - Creates `.odin/tasks/`, `.odin/logs/`, `.odin/specs/`

5. **Git repo initialization** (lines 261-288)
   - Only runs if `.git/` doesn't exist
   - `git init` → creates `.gitignore` → `git add -A` → `git commit -m "Initial commit (odin init)"`
   - `.gitignore` excludes: `.odin/worktrees/`, `.odin/locks/`, `.odin/logs/`, `.odin/costs/`, `.env`
   - Best-effort: failure logs warning, continues without worktree support

6. **MCP configs** (lines 290-300)
   - Generates per-CLI config files for 5 agent CLIs (qwen retired in #102)
   - Reads `cfg.mcps` to determine which MCP servers to include
   - Generates `.claude/settings.local.json` for Claude Code permissions

7. **Auth template** (lines 302-314)
   - Creates `.env.example` with `ODIN_ADMIN_USER`, `ODIN_ADMIN_PASSWORD`, `ODIN_FIREBASE_API_KEY`

---

## 2. Board Model (Backend)

**File**: `taskit/taskit-backend/tasks/models.py`
**Fields**: `id` (auto PK), `name` (CharField 255), `description` (TextField), `is_trial` (BooleanField), `created_at`, `updated_at`
**Relations**: `memberships` (FK from BoardMembership), `tasks` (FK from Task), `specs` (FK from Spec)

No `working_dir` field on the model. Project directory association is only in `.odin/config.yaml` and `~/.odin/boards.json`.

---

## 3. Board API (Backend)

**File**: `taskit/taskit-backend/tasks/views.py` :: `BoardViewSet`
**Serializers**: `taskit/taskit-backend/tasks/serializers.py`

Endpoints:
- `GET /api/boards/` — list (paginated, sortable, searchable)
- `POST /api/boards/` — create (name required, description optional)
- `GET /api/boards/{id}/` — detail (includes tasks via BoardDetailSerializer)
- `PATCH /api/boards/{id}/` — partial update
- `DELETE /api/boards/{id}/` — cascade delete
- `POST /api/boards/{id}/members/add/` — bulk add members
- `POST /api/boards/{id}/members/remove/` — bulk remove + unassign tasks
- `POST /api/boards/{id}/clear/` — delete all tasks and specs on board

---

## 4. Board Creation (UI)

**File**: `taskit/taskit-frontend/src/components/CreateBoardModal.tsx`
**Trigger**: "+ Board" button in AppHeader
**Fields**: name (required), description (optional)
**Handler**: `App.tsx :: handleCreateBoard()` → `service.createBoard(name, description)` → refresh

Post-creation: full shell data refresh, user stays on "All Boards" view. No auto-select of new board.

---

## 5. Board Selection (UI)

**File**: `taskit/taskit-frontend/src/components/AppHeader.tsx`
**Constant**: `ALL_BOARDS_ID = '__ALL__'`

- Select dropdown: "All Boards" (always first) + separator + board list
- State: `App.tsx :: selectedBoard = searchParams.get('board') || ALL_BOARDS_ID`
- All page components receive `boardFilter` for API-level filtering

---

## 6. Config Loading (odin startup)

**File**: `odin/src/odin/config.py` :: `load_config()`

Search order:
1. Explicit `--config` path
2. `.odin/config.yaml` (project-local, CWD)
3. `~/.odin/config.yaml` (global)
4. Built-in defaults (no config file)

Key behavior: When loading from YAML, agents not mentioned get full built-in defaults injected. A minimal config with just `taskit` settings still has all 6 agents available.

`load_dotenv(CWD/.env)` runs automatically on config load.

---

## 7. Worktree Manager Initialization (orchestrator)

**File**: `odin/src/odin/orchestrator.py` (lines 161-172)

```python
self._worktree = None
if self.config.worktree_enabled:
    project_root = Path(self.config.task_storage).resolve().parent.parent
    self._worktree = WorktreeManager(project_root, worktree_dir=self.config.worktree_dir)
```

- `worktree_enabled` defaults to `True` in `OdinConfig`
- `project_root` derived from `.odin/tasks` → up 2 levels → CWD
- WorktreeManager init succeeds even without `.git/` — failure comes at `create_spec_branch()` time
- Exception caught: worktree silently disabled if init fails

---

## 8. Spec Branch Creation (plan time)

**File**: `odin/src/odin/orchestrator.py` (lines 357-367)

After spec archive saved, before task creation:
```python
if self._worktree:
    branch = self._worktree.create_spec_branch(sid, base_branch=self.config.base_branch)
    spec_archive.metadata["branch"] = branch
    self._save_spec(spec_archive)
```

Best-effort: exception caught, plan continues without worktree isolation.
If no `.git/` exists, `create_spec_branch()` fails → no `metadata.branch` → UI shows `—` for branch.

---

## 9. Working Directory Resolution (execution time)

**File**: `taskit/taskit-backend/tasks/execution/local.py`

Three-tier fallback:
1. `task.metadata.working_dir` (set by worktree creation or manually)
2. `spec.metadata.working_dir` (set by `odin plan` to CWD)
3. `settings.ODIN_WORKING_DIR` (env var)

No board-level working directory exists in the current model.

---

## 10. Dual Instance (dev vs stable)

**File**: `docs/solutions/dual-instance-setup.md`

When dogfooding (harness-kit develops itself):
- `odin` binary → stable (pipx frozen copy)
- `odin-dev` binary → dev (pipx editable install with `--suffix='-dev'`)
- Dev instance must always use `odin-dev` for all commands including `init`
- Mistake signal: traceback path shows `pipx/venvs/odin/` (stable) vs dev checkout path
- Config `taskit.base_url` differentiates: port 9100 (stable) vs 9101 (dev)
