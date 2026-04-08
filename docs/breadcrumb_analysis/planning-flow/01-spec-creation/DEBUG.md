# UI Spec Creation — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|--------------|
| Django | `taskit/taskit-backend/logs/taskit_detail.log` | Serializer validation errors, DB save failures |
| Frontend | browser console | API call errors, toast messages |

## What to search for

| Symptom | Where to look | Search term |
|---------|---------------|-------------|
| Spec not created, no toast | browser console | `POST /api/specs/` — check response status |
| "Title is required" but title is filled | `CreateSpecModal.tsx` | `titleError` state — whitespace-only titles are rejected |
| Agent dropdown empty / shows no models | browser console | `GET /api/boards/{boardId}/agents/` — check response |
| Spec created but status is not `planning` | Django log | `CreatePlanningSpecSerializer` — check `planner_config` key is present in request body |
| Spec created with wrong `odin_id` | Django log | `SpecViewSet.create` — search `ui-planning-` |
| 400 error on spec create | Django log | `serializer.is_valid` failure — missing `board_id` or invalid board |

## Quick commands

```bash
# Check if a spec was created and its current status
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --brief

# Find the most recently created planning spec on a board
cd taskit/taskit-backend && python manage.py shell -c "
from tasks.models import Spec
specs = Spec.objects.filter(source='ui', odin_id__startswith='ui-planning-').order_by('-created_at')[:5]
for s in specs: print(s.id, s.status, s.odin_id, s.title[:40])
"

# Check planner_config stored on a spec
cd taskit/taskit-backend && python manage.py shell -c "
from tasks.models import Spec
s = Spec.objects.get(pk=<spec_id>)
print(s.planner_config)
print(s.status)
"
```

## Common breakpoints

- `CreateSpecModal.tsx:handleSubmit()` — inspect `plannerConfig` before the API call
- `tasks/views.py:SpecViewSet.create()` — verify `is_planning` branch is taken and `odin_id` is set
- `tasks/serializers.py:CreatePlanningSpecSerializer` — check `planner_config` deserialization
