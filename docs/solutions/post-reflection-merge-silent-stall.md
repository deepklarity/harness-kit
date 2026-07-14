# Solution: Post-reflection auto-merge silent stalls (W3.22 / task #171)

## Summary

After reflection verdicts PASS, the dispatch chain that merges the task
branch into the spec branch had **three silent-skip holes**. When any of
them fired, the operator saw the reflection PASS, the task sat in REVIEW,
and `merge_status` stayed `pending` with no log line, no comment, and no
needs_human escalation. In wave 3 this hit 9 tasks (150, 151, 154, 156,
157, 164, 165, 166, 167) — the operator merged each by hand.

This fix:

1. **Closes every silent skip** with a `logger.warning` + a
   `STATUS_UPDATE` (or `QUESTION`) `TaskComment` on the task timeline.
2. **Stamps dispatch metadata** (`merge_dispatched_at`,
   `merge_dispatch_attempts`, `merge_dispatch_source`) so a watchdog can
   detect a stalled dispatch without parsing free-text logs.
3. **Wraps `merge_task_on_reflection.delay()` in try/except** so a Celery
   broker outage cannot 500 the reflection PATCH or silently lose the
   merge — instead the task is marked `merge_status = "needs_human"`
   with a `STATUS_UPDATE` comment.
4. **Adds a watchdog scan** (`tasks.dag_executor.scan_pending_merge_dispatches`)
   that runs on a Celery Beat schedule (e.g. every 60s), retries a
   stalled dispatch once, and on the second observation marks
   `needs_human` with a blocking `QUESTION` comment.

The Celery Beat entry point is `tasks.dag_executor.scan_pending_merges`.

---

## Root cause analysis

### The dispatch chain

```
ReflectionReportViewSet.partial_update()        (views.py)
└── if verdict == "PASS" and task.status == REVIEW:
    └── _merge_task_on_reflection_pass(task)    (views.py)
        ├── if not metadata["branch"]:
        │     └── _advance_task_to_testing(task); return     ← SILENT
        ├── if metadata["merge_status"] == "merged":
        │     └── return                                    ← SILENT (bare return)
        └── merge_task_on_reflection.delay(task.id)         ← RAISES if broker down (uncaught)

merge_task_on_reflection(task_id)               (dag_executor.py)
├── if not metadata["branch"]:
│     └── _advance_task_to_testing(task); return            ← SILENT
├── if metadata["merge_status"] == "merged":
│     └── _advance_task_to_testing(task); return            ← SILENT
├── _merge_task_branch(task) → MergeResult
└── ... (loud paths: conflict / needs_human / error all post comments)
```

### The three silent-skip holes

| # | Location | Trigger | Original behavior |
|---|---|---|---|
| 1 | `views.py` (PASS branch, ~line 3637) | `task.status != REVIEW` when reflection PATCH arrives | bare `if`-no-else — **no log, no comment** |
| 2 | `views.py` `_merge_task_on_reflection_pass` (~line 345) | `metadata["merge_status"] == "merged"` (duplicate dispatch / manual merge) | bare `return` — **no log, no comment** |
| 3 | `views.py` `_merge_task_on_reflection_pass` (~line 349) | `merge_task_on_reflection.delay()` raises (Celery broker outage) | exception propagates up the stack, **the PATCH 500s, no merge ever runs** |

### Why the 9 wave-3 tasks stalled

In the wave-3 evidence, the reflection reported PASS but the post-PASS
merge never fired. There are two realistic failure modes that match:

**(A) Race with status transitions.** The reflection PATCH handler
performs `task.refresh_from_db(fields=["status"])` then guards with
`if task.status == TaskStatus.REVIEW:`. If between the reflection
saving and the status check, *any other flow* moved the task out of
REVIEW — spec finalization, a manual operator transition, a previous
reflection chain that already advanced it, or `odin spec finalize` —
the guard silently skips. The reflection saved, the verdict was PASS,
the dispatch was never made, and the task was DONE without the merge.

**(B) Bare `return` on duplicate dispatch.** `_merge_task_on_reflection_pass`
returns silently when `merge_status == "merged"`. In a re-eval scenario
(operator manually merged after the agent stalled), a second reflection
PATCH correctly sees `merged` and skips the dispatch — but the operator
sees no log explaining the skip. If the manual merge *didn't* actually
push the code, this looks like a stall with no breadcrumb.

(Celery broker outage was not confirmed for these 9 tasks, but the fix
covers it anyway because it's the same symptom: silent no-op.)

### Why the 6 fired (152, 160, 161, 162, 168, 169)

These tasks landed on the happy path: reflection PATCH arrived, task was
in REVIEW, no race, no duplicate dispatch, broker reachable → dispatch
fired → `_merge_task_branch` did its work.

---

## The fix

### 1. Views.py — `_merge_task_on_reflection_pass` and the PASS handler

Every skip path now routes through a new `_record_merge_skip` helper that
logs a warning **and** posts a `STATUS_UPDATE` `TaskComment`. The
`merge_task_on_reflection.delay()` call is wrapped in try/except; a
broker failure marks `merge_status = "needs_human"` and posts a comment.
The dispatch path stamps metadata (`merge_dispatched_at`,
`merge_dispatch_attempts`, `merge_dispatch_source`) so the watchdog can
find stalled dispatches.

The PASS branch in `ReflectionReportViewSet.partial_update` now logs a
warning and posts a comment when `task.status != REVIEW` — that was the
most common silent skip.

### 2. Dag_executor.py — `merge_task_on_reflection`

The two early-return branches (no branch, already merged) now emit
`logger.info` lines. The downstream advancement is unchanged.

### 3. Dag_executor.py — new `scan_pending_merge_dispatches` + Celery Beat entry point

A new helper scans for tasks where:
- `status == REVIEW`
- `merge_status` not in `{merged, noop, needs_human, conflict, error}`
- latest completed reflection verdict is `PASS`
- last dispatch was > `MERGE_WATCHDOG_MINUTES_IDLE` ago (default 10)

For each candidate:
- If `merge_dispatch_attempts < MERGE_WATCHDOG_MAX_ATTEMPTS` (default 1):
  bump the counter, re-dispatch `merge_task_on_reflection`, log WARNING.
- Else: set `merge_status = "needs_human"` and post a `QUESTION` comment.

The Celery Beat task is `tasks.dag_executor.scan_pending_merges`. Cadence
recommendation: every 60 seconds.

### Tunables (Django settings)

```python
MERGE_WATCHDOG_MINUTES_IDLE = 10                # min idle time before retrying
MERGE_WATCHDOG_MAX_ATTEMPTS = 1                 # auto-retries before needs_human
MERGE_WATCHDOG_SCAN_INTERVAL_SECONDS = 60       # celery-beat cadence (default 60s)
```

---

## Celery Beat registration

The watchdog implementation was shipped in task #170 but never registered
in `CELERY_BEAT_SCHEDULE`, so on production the watchdog sat idle — the
acceptance criterion was implemented and tested but the silent-path
guarantee was not actually delivered. Task #171 closes that gap:

```python
CELERY_BEAT_SCHEDULE["merge-watchdog-scan"] = {
    "task": "tasks.dag_executor.scan_pending_merges",
    "schedule": int(os.environ.get("MERGE_WATCHDOG_SCAN_INTERVAL_SECONDS", "60")),
}
```

The entry is always present (not gated behind `ODIN_EXECUTION_STRATEGY`)
because stalling merges hurt every deployment mode, not just the
`celery_dag` mode. Tunables are env-overridable so an operator can adjust
cadence without code changes. A test (`MergeWatchdogBeatScheduleTests`)
pins the registration so the next person who touches the schedule has to
touch the test first.

---

## Tests

`tests/test_merge_dispatch_watchdog.py` — **15 tests, all passing.**

| Test | Covers |
|---|---|
| `test_dispatch_fires_for_review_with_branch` | Happy path — REVIEW + branch → `.delay()` called |
| `test_dispatch_records_metadata` | Stamps `merge_dispatched_at` + `merge_dispatch_attempts` |
| `test_skip_when_status_not_review_logs_and_posts_comment` | Silent skip #1 closed |
| `test_skip_when_already_merged_logs_and_posts_comment` | Silent skip #2 closed |
| `test_celery_dispatch_failure_posted_as_comment` | Silent skip #3 closed (broker outage → needs_human + comment) |
| `test_watchdog_finds_pending_merges_and_dispatches_once` | Watchdog first observation re-dispatches |
| `test_watchdog_retries_only_once_then_marks_needs_human` | Watchdog second observation escalates |
| `test_watchdog_skips_when_merge_already_done` | Watchdog ignores terminal `merge_status` |
| `test_watchdog_skips_recent_dispatch` | Watchdog respects idle window |
| `test_watchdog_only_considers_pass_verdicts` | Watchdog ignores NEEDS_WORK/FAIL verdicts |
| `test_watchdog_skips_non_review_status` | Watchdog doesn't restart moved tasks |
| `test_merge_task_on_reflection_no_branch_logs_info` | Worker-side silent skip closed |
| `test_merge_task_on_reflection_already_merged_logs_info` | Worker-side silent skip closed |
| `test_scan_pending_merges_is_in_celery_beat_schedule` | Beat registration is present (task #171 fix) |
| `test_watchdog_settings_exposed` | Settings tunables are wired |

Full backend suite: green, no regressions.

## Live verification

A live run exercises the full chain end-to-end against an in-memory
SQLite DB with a mocked Celery broker — see proof comment on task #171
for the exact transcript.

## Files changed

- `taskit/taskit-backend/tasks/views.py` — `_merge_task_on_reflection_pass`,
  new `_record_merge_skip` helper, PASS branch loud-skip in
  `ReflectionReportViewSet.partial_update`.
- `taskit/taskit-backend/tasks/dag_executor.py` — log on the two worker
  early-return paths, new `scan_pending_merge_dispatches` helper, new
  `scan_pending_merges` Celery Beat task, top-level imports extended
  for `ReflectionReport`/`ReflectionStatus`.
- `taskit/taskit-backend/config/settings.py` — register
  `merge-watchdog-scan` in `CELERY_BEAT_SCHEDULE`, expose
  `MERGE_WATCHDOG_SCAN_INTERVAL_SECONDS`, `MERGE_WATCHDOG_MINUTES_IDLE`,
  `MERGE_WATCHDOG_MAX_ATTEMPTS` (task #171 fix).
- `taskit/taskit-backend/.env.example` — document the new env vars.
- `taskit/taskit-backend/tests/test_merge_dispatch_watchdog.py` — new
  test file (13 tests); task #171 adds two beat-schedule tests
  (`MergeWatchdogBeatScheduleTests`).

## Operator notes

- The watchdog Celery task is now wired into Beat by default. The
  recommended cadence is 60s; tune `MERGE_WATCHDOG_MINUTES_IDLE` upward
  on systems where merges routinely take longer than 10 minutes, or
  `MERGE_WATCHDOG_SCAN_INTERVAL_SECONDS` to scan more/less frequently.
- If you see `merge_watchdog_escalated_at` on a task metadata, the
  watchdog has already given up — go look at the task and either merge
  manually (`odin merge <spec_odin_id>`) or fix whatever's blocking the
  Celery worker.
- The new `merge_dispatch_skip_reason` field is the canonical signal
  for *why* a dispatch was skipped — set in every skip path now.