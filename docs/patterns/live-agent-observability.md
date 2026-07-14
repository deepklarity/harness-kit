# Live agent observability — the trace-file invariant

**Principle: every agent execution streams its raw output to the canonical trace
file (`.odin/logs/task_<id>.trace.jsonl`) in real time. A harness that buffers
output until exit is a defect, even if the happy path works** — because the
run you most need to inspect is the one that never exits cleanly.

Everything downstream keys off that one file: `odin logs -f <id>` (extracted
text via `task_<id>.out`), `watch_task.sh` (trace-size growth as the progress
signal), post-run ingestion into TaskIt (trace comment → TraceViewer), and any
human or AI operator who just wants `tail -f`. One file, many observers. The
path to "what is the agent doing right now?" must be straightforward,
inspectable, and identical for host and sandboxed runs.

## The standard process for watching a dispatched task

1. Dispatch backgrounded: `odin exec <id>` (env-scrubbed per F9).
2. Start the watcher backgrounded: `sh docs/fable_roadmap/bootstrap/watch_task.sh <id>`.
   It heartbeats (status, trace bytes, VM CPU, worktree HEAD/dirt) with
   exponential backoff and exits on the first meaningful change — terminal
   status, first commit, VM death, stall (CPU **and** trace frozen ×3), or budget.
   An agent session is woken by the exit; a human just reads the heartbeat log.
3. Inspect anytime, without waiting: `tail -f .odin/logs/task_<id>.trace.jsonl`
   (raw) or `odin logs -f <id>` (extracted text). Works mid-run, sandboxed or not.
4. Never wait out a timeout to learn what happened — if the watcher's trace
   signal is flat while CPU climbs, look at the trace tail *now*.

## Instances

### 2026-07-04 — microsandbox buffered 30 minutes of work, then discarded it (task #102)

Task #102 ran confined for exactly its 1800s cap and died with a 34-byte trace:
`error: exec timed out after 1800s`. Everything glm produced was gone.
Two compounding causes, both invariant violations:

1. `MicrosandboxHarness._execute_sync` used `subprocess.run(capture_output=True)`
   — memory-buffered, written to the trace file only after exit, discarded by
   the timeout path.
2. Deeper: **msb itself buffers guest stdout until VM exit and discards it
   entirely on a timeout-kill** (proven by experiment — 6 ticks arrived in one
   burst at exit; with `--timeout` only the error line survived). So host-side
   streaming (`Popen`, `read_with_trace`) could never fix it alone.

Fix (commit `4dc4e57`): bind-mount the canonical trace file into the guest and
tee the agent's stdout+stderr into it *from inside* the VM
(`bash -lc 'set -o pipefail; <agent> </dev/null 2>&1 | tee /odin-trace.jsonl'`).
virtio-fs makes the writes host-visible live (~1s) and kill-proof (proven: file
grew during the run and kept all content through a timeout-kill). A host-side
mirror thread extracts text into `task_<id>.out` so `odin logs -f` stays live.
The trace file — not msb stdout — is the output source of truth, and host-timeout
kills preserve the partial trace with an explanatory `[odin] host timeout` marker.

**Transferable lesson:** when a sandbox/wrapper sits between you and a process,
verify empirically *when* its stdout arrives and *whether it survives a kill*
before trusting it as a streaming channel. A 20-line tick-loop experiment
(1 line/sec, then a timeout-kill variant) answers both in under a minute. If the
wrapper buffers, stream through the filesystem boundary instead — mounted files
don't care how the process dies.

### 2026-07-05 — operator blocked 30 minutes on an unobservable exec

The invariant applies to the *operator's own* commands too, not just harnesses.
A blocking diagnostic bash call (task 102 trace + DB comments) hung for its full
1800s cap with the user watching nothing happen — "*that's the problem to not
see what agent is typing mid run… 30mins wasted*". The watcher
(`watch_task.sh`) was built only *after* the waste. Rule: never start a
long-running process without a live signal already in place — heartbeat first,
dispatch second. Waiting via `sleep N` chains is the same defect in operator
form (see docs/patterns/second-friction-structural-fix.md).

## Known gap (queued)

TaskIt UI cannot show the live trace yet — ingestion happens post-run (trace
posted as a comment). A backend tail endpoint over the board's
`.odin/logs/task_<id>.trace.jsonl` + a TraceViewer live mode would let humans
and dashboard-watching AIs see running agents. Logged as a wave-2 candidate in
`docs/fable_roadmap/OPERATIONS.md`.
