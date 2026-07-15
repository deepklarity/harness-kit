# ORCHESTRATION, the runbook

The idea in one breath. Create a task with an agent, a spec, and its
dependencies. Set it to IN_PROGRESS. The executor runs it in a sandbox, a
reviewer checks the work, the merge happens on its own, and the task lands in
TESTING. You check the evidence and set it DONE. You send work and watch. The
executor does the rest. Don't run `odin exec` or `odin reflect` by hand while
the executor is up.

## How a task moves

```
TODO → IN_PROGRESS → EXECUTING → REVIEW → TESTING → DONE
                          └─ failures land in FAILED
```

- IN_PROGRESS means go. The executor picks it up when a slot frees and its
  dependencies are done.
- TESTING to DONE is the human's call after reading the evidence. The task
  lands in TESTING on its own when the merge is clean; the flip to DONE is
  housekeeping, not a step the system owes you.
- The executor silently skips a task with a missing agent or spec. Check both
  before you dispatch.

## Create and dispatch

Create tasks with the loader scripts in `bootstrap/`. Agent accounts are
`{agent}@odin.agent`. Write every title and brief per
`docs/task_brief_template.md` and `docs/fable_roadmap/TONE.md` — a title is
a sentence you'd say to a teammate out loud, not a category label with a
system word in it.

Dispatch means setting the status through the API:

```bash
curl -s -X PATCH "http://localhost:9100/api/tasks/<id>/" \
  -H 'Content-Type: application/json' \
  -d '{"status":"IN_PROGRESS","updated_by":"<your-email>"}'
```

Send everything that is ready, all at once. The executor's limit
(`DAG_EXECUTOR_MAX_CONCURRENCY`, default 3, sized to this machine's RAM at
about 4 GB per sandbox) is the queue. Don't hold work back to manage load.

Exception — a brand-new spec: dispatch ONE task first and wait for it to
reach EXECUTING before sending the rest. Two tasks initializing a fresh
spec branch at the same time race on worktree creation and one lands in
FAILED (missing_worktree); it happened four times in one day. The requeue
always succeeds, but the failure is pure noise. The real fix (the executor
serializes spec-branch initialization) is in the backlog; until it lands,
stagger the first dispatch.

## Watch and triage

Watchers come from bootstrap/watch_board.sh, one per active board — never
hand-rolled in a session. A hand-rolled watcher missed a FAILED task on a
fresh board and broke the human's momentum; the standard one announces
pre-existing state in its first heartbeat and bounds its own silence. When
a new board is born, arming its watcher is part of the birth.

```bash
sh docs/fable_roadmap/bootstrap/watch_board.sh 5 10      # whole board
sh docs/fable_roadmap/bootstrap/watch_task.sh <id> 1800  # one task
sh docs/fable_roadmap/bootstrap/triage_task.sh <id>      # a failed task, fast
```

`WATCH_EXIT: never-picked-up` means the executor skipped the task. Check its
agent, spec, and dependencies. Live log: `odin logs -f <id>`. Infra failures
requeue on their own. The same failure twice means stop and file the real
fix. Real work failures go back through review and rework.

## Promote

A task whose review passes and whose merge lands clean advances to TESTING
on its own and stays there. TESTING means merged, as good as done. The
merge already ran the suites once; re-running them per task added
false holds (missing node_modules, placeholder paths, a lost exec bit) and
parked green work on a human for hours, so the gate and the auto-promote
that wrapped it were removed. Read the evidence — the merge comment, the
reflection verdict, the `.proof/task-<id>/` files uploaded as task-board
attachments on reflection pass — and flip TESTING to
DONE as housekeeping when your spot-check is satisfied. Nothing waits on
that flip; it is your call, not a step the system owes you. The task's
worktree cleans up on its own.

## Agent credentials in sandboxes

Each agent gets its login mounted or injected by
`odin/src/odin/harnesses/microsandbox.py`:

- **glm / minimax**: read-only mounts of the opencode and kilo config plus
  auth. The `-m <model>` flag is required. Give the sandbox at least 4 GB.
- **codex**: read-only mounts of `~/.codex/auth.json` and `config.toml`.
- **claude**: token read from the host and injected as
  `CLAUDE_CODE_OAUTH_TOKEN`, with `IS_SANDBOX=1`.
- **agy**: token read from the host keychain and seeded into a fresh keyring
  inside the sandbox on every run. If it goes stale, refresh with
  `agy models`. It posts board comments like the others.

Network: allow outbound (`--net-default-egress allow`) when the task needs to
post comments to the board. Never use `--net-rule allow@public`, it flips the
sandbox to deny-by-default and breaks DNS.

## Standing choices

Cheap agents first. glm and minimax on their newest models, checked against
the full `opencode models` list. claude only where judgment really matters.
Reviews run on sonnet for now, haiku kept writing reports our parser could
not read and every one cost a redo round. We are re-measuring that this wave.
For codex, check its quota first. Move to a stronger agent only after a real
failure, not in advance.

Hands off product code. The human touches only the layer the agents stand on:
the executor, the harnesses, the tooling. A bug in finished agent work
becomes a board task, even after DONE. If that feels too heavy, the heaviness
itself is a gap. File a task to reduce it.

Chores we are removing. Each wave should kill at least one:

| Chore | Plan |
|---|---|
| Promote-check re-running suites and holding on false gaps | retired the gate, wave 5 — TESTING is merged-and-done, the DONE flip is manual housekeeping |
| Requeueing obvious infra failures | auto-requeue, wave 4 |
| Writing wave tasks by hand | fine for now, revisit at L3 |
| Merging the wave branch back | wave 5 — auto-promote retired; merge when the spec's tasks are green |

## Cleanup

Kill your background jobs when you restart or change plans. Sandbox VMs named
`msb-*` are disposable. Never delete `odin-agents` or `odinbuild`.
