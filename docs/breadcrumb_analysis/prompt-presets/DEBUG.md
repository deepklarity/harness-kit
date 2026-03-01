# Prompt Presets — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Django | `taskit/taskit-backend/logs/taskit.log` | Request to `/api/presets/`, errors |
| Frontend | Browser console (Network tab) | `GET /api/presets/` response, fetch errors |

## What to search for

| Symptom | Where to look | Search term / action |
|---------|--------------|---------------------|
| No presets shown in CreateTaskModal | Browser Network tab | Check if `GET /api/presets/` returns 200 with data |
| Preset button doesn't appear | Browser console | `presets.length` is 0 — fetch failed silently |
| Preset populates wrong fields | `CreateTaskModal.tsx` | Check `handlePresetSelect()` — mapping from preset to form state |
| Preset description truncated in picker | `PresetPicker.tsx` | `.substring(0, 100)` — by design, not a bug |
| Category missing from picker | `task_presets.json` | Verify category `slug` in presets matches a category entry |
| Category has no color | `PresetPicker.tsx` | Check `CATEGORY_COLORS` map — new categories need a color entry |
| Presets not updating after JSON edit | Backend | `list_presets()` reads file on every request — check file was saved, check the right file path |
| 500 on `/api/presets/` | `taskit/taskit-backend/logs/taskit_detail.log` | File not found or JSON parse error in `task_presets.json` |
| Scroll doesn't work in preset popover | `PresetPicker.tsx` | `stopScrollCapture` — react-remove-scroll conflict with Dialog |

## Quick commands

```bash
# Verify the presets endpoint returns valid data
curl -s http://localhost:8000/api/presets/ | python -m json.tool | head -20

# Count presets by category
curl -s http://localhost:8000/api/presets/ | python -c "
import sys, json
data = json.load(sys.stdin)
from collections import Counter
cats = Counter(p['category'] for p in data['presets'])
for cat, count in cats.most_common():
    print(f'  {cat}: {count}')
print(f'  Total: {len(data[\"presets\"])} presets in {len(data[\"categories\"])} categories')
"

# Validate JSON file directly (no server needed)
python -c "import json; d=json.load(open('taskit/taskit-backend/data/task_presets.json')); print(f'{len(d[\"presets\"])} presets, {len(d[\"categories\"])} categories, version {d[\"version\"]}')"

# Check for category slug mismatches (presets referencing non-existent categories)
python -c "
import json
d = json.load(open('taskit/taskit-backend/data/task_presets.json'))
cat_slugs = {c['slug'] for c in d['categories']}
orphans = [p['id'] for p in d['presets'] if p['category'] not in cat_slugs]
print('Orphaned presets:', orphans if orphans else 'None')
"
```

## Env vars that affect this flow

None. Presets are unconditionally available (no feature flag, no auth required).

## Common breakpoints

- `tasks/views.py:list_presets()` (line 2624) — check if file path resolves correctly
- `CreateTaskModal.tsx` useEffect (line 65) — check if fetchPresets() is called and what it returns
- `PresetPicker.tsx:handleSelect()` (line 61) — check if onSelect callback fires with correct preset data
- `CreateTaskModal.tsx:handlePresetSelect()` (line 73) — check form state population

## Adding new presets

1. Edit `taskit/taskit-backend/data/task_presets.json`
2. Add preset object to `presets[]` array with unique `id` and valid `category` slug
3. If adding a new category: add to `categories[]` AND add color to `PresetPicker.tsx:CATEGORY_COLORS`
4. No migration, no restart needed (file read on every request)
5. Verify: `curl http://localhost:8000/api/presets/` should include the new preset
