# Board → Project Lifecycle — Detailed Trace

## 1. Board Model (Backend)

**File**: `taskit/taskit-backend/tasks/models.py` (lines 44-56)
**Fields**: `id` (auto PK), `name` (CharField 255), `description` (TextField), `is_trial` (BooleanField), `created_at`, `updated_at`
**Relations**: `memberships` (FK from BoardMembership), `tasks` (FK from Task), `specs` (FK from Spec)

No `working_dir` field. No project directory association.

---

## 2. Board API (Backend)

**File**: `taskit/taskit-backend/tasks/views.py` (lines 502-612)
**Serializers**: `taskit/taskit-backend/tasks/serializers.py` (lines 138-162)

Endpoints:
- `GET /api/boards/` — list (paginated, sortable, searchable)
- `POST /api/boards/` — create (name required, description optional)
- `GET /api/boards/{id}/` — detail (includes tasks via BoardDetailSerializer)
- `PATCH /api/boards/{id}/` — partial update
- `DELETE /api/boards/{id}/` — cascade delete
- `POST /api/boards/{id}/members/add/` — bulk add members
- `POST /api/boards/{id}/members/remove/` — bulk remove + unassign tasks
- `POST /api/boards/{id}/clear/` — delete all tasks and specs on board

Serializer hierarchy:
- `BoardSerializer` — base: id, name, description, is_trial, created_at, updated_at, member_ids
- `BoardListSerializer` — adds member_count (annotated query)
- `BoardDetailSerializer` — adds tasks (nested TaskSerializer)

---

## 3. Board UI — Selector Dropdown

**File**: `taskit/taskit-frontend/src/components/AppHeader.tsx` (lines 73-104)
**Constant**: `ALL_BOARDS_ID = '__ALL__'` (line 18)

Structure:
- Select component with `value={selectedBoard}` and `onValueChange={onBoardChange}`
- First item: `SelectItem value={ALL_BOARDS_ID}` → "All Boards" (always present)
- Separator (if boards exist)
- Each board: `SelectItem value={board.id}` → `{board.name} ({id substring})`

State management in App.tsx:
- `selectedBoard = searchParams.get('board') || ALL_BOARDS_ID` (line 72)
- `boardFilter = selectedBoard === ALL_BOARDS_ID ? undefined : selectedBoard` (line 73)
- `handleBoardChange` updates URL param `?board=<value>` (line 253)
- All page components receive `boardFilter` for API-level filtering

---

## 4. Board Creation (UI)

**File**: `taskit/taskit-frontend/src/components/CreateBoardModal.tsx`
**Trigger**: "+ Board" button in AppHeader (line 158)
**Fields**: name (required), description (optional)
**Handler**: App.tsx `handleCreateBoard` (lines 309-312) → `service.createBoard(name, description)` → `setRefreshKey(k => k + 1)`

Post-creation: full shell data refresh, but user stays on "All Boards" view. No auto-select of new board.

---

## 5. Fresh Install Behavior

**File**: `taskit/taskit-frontend/src/components/AppHeader.tsx`, `App.tsx`

When `boards = []`:
- Dropdown shows only "All Boards" option
- No separator rendered (line 92 checks `boards.length > 0`)
- Task creation modal has empty board selector → form validation blocks submit
- Overview page renders with zero-value KPI cards
- Settings page shows "No boards found." message
- No prompt or guidance to create a board

---

## 6. odin init (CLI)

**File**: `odin/src/odin/cli.py` (lines 172-296)
**Command**: `odin init [--force]`
**Working directory**: CWD where command is run

Creates:
1. `.odin/config.yaml` — from `config/config.sample.yaml` template
2. `.odin/tasks/`, `.odin/logs/`, `.odin/specs/` directories
3. `.env.example` — auth template (ODIN_ADMIN_USER, ODIN_ADMIN_PASSWORD, ODIN_FIREBASE_API_KEY)
4. MCP config files for each CLI (claude, gemini, qwen, codex, kilo, opencode)
5. `.claude/claude.json` — Claude Code permissions

Config defaults in generated `config.yaml`:
- `base_agent: claude`
- `agents:` with 6 CLI definitions
- `model_routing:` priority list
- `board_backend: taskit` (default)
- `taskit.board_id:` placeholder
- `taskit.base_url:` placeholder
- `taskit.created_by:` placeholder

Key: `odin init` runs in a project directory but does NOT create or link to a board. The user must manually set `taskit.board_id` in config after board exists.

---

## 7. Working Directory Resolution

**File**: `taskit/taskit-backend/tasks/execution/local.py` (lines 25-30)

```python
working_dir = (task.metadata or {}).get("working_dir")
if not working_dir and task.spec_id:
    working_dir = (task.spec.metadata or {}).get("working_dir")
if not working_dir:
    working_dir = getattr(settings, "ODIN_WORKING_DIR", None)
```

Three-tier fallback: task metadata → spec metadata → env var.
No board-level working directory exists.

**File**: `taskit/taskit-backend/config/settings.py` (lines 94-99)
- `ODIN_WORKING_DIR = os.environ.get("ODIN_WORKING_DIR", None)`
- `ODIN_CLI_PATH = os.environ.get("ODIN_CLI_PATH", "odin")`

---

## 8. Spec → Board Linkage

**File**: `odin/src/odin/orchestrator.py` (lines 246-256)
**File**: `odin/src/odin/backends/taskit.py`

Spec archived with `board_id` from `.odin/config.yaml → taskit.board_id`.
Tasks created with same `board_id`.
`board_id` is a config value — not validated against backend at init time.

Spec model (backend): `taskit/taskit-backend/tasks/models.py`
- `board = models.ForeignKey(Board, on_delete=CASCADE, related_name='specs')`

Task model (backend):
- `board = models.ForeignKey(Board, on_delete=CASCADE, related_name='tasks')`
- `spec = models.ForeignKey(Spec, on_delete=SET_NULL, null=True, related_name='tasks')`

Both spec and task carry `board_id` — the board is the grouping container but has no awareness of where the project lives on disk.
