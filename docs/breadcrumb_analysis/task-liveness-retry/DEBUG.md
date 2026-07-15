# Task Liveness & Retry — Debug Guide

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Dispatcher | `taskit/taskit-backend/logs/taskit_detail.log` | `poll_and_execute` cadence, `Executing task N: cmd=... run_token=...`, reap verdicts (`Reaped stale EXECUTING task N`), auto-redispatch comments, `Rotated N leftover trace file(s) before run` (W12.7) |
| Per-run | `taskit/taskit-backend/logs/spec_<spec>_task_<id>.log` | `odin exec` stdout/stderr for one run, exit code, SUMMARY block |
| Agent trace | `<working_dir>/.odin/logs/task_<id>.trace.jsonl` | the JSONL stream the liveness checks stat (mtime = progress signal). Each attempt overwrites in place; W12.7 also leaves `.<epoch>.bak` siblings for prior attempts. |
| Agent text | `<working_dir>/.odin/logs/task_<id>.out` | extracted text output (tmux runs). W12.7 rotation covers this too. |
| Odin run | `.odin/logs/run_<run_id>.jsonl` | structured odin execution events |
| TaskRun | DB (`task_runs` table) | one row per attempt: `run_token`, `pid`, `state`, `started_at`, `last_heartbeat`, `finished_at` |

## What to search for

| Symptom | Where to look | Search term |
|---------|--------------|-------------|
| Run died ~3 min after dispatch, no output | dispatcher log | `No heartbeat for over 180s` (lease reap — dead supervisor) |
| Run killed ~10 min in, process was alive | dispatcher log | `No agent progress for ~` (progress reap — live supervisor, dead agent) |
| Run killed while trace kept growing | dispatcher log | `Error loop detected:` (error-loop reap) |
| Worker restarted, task still EXECUTING | dispatcher log | `adopt` vs `pid recycled by another process` (adopt-if-alive in `_recover_stale_executions`) |
| Every retry dies the instant it starts | dispatcher log + trace mtimes | `No agent progress for ~` on a run seconds old → **stale-trace reap** (see worked example). On a healthy board you should also see `Rotated N leftover trace file(s) before run` from the W12.7 rotation step right before each dispatch. |
| Task FAILED then flipped back to IN_PROGRESS | dispatcher log | `Auto-redispatch N/2: failure class (` |
| Retry cap hit, task stuck FAILED | task metadata | `auto_redispatch_cap_reached` / `policy_cap_reached` |
| Run killed as "timeout" right after start | dispatcher log | `failure_stage` ordering — check run_token/cancel ran before deadline (F48) |
| Late execution_result post ignored | API / dispatcher | `run_token` fencing rejection in `views.execution_result` |
| Rotation step fired | dispatcher log | `Rotated \d+ leftover trace file\(s\) before run` (W12.7) — proves each dispatch starts clean |
| Rotation step broke but dispatch still ran | dispatcher log | `Leftover-trace rotation failed; continuing dispatch` (defensive: age-vs-run-start guard in `_run_progress_mtime` still catches a poisoned file) |

## Quick commands

```bash
# Trace-file age audit — the exact signal _reap_stalled_progress_runs consumes.
# Any line flagged STALE is what the progress reaper would act on for a live run.
# W12.7 rotation leaves task_<id>.trace.jsonl.<epoch>.bak siblings next to the
# canonical file — those are bygones and the age audit ignores them (they're
# not in the resolved path). Flagging STALE on a `task_<id>.trace.jsonl` that
# is *newer* than the matching TaskRun.started_at is fine; the flagging that
# matters is the contradiction: STALE on a fresh retry's own canonical file.
python3 - <<'PY'
import os, time, glob
PROGRESS_WINDOW = 600
roots = [".odin/logs"] + glob.glob(".odin/worktrees/*/*/.odin/logs")
seen = False
for root in roots:
    for pat in ("task_*.trace.jsonl", "task_*.out"):
        for p in glob.glob(os.path.join(root, pat)):
            seen = True
            try: age = int(time.time() - os.stat(p).st_mtime)
            except OSError: continue
            flag = "  <-- STALE (would be reaped)" if age > PROGRESS_WINDOW else ""
            print(f"age={age:>7}s  {p}{flag}")
if not seen: print("no task trace files found under .odin/")
PY

# Reconciler inventory — the three checks (code), their thresholds (settings),
# and any reaper verdicts already in the backend log. First thing to run when a
# task "dies on its own".
echo "=== checks ==="; grep -anE '^    _reap_|^    _recover_stale' taskit/taskit-backend/tasks/dag_executor.py
echo "=== thresholds ==="; grep -anE 'TASK_RUN_LEASE_SECONDS = |TASK_RUN_PROGRESS_WINDOW_SECONDS = |TASK_RUN_RECONCILE_INTERVAL_SECONDS|TASK_RUN_ERROR_LOOP_THRESHOLD' taskit/taskit-backend/config/settings.py
echo "=== verdicts ==="; grep -aE 'Reaped stale EXECUTING|No agent progress|No heartbeat for over|Auto-redispatch|Rotated .* leftover trace|Leftover-trace rotation failed' taskit/taskit-backend/logs/taskit_detail.log 2>/dev/null | tail -15 || echo "(log absent)"

# W12.7 fix is present iff both the rotation step in poll_and_execute and
# the age-vs-run-start guard in _run_progress_mtime are there.
echo "=== dispatch rotation ==="; grep -anE '_rotate_leftover_trace_files_for_task|Rotated .* leftover' taskit/taskit-backend/tasks/dag_executor.py
echo "=== age-vs-run-start guard ==="; grep -anE 'started_at_epoch|mtime < started_at_epoch - 2\.0' taskit/taskit-backend/tasks/dag_executor.py

# One task's full run history + failure metadata (needs the backend DB).
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --json --sections basic,diagnosis,metadata

# TaskRun rows for a task — every attempt's token, pid, state, heartbeat.
# (in practice: python manage.py shell, then TaskRun.objects.filter(task_id=N).order_by('started_at'))

# Is the worker even running the reconciler? Beat fires it every 20s.
pgrep -fl celery | head
grep -c reconcile_task_runs taskit/taskit-backend/logs/taskit_detail.log   # growing?
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `DAG_EXECUTOR_POLL_INTERVAL` | how often the dispatch poller fires | 5s |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | max simultaneous EXECUTING tasks | 3 (or `SystemSetting.executor_max_concurrency`) |
| `TASK_RUN_RECONCILE_INTERVAL_SECONDS` | reconciler cadence (also runs at worker boot + inline pre-dispatch) | 20s |
| `TASK_RUN_LEASE_SECONDS` | heartbeat staleness → lease reap (check 4a) | 180s |
| `TASK_RUN_PROGRESS_WINDOW_SECONDS` | trace-mtime staleness → progress reap (check 4b) | 600s |
| `TASK_RUN_ERROR_LOOP_THRESHOLD` | error-signature ratio → loop reap (check 4c) | 0.8 |
| `TASK_RUN_ERROR_LOOP_WINDOW` / `_MIN_LINES` | trace-tail window size / minimum lines before loop detection | 50 / 10 |
| `TASK_RUN_ERROR_LOOP_PROVIDER_BACKOFF` | next_retry_after stamped for a provider loop | 600s |
| `DAG_EXECUTOR_QUEUED_STALE_SECONDS` | no-pid deadline (covers worst-case queue wait) | 3000s |
| `DAG_EXECUTOR_TASK_TIMEOUT_SECONDS` | overall per-run wall budget (sleep-aware) | 3000s |
| `SANDBOX_MEMORY_BUDGET_MIB` | global microVM RAM budget; unset/undetectable → unbounded | host-derived |
| `DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES` | per-class retry/backoff overrides (settings layer) | `{}` |

## Common breakpoints

- `tasks/dag_executor.py:poll_and_execute` (around line 537) — the W12.7 dispatch-time rotation step. Print `rotated` (the list of backup paths) to see which leftovers were moved aside before this run.
- `tasks/dag_executor.py:_reap_stalled_progress_runs()` (line 1153) — the progress reap; print `mtime`, `cutoff_epoch`, `run.started_at` to see *which* trace file is being judged.
- `tasks/dag_executor.py:_run_progress_mtime()` (line 1098) — the resolved path + mtime; confirm it is the **current run's** trace, not a dead run's leftover. The age-vs-run-start guard at line 1147-1149 is the defensive backstop if rotation ever fails.
- `tasks/dag_executor.py:_reap_task_run()` (line 1023) — every detector funnels here; the pid-verify + kill + finish_run tail.
- `tasks/dag_executor.py:_fail_stale_execution()` (line 915) — the FAILED transition + policy handoff.
- `tasks/dag_executor.py:_maybe_auto_redispatch_infra_failure()` (line 1543) — the counter/cap decision inside the `select_for_update` lock.
- `tasks/session_resolver.py:_task_trace_path()` (line 145) — "which file is *the* trace?" — root of the stale-trace trap.
- `tasks/session_resolver.py:_rotate_leftover_trace_files_for_task()` (line 76) — the W12.7 rotation helper. Backups land at `<log_dir>/task_<id>.trace.jsonl.<epoch>.bak` (or `.out.<epoch>.bak`); the original is preserved (mtime + content) for forensics.

---

## Worked example: a dead run's trace reaped every retry

### Symptom

A task's first run died (worker restart mid-run). Every auto-redispatch after it was killed **within one reconciler pass (~20s)** of starting — long before the agent could produce output. The dispatcher log showed, on each retry:

```
Reaped stale EXECUTING task N as FAILED: No agent progress for ~25830s
(trace file idle; heartbeat fresh, pid=... alive) — the sandbox process is up
but the agent inside has stopped producing output ...
```

The tell-tale: a huge idle age (~25830s ≈ 7h) on a run that started seconds ago. A fresh run cannot have a 7-hour-old trace of its own. The progress reaper was judging the retry by **a trace file left over from the dead run**.

### Mechanism

1. The trace path is keyed to the **task id**, not the run/attempt: `session_resolver._task_trace_path()` (`tasks/session_resolver.py:145`) resolves `.odin/logs/task_<id>.trace.jsonl` for every attempt of the same task. The `.out` fallback is tried next (line 159).
2. The dead run wrote to that file and then died; the file stayed on disk with the dead run's last write mtime (hours old).
3. Each retry's `execute_single_task` started a new `odin exec`, but the reconciler (every 20s) ran `_reap_stalled_progress_runs` (`tasks/dag_executor.py:1153`) **before** the new agent wrote its first trace line.
4. `_run_progress_mtime` (`tasks/dag_executor.py:1098`) resolved the same `task_<id>.trace.jsonl` and returned the dead run's old mtime; `mtime < cutoff` → reap immediately, with a misleading "agent hung" reason.
5. The reap → FAILED → `stale_execution` is AUTO_REQUEUE → `_maybe_auto_redispatch_infra_failure` retried → same trap. Five runs died the moment they started.

The heartbeat/lease check (4a) did **not** save it: the new run's supervising process was alive and heartbeating, so the lease passed. The bug is specifically that the **progress** check (4b) could not tell the dead run's trace apart from the new run's trace.

### Diagnosis commands

```bash
# 1. Confirm the trace being judged predates the current run.
#    Run this while the retry is EXECUTING (or right after a reap):
ls -la --time-style=+%s <working_dir>/.odin/logs/task_<id>.trace.jsonl
# Compare the file's mtime epoch to the TaskRun.started_at for the current run
# (task_inspect metadata.active_execution.started_at). mtime < started_at
# => the trace belongs to a PRIOR dead run, not this one. That is the bug.

# 2. The age-audit quick command above will flag the leftover file STALE even
#    while a fresh run is live — the contradiction that pinpoints the cause.
#    On a healthy board, you'd instead see a `task_<id>.trace.jsonl.<epoch>.bak`
#    sibling next to a fresh canonical file (artifact of the dispatch-time
#    rotation step).

# 3. Read the reap reason in the dispatcher log (the huge idle age is the signal):
grep -aE 'No agent progress for ~' taskit/taskit-backend/logs/taskit_detail.log | tail
```

### The fix — two parts

The bug has two halves. The progress check must stop being fooled by a predecessor's file, **and** the dispatch step must stop handing the predecessor's file down. Fixing only one leaves a hole — rotation can fail (filesystem fault, missing log dir), and the guard can race against a barely-newer predecessor on a millisecond-class test fixture. The fix is both halves; each is independently pinned by `tests/test_dag_executor_zombie_inherit_f354.py`.

**Part 1 — dispatch-time rotation (the structural fix).** Before `poll_and_execute` mints the run_token and calls `task_runs.start_run`, it calls `_rotate_leftover_trace_files_for_task` (`tasks/session_resolver.py:76`) via `tasks/dag_executor.py:539` (inside the `try/except` at lines 537-549). Any leftover `task_<id>.trace.jsonl` or `task_<id>.out` files in the board-root log dir are moved aside with a `.<epoch>.bak` suffix, preserving the original mtime and content for forensics. The resolver then sees no fresh file at the canonical path, so the new `odin exec` writes its first line into an empty slot, and the progress scanner can only judge the run by its own file. The list of filenames covered lives at `_TASK_TRACE_FILENAMES` (`tasks/session_resolver.py:42`); the same-second collision logic at `session_resolver.py:124-129` appends a counter so two dispatches in one second never clobber each other. The rotation step is wrapped in `try/except` so a filesystem fault downgrades to a warning and the run still proceeds — Part 2 catches a poisoned file defensively. Worktree-local files are not rotated: the worktree is recreated at every dispatch and any prior file vanishes with it.

**Part 2 — age-vs-run-start guard (the defensive fix).** In `_run_progress_mtime` (`tasks/dag_executor.py:1098`), after `_stat` returns the trace's mtime, the function returns `None` whenever `mtime < run.started_at - 2.0` (line 1148). The 2-second grace absorbs the dispatch→first-write race (odin emits its first trace line a fraction of a second before the supervisor stamps the run row, and the test fixtures do the same thing on a millisecond scale). A trace older than the run's `started_at` belongs to a previous attempt; returning `None` makes `_reap_stalled_progress_runs` treat the run as "no trace yet" and the lease/heartbeat check governs. 2s vs the 600s progress window leaves real zombie detection (10-minute idle) completely untouched — the guard only catches the poisoned-retry signature where a fresh run inherits a stale file.

Root principle (both halves): the liveness signal must be attributed to the attempt it observes, never inherited from a predecessor that already died.

### Manual recovery (rotate the run files)

Before the code fix lands (or if it hasn't yet), unblock the task by moving the stale trace aside so the next retry starts clean:

```bash
cd <board working_dir>/.odin/logs
mv task_<id>.trace.jsonl task_<id>.trace.jsonl.dead-run   # or rm if unneeded
mv task_<id>.out          task_<id>.out.dead-run          # if present
# Then PATCH the task back to IN_PROGRESS (or let the next auto-redispatch fire).
# The retry now has no leftover trace to be judged by until it writes its own.
```

Rotate both the `.trace.jsonl` and the `.out` — both are tried by `_task_trace_path` (`session_resolver.py:165-170`). After rotating, the progress check sees no trace → falls back to the lease check → the run lives until its own trace appears. (This is exactly what Part 1 of the dispatch-time rotation now does automatically at every dispatch.)

### Prevention

This is one instance of **bookkeeping never kills the run** (`docs/patterns/bookkeeping-never-kills-the-run.md`): a leftover artifact from a prior attempt poisoned the liveness decision for the next. The durable fix is run-scoping the signal (Part 2) AND keeping each attempt's file space clean (Part 1) — not rotating files by hand. See also `docs/patterns/run-scoped-resource-lifecycle.md` for the broader rule that retries must not inherit a predecessor's resources.