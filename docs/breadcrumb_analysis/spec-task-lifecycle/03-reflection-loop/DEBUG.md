# Reflection Loop — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Celery reflection | `taskit/taskit-backend/logs/reflect_<task_id>_<report_id>.log` | odin reflect subprocess output |
| DAG executor | `taskit/taskit-backend/logs/taskit_detail.log` | Reflection dispatch, fallback status, auto-advance logs |
| Odin reflection | `.odin/logs/` | Reflection harness execution trace |
| Django views | `taskit/taskit-backend/logs/taskit.log` | PATCH /reflections/ requests, auto-advance transitions |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Reflection stuck in PENDING | `taskit_detail.log` | `execute_reflection` — check if Celery task was dispatched |
| Reflection stuck in RUNNING | `reflect_<task_id>_<report_id>.log` | Check if subprocess is still alive or timed out |
| Odin didn't PATCH report back | `reflect_<task_id>_<report_id>.log` | Look for auth errors or harness failures |
| Verdict is empty on COMPLETED report | Use `reflection_inspect.py` | `--sections verdict,diagnosis` — checks for parsing failures |
| Reflection PASS but task didn't move to TESTING | `taskit_detail.log` | `Auto-advanced task` — check if status transition code ran |
| NEEDS_WORK but task didn't retry | `taskit_detail.log` | `NEEDS_WORK retry` — check if execution strategy was fired |
| Task failed after 3 loops but shouldn't have | `reflection_inspect.py` | Count completed reports — the count includes ALL completed reflections (manual + auto) |
| Auto-reflection didn't trigger after REVIEW | `taskit_detail.log` | `auto-reflection` or `Skipping auto-reflection` — duplicate guard may have blocked it |
| Agent not using reflection feedback on rework | Check task comments | Reflection comment should appear in agent's context on re-execution |
| Quota failure not detected | `reflection_inspect.py` | Check `quota_failure` field; also check `task_inspect.py` for `last_failure_type` |
| Task retrying same agent after quota error | `taskit_detail.log` | `Quota failure reassignment:` — if missing, detection didn't trigger |
| Quota detected but no reassignment | `taskit_detail.log` | `no alternative agent available` — no other AGENT users on the board |
| Wrong agent selected after reassignment | `taskit_detail.log` | `Quota failure reassignment: X → Y` — check BoardMembership agents |
| Dual dep check disagreement (task skipped by odin) | `spec_*_task_<id>.log` | `Waiting — unmet deps` — see 02-execute-and-dispatch DEBUG.md |

## Quick commands

```bash
# Check reflection report status and verdict
cd taskit/taskit-backend && python testing_tools/reflection_inspect.py <report_id> --brief

# Check all reflections for a task
cd taskit/taskit-backend && python -c "
import django; import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from tasks.models import ReflectionReport
for r in ReflectionReport.objects.filter(task_id='<task_id>').order_by('created_at'):
    print(f'  report={r.id} status={r.status} verdict={r.verdict} requested_by={r.requested_by} created={r.created_at}')
"

# Check task state including reflection metadata
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic

# Count completed reflections (this is what determines retry limit)
cd taskit/taskit-backend && python -c "
import django; import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from tasks.models import ReflectionReport, ReflectionStatus
count = ReflectionReport.objects.filter(task_id='<task_id>', status=ReflectionStatus.COMPLETED).count()
print(f'Completed reflections: {count}/3')
"

# Check if reflection subprocess is running
ps aux | grep "odin reflect"

# Tail reflection log for a specific task
tail -f taskit/taskit-backend/logs/reflect_<task_id>_*.log

# Check task failure metadata and current assignee (quota debugging)
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic,metadata

# Check which AGENT users are available on a board for reassignment
cd taskit/taskit-backend && python -c "
import django; import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from tasks.models import BoardMembership, UserRole
for bm in BoardMembership.objects.filter(board_id=<board_id>, user__role=UserRole.AGENT).select_related('user'):
    print(f'  {bm.user.id} | {bm.user.name} | {bm.user.email} | models={bm.user.available_models}')
"

# Check quota_failure field on a reflection report
cd taskit/taskit-backend && python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_CLI_PATH` | Path to odin binary for reflection subprocess | `odin` |
| `ODIN_WORKING_DIR` | Fallback working directory for reflection | None |
| `ODIN_EXECUTION_STRATEGY` | Determines how re-execution is triggered after NEEDS_WORK | `""` |

## Common breakpoints

- `views.py:_trigger_auto_reflection()` line 74 — auto-trigger entry, see if duplicate guard blocks
- `dag_executor.py:execute_reflection()` line 412 — guard check, see if report is in expected state
- `dag_executor.py:execute_reflection()` line 463 — fallback status, odin didn't update report
- `reflection.py:reflect_task()` line ~394 — prompt construction, see what context was gathered
- `reflection.py:parse_reflection_report()` line ~195 — verdict extraction, see if parsing worked
- `views.py:ReflectionReportViewSet.partial_update()` line ~1319 — PASS verdict → TESTING transition
- `views.py:ReflectionReportViewSet.partial_update()` line ~1344 — NEEDS_WORK verdict → retry or fail logic
- `views.py:_is_quota_failure()` line ~116 — quota detection check (inspect 3 sources)
- `views.py:_find_alternative_agent()` line ~146 — agent selection for reassignment

## Known failure modes

| Failure mode | Cause | Detection | Recovery |
|--------------|-------|-----------|----------|
| Reflection passes but task stays in REVIEW | `refresh_from_db` found task no longer in REVIEW (race) | Task in REVIEW + report COMPLETED with PASS verdict | Manually move to TESTING |
| Infinite retry loop | NEEDS_WORK verdict every time, count never reaches 3 | Check completed report count vs task status | Manual intervention or adjust reflection prompt |
| Agent ignores reflection feedback on rework | Feedback appears only as a comment, not structured injection | Check agent's effective_input in execution_result | Ensure reflection comments are visible in task context |
| Reflection timeout kills the loop | 300s not enough for complex reviews | Report stuck in RUNNING, fallback status applied | Increase timeout or cancel and retry |
| Manual reflection bumps count | `completed_count` includes ALL completed reports, not just auto | Task fails at 3 even though only 1 was auto | Filter by `requested_by` if this becomes a problem |
| Duplicate auto-reflection after re-execution | Duplicate guard only checks PENDING/RUNNING, not recent COMPLETED | Multiple reports created in quick succession | Guard prevents true duplicates; multiple cycles are expected |
| Quota reassigned but new agent also over quota | Reassignment doesn't check live quota availability | Task cycles through agents, hits 3-strike limit | Add more agents to the board, or wait for quota reset |
| Reflection itself hits quota | Reviewer agent (Claude) over quota | Report marked FAILED by dag_executor fallback | Retry manually or wait for quota reset |
