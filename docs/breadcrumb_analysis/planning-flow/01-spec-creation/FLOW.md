# UI Spec Creation

Trigger: user clicks "Create plan" in `CreateSpecModal`
End state: spec row in DB with `status=planning`, `source=ui`, `odin_id=ui-planning-{uuid}`. User is redirected to the spec detail page.

## Flow

`CreateSpecModal.tsx` :: `handleSubmit()`
  → validates title + content (inline, no API call)
  → builds `plannerConfig = {agent, model?, quick?, auto?}`
  → calls `service.createPlanningSpec({title, content, boardId, plannerConfig})`

`HarnessTimeService.ts` :: `createPlanningSpec()`
  → POST `/api/specs/`
  → body: `{title, content, board_id, planner_config: {agent, model, quick?, auto?}}`
  → returns spec id (number)

`tasks/views.py` :: `SpecViewSet.create()`
  → detects `planner_config` key in request.data
  → selects `CreatePlanningSpecSerializer`
  → saves spec with `odin_id="ui-planning-{12-char uuid}"`, `source="ui"`, `status=Spec.STATUS_PLANNING`
  → returns `SpecSerializer(spec).data` with HTTP 201

`tasks/serializers.py` :: `CreatePlanningSpecSerializer`
  → fields: `title`, `content`, `board_id`, `planner_config`
  → no status field — status is set by the view via `.save(status=...)`

`CreateSpecModal.tsx` :: `handleSubmit()` (continued)
  → receives spec id
  → calls `onCreated(specId)` → parent navigates to `/specs/{specId}`

`SpecDetailView.tsx` :: component mount
  → fetches spec, sees `spec.status === 'planning'`
  → sets `terminalVisible = true`, increments `terminalKey`
  → renders `<PlanningTerminal specId={spec.id} onComplete={refetchSpec} />`

## Notes

- Agent/model dropdown populates from `GET /api/boards/{boardId}/agents/` — same endpoint as the board settings page
- Title auto-extracts from first `# heading` in the markdown content (`extractTitle()` in `CreateSpecModal.tsx`)
- File upload (`.md` / `.txt`) reads via `FileReader` and feeds into the same `handleContentChange()` path
- `preferredPlannerModel()` picks `premiumModel` first, then `defaultModel`, then first model in list
