# Trust

**Why.** Everything else stands on this. A test gate that reports fake
failures, a watchdog that cries wolf, a flag that stays stale after the
problem is gone. Each one teaches you to ignore the kit, and a kit you
ignore is worthless no matter what else we build.

**Where it stands.** Sandboxes, review, proof, and merging all work. But the
gate reported fake frontend failures four times in one day, the watchdog
raised three false alarms in one hour, and failed tasks wait for a human to
retry the obvious.

**In flight now.** The honest gate, the calm watchdog, auto-retry for
transient failures, phantom flag cleanup, history-based routing. Five tasks,
all running.

**Ideas beyond that.**
- One memory budget for everything. Executions, reviews, and merges each
  spawn their own 4 GB sandbox and only executions are capped. Six sandboxes
  ran on an 18 GB machine, swap filled up, and merges took 20 minutes. One
  shared budget ends the swapping and the false alarms it causes.
- Failure fingerprints. Every triage leaves a signature and its fix. The
  next matching failure gets answered from history: "seen this three times,
  it's the provider dropping streams, retry is safe."
- Migrate the prod store off SQLite. WAL + busy_timeout (task #253)
  absorbed the lock contention that was killing executions, but one file
  DB still serializes every writer (dag poll, merge scan, reflection
  scan, reconciler, watchers). If locks reappear under heavier load,
  PostgreSQL is the eventual fix — a deployment choice, not a code one.

When an idea here becomes real work, it gets its own section below this
line (or its own file) with the design, the tasks, and the before and after
numbers. The WHY above travels into every task description.
