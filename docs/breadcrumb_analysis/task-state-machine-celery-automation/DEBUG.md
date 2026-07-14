# Task State Machine + Celery Automation — Debug Guide

## Fast first checks — the <60s runbook

Run these IN ORDER the moment a task fails or looks stuck. Each step is seconds. Stop at the first step that explains the state. Commands assume repo root unless prefixed with `cd taskit/taskit-backend`.

```bash
# [0] ~5s — What does the board think? Status + failure metadata + auto-diagnosis.
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --brief
# Not enough? Same call, targeted sections (never --full first):
python testing_tools/task_inspect.py <task_id> --json --sections basic,diagnosis,metadata
```

Read from the metadata: `last_failure_type`, `last_failure_reason`, `last_failure_origin`, `merge_status`, `escalation_skip_reason`, `active_execution` (pid/run_token/queued_at). `--sections diagnosis` auto-detects missing execution_result posts, raw-JSON output extraction failures, and REVIEW-without-telemetry (`testing_tools/task_inspect.py:31`).

```bash
# [1] ~5s — Board-level view: is it just this task, or is the whole wave stuck?
cd taskit/taskit-backend && python testing_tools/board_overview.py <board_id>
```

```bash
# [2] ~5s — Is the machinery even on? Beat poll fires every 5s; silence = celery down.
tail -5 taskit/taskit-backend/logs/taskit_detail.log
grep -c "poll_and_execute" taskit/taskit-backend/logs/taskit_detail.log   # growing?
# Processes: one beat + at least one worker expected
pgrep -fl "celery" | head -5
```

```bash
# [3] ~10s — What did the agent actually do? Live trace (streams even from microsandbox).
ls -la .odin/logs/task_<task_id>.trace.jsonl        # size 0 or missing = agent never started
tail -3 .odin/logs/task_<task_id>.trace.jsonl       # last events (raw JSONL — do NOT cat whole file)
tail -20 .odin/logs/task_<task_id>.out               # extracted text output
# or the curated view:
odin logs <task_id>          # from the project working dir; add -f to follow, -b <board> from anywhere
```

```bash
# [4] ~10s — The dispatcher's own log for this run (exit code, log tail, SUMMARY block).
tail -30 taskit/taskit-backend/logs/spec_<spec_id>_task_<task_id>.log
# Reflection run log (if stuck in REVIEW):
ls -t taskit/taskit-backend/logs/reflect_<task_id>_*.log | head -1 | xargs tail -20
```

```bash
# [5] ~10s — Worktree state: did work land? Is a merge pending/conflicted?
git -C .odin/worktrees/<spec_odin_id>/<task_id> log --oneline -3
git -C .odin/worktrees/<spec_odin_id>/<task_id> status --porcelain | head
# merge_status lives in task metadata (step 0). conflict/error = task parked in REVIEW on purpose.
```

```bash
# [6] ~10s — Still EXECUTING? Is anything actually running?
pgrep -fl "odin exec <task_id>"
tmux ls 2>/dev/null | grep odin-<task_id>
pgrep -f "microsandbox sandbox" | wc -l            # sandboxed runs
# Dead process + still EXECUTING → stale recovery will FAIL it within ~120s (dag_executor.py:411). Wait, don't ORM-fix.
```

Decision table after the six checks:

| Finding | Meaning | Action |
|---|---|---|
| `--brief` shows FAILED + `last_failure_reason` | Normal failure path worked | Fix cause, redispatch (IN_PROGRESS via API/UI) |
| IN_PROGRESS forever, poll log silent | celery beat/worker not running | `./dev.sh` / `sh docs/fable_roadmap/bootstrap/start_services.sh` |
| IN_PROGRESS forever, poll running | no assignee, deps not DONE/TESTING, or slots full | `task_inspect.py --sections basic,deps`; assign or fix upstream |
| EXECUTING, trace growing | it's just working | background `watch_task.sh`, do something else |
| EXECUTING, no PID/VM, trace frozen | run died silently | wait ≤120s for stale recovery → FAILED with reason |
| REVIEW, no reflection comment | reflection PENDING/RUNNING/FAILED | `reflection_inspect.py <report_id>`; reflect log in [4]; re-drive via `POST /tasks/:id/reflect/` |
| REVIEW, `merge_status=conflict\|error` | merge gate parked it (by design) | resolve in spec branch, then reflect again to re-drive merge |
| Status flipped with no comment/history | someone did a raw ORM write | that's the bug — re-drive via API (DETAILS.md §12) |

## watch_task.sh — the slow watcher (use AFTER fast checks)

`docs/fable_roadmap/bootstrap/watch_task.sh <task_id> [max_wait_secs]` (default budget 2400s). Run it BACKGROUNDED from repo root after the fast checks say "still working" — it is a wake-me-on-change monitor, not a first-response tool.

Samples with exponential backoff (5s → 10s → 20s → … → 120s cap; fast early samples catch config/boot deaths in the first ~30s), one heartbeat line per sample (`status/vm/cpu/trace-bytes/head/dirty`), and exits the moment anything meaningful changes. Signals sampled: task status (`task_inspect.py --json --sections basic`), trace size (`.odin/logs/task_<id>.trace.jsonl`), worktree HEAD+dirt, microVM liveness (`pgrep "microsandbox sandbox"`), microVM CPU time.

Exit reasons (last stdout line, machine-scannable):

| Exit line | Meaning | Your next move |
|---|---|---|
| `WATCH_EXIT: terminal-status <S>` | left EXECUTING/IN_PROGRESS | run fast check [0] on the new status |
| `WATCH_EXIT: new-commit <sha>` | agent committed in its worktree | progress — inspect commit, keep watching if mid-task |
| `WATCH_EXIT: vm-gone` | no microVM process left | fast checks [0]+[3]: done, or crashed (stale recovery will land) |
| `WATCH_EXIT: stalled` | CPU AND trace frozen 3 samples | run is dead-hung: fast check [6], then stop_execution |
| `WATCH_EXIT: max-wait <secs>` | budget exhausted, still running | raise budget or investigate via [3]/[6] |

When to use which: fast checks = anything already failed/stuck/suspicious (answer in <60s). watch_task.sh = a healthy long-running dispatch you want to be woken up about. Never foreground-sleep instead of either.

## Log locations

| Layer | Log | What's in it |
|-------|-----|-------------|
| Django views | `taskit/taskit-backend/logs/taskit_detail.log` | status changes (`logger.important`), poll cycles, tracebacks |
| Django views (short) | `taskit/taskit-backend/logs/taskit.log` | abbreviated, no tracebacks |
| DAG exec per run | `taskit/taskit-backend/logs/spec_<sid>_task_<tid>.log` | odin exec stdout+stderr + SUMMARY block (`dag_executor.py:210,771`) |
| Reflection run | `taskit/taskit-backend/logs/reflect_<tid>_<report_id>.log` | odin reflect output (`dag_executor.py:584`) |
| Summarize run | `taskit/taskit-backend/logs/summarize_task_<tid>.log` | odin summarize output (`dag_executor.py:371`) |
| odin live trace | `.odin/logs/task_<tid>.trace.jsonl` | streamed harness events, survives VM kill (`orchestrator.py:3269`) |
| odin live text | `.odin/logs/task_<tid>.out` | extracted agent text (`orchestrator.py:3268`) |
| odin debug | `.odin/logs/odin_detail.log` | odin tracebacks (`odin logs debug -f`) |
| Celery broker | `taskit/taskit-backend/.celery/out/` | queued messages (filesystem broker) — growing backlog = worker dead |
| Services | `.dev-logs/` | start_services.sh backgrounded stdout |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Task never leaves IN_PROGRESS | taskit_detail.log | `"Dep check"` / `"No available slots"` / `"has no assignee"` |
| Task flips FAILED ~2min after dispatch | taskit_detail.log | `"Recovered stale EXECUTING"` |
| Result seemingly ignored | taskit_detail.log | `"Ignoring stale execution_result"` / `"run_token mismatch"` |
| Stuck in REVIEW | taskit_detail.log | `"Skipping auto-reflection"` / `"fallback status"` (reflection FAILED) |
| REVIEW with merge problem | task metadata + comments | `merge_status` = `conflict`/`error`; comment "Merge conflict merging" |
| Wrong agent suddenly assigned | task comments/history | `"Reassigned to"` (quota) / `"escalat"` (model escalation) |
| Whole DAG frozen | board_overview + deps | one upstream FAILED (BLOCKED) or parked in REVIEW |
| Agent ran but produced nothing | `.odin/logs/task_<id>.trace.jsonl` size + worktree | 0-byte trace = never started; check dual dep-check divergence |
| Executing task won't accept edits | API response | `task_executing_locked` (409) — by design, stop first |

## Quick commands

```bash
# Full spec run post-mortem (order of events, what broke)
cd taskit/taskit-backend && python testing_tools/spec_trace.py <spec_id> --sections tasks,problems

# Reflection verdict + diagnosis for a report
cd taskit/taskit-backend && python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis

# Count completed reflections (the 3-strikes counter is this DB count, nothing else)
cd taskit/taskit-backend && python -c "
import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import ReflectionReport
print(ReflectionReport.objects.filter(task_id=<task_id>, status='COMPLETED').count())"

# Stop a runaway execution properly (never kill -9 the odin pid alone — agent lives in tmux)
# via API: POST /tasks/<id>/stop_execution/ {"target_status":"FAILED","updated_by":"you@x"}
odin stop <task_id>          # CLI equivalent; then POLL until the status lands before redispatch

# Follow everything currently running
odin logs -f                 # from working dir      |  odin logs -b <board_id> -f  # from anywhere
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `ODIN_EXECUTION_STRATEGY` | `celery_dag` / `local` / empty (nothing auto-runs; beat entry only registered for celery_dag) | `""` |
| `DAG_EXECUTOR_POLL_INTERVAL` | Beat poll cadence (s) | 5 |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | Max simultaneous EXECUTING | 10 (settings.py:195) |
| `DAG_EXECUTOR_TASK_TIMEOUT_SECONDS` | exec subprocess kill + stale timeout | 1800 |
| `DAG_EXECUTOR_QUEUED_STALE_SECONDS` | EXECUTING-with-no-PID grace before auto-FAIL | 120 |
| `DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS` | odin reflect timeout | 1800 |
| `USE_FILESYSTEM_BROKER` | filesystem vs Redis celery broker | True |
| `ODIN_CLI_PATH` | which odin binary the worker execs (dual-instance trap — see `board-project-lifecycle/`) | `odin` |

## Common breakpoints

- `tasks/dag_executor.py:92` (`check_deps` result in poll loop) — why a task isn't picked up.
- `tasks/dag_executor.py:227` (post-subprocess status re-read) — who won the status race, odin or fallback.
- `tasks/views.py:2919` (stale-result guard) — why an execution_result was discarded.
- `tasks/views.py:3259` (verdict handling) — why a PASS didn't advance / a retry didn't fire.
- `tasks/dag_executor.py:739` (`_advance_task_to_testing`) — why REVIEW→TESTING was skipped (status raced off REVIEW).
- `odin/src/odin/orchestrator.py:3243` (EXECUTING + trace file setup) — agent-side start of run.
