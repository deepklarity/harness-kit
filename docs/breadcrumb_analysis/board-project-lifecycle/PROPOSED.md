# Board = Project: Current vs Proposed Architecture

## Intent

Transform Board from a lightweight task grouping container into the central entity that represents a project. Every board maps 1:1 to a project directory on disk. The UI becomes the primary interface for project setup (not the CLI). This eliminates the fragmented working directory resolution and the disconnect between CLI-initialized projects and UI-created boards.

## Current Architecture

```
User runs `odin init` in project dir
  → creates .odin/ config (board_id is a manual config field)
  → user separately creates board in UI (or uses existing)
  → user manually sets board_id in .odin/config.yaml
  → working_dir scattered: task.metadata > spec.metadata > env var
  → no validation of board ↔ project directory link
```

**Board model**: name, description, is_trial — no project awareness
**Working dir**: 3-tier fallback in execution/local.py — fragile, implicit
**Fresh install**: shows empty "All Boards" view, no onboarding guidance
**UI selector**: "All Boards" (default) + individual boards

## Proposed Architecture

```
User opens UI (fresh install)
  → no boards exist → UI redirects to board creation
  → board creation form: name + description + project directory (CWD picker)
  → on submit:
    1. POST /api/boards/ {name, description, working_dir: "/path/to/project"}
    2. Backend creates Board with working_dir field
    3. Backend runs odin init in working_dir (via subprocess or library call)
    4. Backend writes board_id back into .odin/config.yaml
    5. Returns board with working_dir and odin_initialized=true
  → UI auto-selects the new board
  → user is ready to create specs and run tasks
```

### Model Changes

**Board model** — add fields:

| Field | Type | Purpose |
|-------|------|---------|
| `working_dir` | CharField(max_length=1024, null=True, blank=True) | Absolute path to project directory on disk |
| `odin_initialized` | BooleanField(default=False) | Whether `.odin/` exists and was initialized in working_dir |

### API Changes

**Board creation** (`POST /api/boards/`):
- Accept `working_dir` in payload
- If `working_dir` provided:
  - Validate directory exists on disk
  - Run `odin init` in that directory (or equivalent setup)
  - Write `board_id` to `.odin/config.yaml`
  - Set `odin_initialized = True`
- If `working_dir` not provided: create board without project link (backward compat)

**Board update** (`PATCH /api/boards/{id}/`):
- Allow updating `working_dir`
- On update:
  - Validate new directory exists
  - Check if `.odin/` is initialized there
  - If NOT initialized: return error with message "Odin is not initialized in this directory. Run `odin init` first or let us initialize it for you."
  - Offer option to initialize (separate endpoint or query param `?init=true`)
  - Update `.odin/config.yaml` in new directory with board_id

**New endpoint**: `POST /api/boards/{id}/init-odin/`
- Runs `odin init` in the board's `working_dir`
- Writes board_id, base_url, created_by to `.odin/config.yaml`
- Returns success/failure with details

### UI Changes

**Fresh install flow**:
1. App loads → `boards = []` → redirect to `/setup` or show CreateBoardModal automatically
2. CreateBoardModal gains a directory picker field (or text input for path)
3. On submit: creates board + initializes odin in one step
4. Auto-selects the new board after creation

**Board selector** (AppHeader):
- Remove "All Boards" option entirely
- Default to first board (or most recently used)
- If only one board exists, still show dropdown (for discoverability of "+ Board")
- Board name displayed prominently — this IS the project context

**Board settings** (SettingsView):
- Show `working_dir` as editable field
- Show `odin_initialized` status badge
- "Initialize Odin" button if not initialized
- "Change Project Directory" with validation

### Working Directory Resolution — Simplified

**Current** (3-tier fallback in execution/local.py):
```python
working_dir = task.metadata.get("working_dir")        # task-level override
    or spec.metadata.get("working_dir")                # spec-level
    or settings.ODIN_WORKING_DIR                       # env var
```

**Proposed** (board-centric with override):
```python
working_dir = task.metadata.get("working_dir")         # task-level override (rare)
    or task.board.working_dir                           # board = project dir (primary)
```

The board's `working_dir` becomes the canonical source. Task-level override preserved for edge cases (e.g., task that operates on a different repo). Spec-level and env var fallbacks removed — they're no longer needed because every board has a directory.

### Benefits

1. **1 board = 1 project**: No ambiguity about where a project lives
2. **UI-first onboarding**: New users are guided through setup, not told to run CLI commands
3. **Simplified working_dir**: Board owns it, tasks inherit it — no 3-tier fallback guessing
4. **odin init via UI**: Backend handles initialization, writes config back to disk
5. **Bidirectional link**: Board knows its directory, `.odin/config.yaml` knows its board_id
6. **Editable project path**: Project moves? Update the board's working_dir (with validation)
7. **No "All Boards" confusion**: Each board is a project context, you're always in one

### Edge Cases to Handle

1. **Directory doesn't exist**: Validate on create and edit. Show clear error.
2. **Directory already has `.odin/`**: On create, detect existing init. Ask: use existing config or overwrite?
3. **Multiple boards pointing to same directory**: Prevent this — validate uniqueness of working_dir across boards.
4. **Editing working_dir when odin not initialized in new location**: Show error, offer to initialize.
5. **Board created without working_dir** (backward compat): Allow it but show "Set up project directory" prompt in UI.
6. **CLI users who already have `.odin/config.yaml`**: Support importing — "Link existing project" flow that reads config and creates/links board.
7. **Permissions**: Backend process must have filesystem access to working_dir. Validate read/write access.

### Migration Path

1. Add `working_dir` and `odin_initialized` fields to Board model (nullable)
2. Existing boards continue to work (working_dir=None falls back to current behavior)
3. UI shows "Set up project directory" nudge for boards without working_dir
4. New boards created through UI always have working_dir
5. Gradually deprecate env var `ODIN_WORKING_DIR` and spec.metadata working_dir

### Execution Order for Implementation

Phase 1 — Backend model + API:
- Add fields to Board model, migration
- Update serializers to include working_dir, odin_initialized
- Add validation logic
- Add odin init endpoint

Phase 2 — UI board-as-project:
- Remove "All Boards" from dropdown
- Add working_dir to CreateBoardModal
- Fresh install redirect to board creation
- Auto-select board after creation

Phase 3 — Working dir simplification:
- Update execution/local.py to use board.working_dir as primary
- Update odin orchestrator to read board.working_dir
- Deprecate spec.metadata.working_dir and ODIN_WORKING_DIR

Phase 4 — Polish:
- Board settings: edit working_dir, init status
- "Link existing project" flow for CLI users
- Uniqueness validation on working_dir
