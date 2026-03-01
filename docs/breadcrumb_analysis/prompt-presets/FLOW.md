# Prompt Presets — Flow

Trigger: User opens CreateTaskModal in the frontend
End state: Task form auto-populated with preset title, description, and suggested priority

## Flow

### Data loading (on modal mount)

```
CreateTaskModal.tsx :: useEffect (mount)
  → calls service.fetchPresets()
HarnessTimeService.ts :: fetchPresets()
  → GET /api/presets/
tasks/urls_api.py :: path("presets/", views.list_presets)
  → routes to list_presets view
tasks/views.py :: list_presets(request)
  → reads data/task_presets.json from disk
  → returns full JSON: { version, categories[], presets[] }
CreateTaskModal.tsx :: .then()
  → stores presets[] and categories[] in component state
  → .catch() silently degrades (presets are optional)
```

### Preset selection

```
PresetPicker.tsx :: render
  → shows "Use a preset..." button (if presets.length > 0)
  → Popover with search bar + category-grouped preset list

User clicks a preset:
PresetPicker.tsx :: handleSelect(preset)
  → calls onSelect(preset), closes popover
CreateTaskModal.tsx :: handlePresetSelect(preset)
  → setTitle(preset.title)
  → setDescription(preset.description)
  → setPriority(preset.suggested_priority)
  → setSelectedPreset(preset) — shows badge with clear button
```

### Preset clearing

```
User clicks X on preset badge:
PresetPicker.tsx :: onClear()
CreateTaskModal.tsx :: handlePresetClear()
  → setSelectedPreset(null)
  → title/description/priority remain (user can edit freely)
```

### Task submission

```
CreateTaskModal.tsx :: handleSubmit()
  → standard task creation — preset data is now just form field values
  → no preset metadata stored on the task itself
  → POST /api/tasks/ with { title, description, priority, board, ... }
```

## Data source

`taskit/taskit-backend/data/task_presets.json` — static JSON file, not database-backed.

5 categories (27 presets total):

| Category | Slug | Presets | Color |
|----------|------|---------|-------|
| Code Review | `code-review` | 8 | `#6366f1` (indigo) |
| UI/UX Audit | `ui-ux-audit` | 4 | `#06b6d4` (cyan) |
| Documentation | `documentation` | 3 | `#22c55e` (green) |
| Analysis | `analysis` | 5 | `#f97316` (orange) |
| Quality Process | `quality-process` | 4 | `#8b5cf6` (violet) |

Each preset has: `id`, `title`, `description` (full prompt text), `category`, `icon`, `suggested_priority`, `source` (prompt-library path), `sort_order`.

## Cross-references

- Proposed TDD task presets (different concept): `task-preset-tdd-enforcement/FLOW.md`
- Prompt source files: `prompt-library/` (paths in each preset's `source` field)
