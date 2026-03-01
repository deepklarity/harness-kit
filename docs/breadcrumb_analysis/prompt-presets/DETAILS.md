# Prompt Presets — Detailed Trace

## 1. Static Data File

**File**: `taskit/taskit-backend/data/task_presets.json`
**Format**: JSON with `version`, `categories[]`, `presets[]`

Key structure:
- `version: 1` — schema version for future migrations
- Categories define display grouping: `slug`, `name`, `description`, `icon`, `sort_order`
- Presets reference categories by `slug`
- `source` field points to the original prompt file in `prompt-library/` (informational, not used at runtime)
- `description` contains the full prompt text (multi-paragraph, can be long)

No database involvement. Adding/removing/editing presets = editing this JSON file.

---

## 2. Backend Endpoint

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `list_presets(request)` (line 2624)
**Route**: `GET /api/presets/` (defined in `tasks/urls_api.py` line 15)

Key logic:
- `@api_view(["GET"])` — read-only, no auth required
- Resolves path relative to views.py: `Path(__file__).resolve().parent.parent / "data" / "task_presets.json"`
- Reads file on every request (no caching)
- Returns raw JSON as DRF `Response(data)`

No serializer, no model, no validation. Pass-through from file to HTTP response.

---

## 3. Frontend Service Layer

**File**: `taskit/taskit-frontend/src/services/harness/HarnessTimeService.ts`
**Function**: `fetchPresets()` (line 879)

Implementation: `return this.get<PresetsResponse>('/api/presets/')`

**Interface**: `taskit/taskit-frontend/src/services/integration/IntegrationService.ts`
**Abstract method**: `fetchPresets(): Promise<PresetsResponse>` (line 108)

The `IntegrationService` abstraction means preset fetching would work with any backend (Trello, Jira, etc.) if they implement the interface.

---

## 4. Type Definitions

**File**: `taskit/taskit-frontend/src/types/index.ts`

```
PresetCategory (lines 362-368):
  slug: string, name: string, description: string, icon: string, sort_order: number

TaskPreset (lines 370-379):
  id: string, title: string, description: string, category: string,
  icon: string, suggested_priority: string, source: string, sort_order: number

PresetsResponse (lines 381-385):
  version: number, categories: PresetCategory[], presets: TaskPreset[]
```

---

## 5. CreateTaskModal Integration

**File**: `taskit/taskit-frontend/src/components/CreateTaskModal.tsx`
**Called by**: BoardPage or App.tsx (modal trigger)

State:
- `presets: TaskPreset[]` — all available presets
- `presetCategories: PresetCategory[]` — category metadata
- `selectedPreset: TaskPreset | null` — currently selected preset

Key logic:
- `useEffect` on mount fetches presets. Failure silently degrades (empty catch).
- `handlePresetSelect(preset)`: populates title, description, priority from preset
- `handlePresetClear()`: clears selectedPreset but leaves form fields as-is (user can edit)
- `PresetPicker` renders only when `presets.length > 0`

Data flow on selection:
```
preset.title → setTitle()
preset.description → setDescription()
preset.suggested_priority → setPriority()
```

No preset metadata is attached to the created task. After selection, the preset is just form data.

---

## 6. PresetPicker Component

**File**: `taskit/taskit-frontend/src/components/PresetPicker.tsx`
**Props**: `presets`, `categories`, `selectedPreset`, `onSelect`, `onClear`

Key logic:
- **Two render modes**: selected (badge with X button) vs unselected (popover trigger button)
- **Search**: fuzzy filter on `title` and category `name` (case-insensitive includes)
- **Grouping**: presets grouped by category, sorted by `sort_order` within each
- **Category colors**: hardcoded `CATEGORY_COLORS` map (5 entries matching the 5 category slugs)
- **Description preview**: first 100 chars of preset description shown as subtitle
- **Scroll isolation**: `stopScrollCapture` prevents Dialog's react-remove-scroll from swallowing scroll events in the portaled Popover

Color mapping:
```
code-review    → #6366f1 (indigo)
ui-ux-audit    → #06b6d4 (cyan)
documentation  → #22c55e (green)
analysis       → #f97316 (orange)
quality-process → #8b5cf6 (violet)
```

The selected preset badge shows a left color bar matching its category.
