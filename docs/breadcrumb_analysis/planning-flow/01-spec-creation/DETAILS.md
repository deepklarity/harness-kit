# UI Spec Creation — Detailed Trace

## 1. CreateSpecModal — form validation and submit

**File**: `taskit/taskit-frontend/src/components/CreateSpecModal.tsx`
**Function**: `handleSubmit()`
**Called by**: "Create plan" button `onClick`
**Calls**: `service.createPlanningSpec()`

Key logic:
- Blocks submit if `title` or `content` is empty — sets `titleError` / `contentError` state, does not call API
- Assembles `plannerConfig` object — only includes `model` if set, only includes `quick`/`auto` if `true`
- `agent` is always included (defaults to `'claude'` on mount)
- Calls `onCreated(specId)` on success — the parent component handles navigation

Data in: `{title: string, content: string, boardId: string, agent, model, quick, auto}`
Data out: spec id (number) from API

---

## 2. CreateSpecModal — agent/model population

**File**: `taskit/taskit-frontend/src/components/CreateSpecModal.tsx`
**Function**: `useEffect` on `open` / `boardId`
**Called by**: modal open event
**Calls**: `service.fetchBoardAgents(boardId)` → `GET /api/boards/{boardId}/agents/`

Key logic:
- `buildPlannerAgentOptions()` filters backend agents to only `['claude', 'codex', 'gemini', 'qwen']`
- `preferredPlannerModel()` picks: `premiumModel` → `defaultModel` → first `is_default` model → first model
- If the fetched agents list is empty (fetch error), dropdowns still render with empty model lists — no crash
- On agent change, model auto-updates to the preferred model for that agent

Data in: `AgentConfig[]` from backend
Data out: `PlannerAgentOption[]` used to populate selects

---

## 3. HarnessTimeService — createPlanningSpec

**File**: `taskit/taskit-frontend/src/services/harness/HarnessTimeService.ts`
**Function**: `createPlanningSpec()`
**Called by**: `CreateSpecModal.handleSubmit()`
**Calls**: `POST /api/specs/`

Key logic:
- Maps camelCase `boardId` → `board_id`, `plannerConfig` → `planner_config`
- Returns the spec `id` (number) extracted from the API response
- Throws on non-2xx — caught by modal and shown as a toast

Data in: `{title, content, boardId, plannerConfig}`
Data out: `specId: number`

---

## 4. SpecViewSet.create — spec persisted

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `SpecViewSet.create()`
**Called by**: `POST /api/specs/`
**Calls**: `CreatePlanningSpecSerializer`, `Spec.save()`

Key logic:
- Route decision: if `planner_config` or `plannerConfig` key is in `request.data` → planning path; else → regular spec creation
- `odin_id` is set to `f"ui-planning-{uuid.uuid4().hex[:12]}"` — used later to find the odin-created sibling spec
- `status=Spec.STATUS_PLANNING` (`"planning"`) set at save time, not from serializer
- `source="ui"` marks this as UI-originated (not CLI)

Data in: `{title, content, board_id, planner_config}`
Data out: `SpecSerializer(spec).data` — full spec object with id, status, planner_config

---

## 5. CreatePlanningSpecSerializer

**File**: `taskit/taskit-backend/tasks/serializers.py`
**Class**: `CreatePlanningSpecSerializer`

Key logic:
- Fields: `title`, `content`, `board_id` (FK to Board), `planner_config` (JSONField, default `{}`)
- Does NOT include `status` — caller sets it explicitly via `.save(status=...)`
- `content` defaults to `""` — empty spec is valid at DB level (UI validates before calling API)

---

## 6. SpecDetailView — terminal mount decision

**File**: `taskit/taskit-frontend/src/components/SpecDetailView.tsx`
**Called by**: route render on `/specs/{specId}`

Key logic:
- `useEffect` on `spec?.status` — when status becomes `'planning'`, sets `terminalVisible = true` and increments `terminalKey`
- `terminalKey` forces `PlanningTerminal` to remount with a fresh WebSocket on retry
- `terminalVisible` is never set back to `false` in the same session — terminal stays mounted so output is readable after completion
- If navigating directly to a `planning_complete` spec (fresh load, no live session), `terminalVisible` stays `false` → shows a static "Planning completed" banner instead
