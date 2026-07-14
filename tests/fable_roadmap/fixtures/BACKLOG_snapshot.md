# BACKLOG — the single queue

The only "what's next" list. Audits re-rank it; the operator dispatches top-down;
anything learned mid-wave lands here. Each READY entry is a dispatchable brief:
scope, acceptance, verify, suggested agent. Statuses: `NOW` · `READY` ·
`BLOCKED(reason)` · `DECISION(user)`.

## NOW — operator

1. **Wave 4 is loaded and dispatched** (`bootstrap/create_wave4.py`, spec
   `sp_fable_w4`): per-task proof paths (W4.1) → auto-promotion v1.5 (W4.2,
   deps W4.1), wave-planning automation (W4.3), data-driven routing (W4.4),
   breadcrumb staleness checker (W4.5), plus board task 174 (UI live trace,
   pre-existing). Gap-verified against the current tree before loading; the
   0g reviewer JSON contract already shipped in wave 3 — only the
   weak-model-as-default-reviewer re-evaluation remains (rank next wave).
   USER DECISIONS that gate more than any task: **Linux host** (24/7 loop,
   concurrency ceiling, forkd removal) and lint remainder.
   Dispatch-all; QUEUE_LOW watcher enforces refill.

### 0. Reflection verdict calibration — WS-A · Size S · suggested: minimax
**Context.** A wave-3 task (XDG isolation) had correct, test-green work confirmed
by its own reviewer, yet burned three full VM rework cycles and ended FAILED over
a process foul (stash use inside its own worktree — rule since scoped in root
CLAUDE.md) plus truncated proof JSON. Substance must outweigh ritual: rework
cycles are the wave's biggest cost multiplier (full VM boot + env each round).
**Scope.** Tune the reflection prompt/verdict rules: (a) correct work with a
process/proof gap → NEEDS_WORK with a targeted fix list, never FAIL; (b) FAIL is
reserved for wrong/unsafe/unverifiable work; (c) reviewer sees the current
CLAUDE.md rules (worktree-scoped stash exception) not stale ones; (d) proof
truncation is a fixable gap, named explicitly, not a verdict driver on its own.
**Acceptance.** Replaying the failed task's three reflection inputs yields
NEEDS_WORK/PASS trajectories with named fixes, not FAIL; reflection suite green.
**Verify.** Replay outputs attached; one live task passing through the new rules.

### 0b. Concurrency cap leak on rework path — WS-A · Size S · suggested: glm
**Context.** `DAG_EXECUTOR_MAX_CONCURRENCY` gates only fresh dispatches in
`poll_and_execute`; reflection-ordered rework re-enters EXECUTING directly,
bypassing the slot check — observed 4+ EXECUTING with cap 3. On a RAM-bound
host every leak risks swap death.
**Scope.** Route rework re-entry through the same slot accounting (or count
rework in `executing_count` and hold it queued); test: rework re-entry beyond
the cap waits.
**Acceptance.** With cap N and N executing, a REVIEW→rework task queues instead
of executing; test green.
**Verify.** Test output + one live wave observation.

### 0c. Restart-safe execution recovery — WS-A · Size S · suggested: glm
**Context.** After a backend/celery restart, surviving `odin exec` processes
(detached children) kept running while the new executor re-fired the same task
— observed two live `odin exec 157` processes sharing one worktree (operator
killed the orphan by hand). Recovery must check whether the recorded pid is
still alive before re-dispatching.
**Scope.** On startup recovery: if `metadata.active_execution.pid` is alive and
its cmdline matches `odin exec <id>`, adopt it (keep tracking) instead of
re-firing; only re-dispatch when the pid is dead. Test both paths.
**Acceptance.** Simulated restart with a live pid → no duplicate exec; with a
dead pid → clean re-dispatch. Tests green.
**Verify.** Test output + one observed restart without duplicates.

### 0d. Evidence-file protocol (proof as artifact, not chat) — WS-A · Size M
**Context.** Proof comments were sliced to 2000 chars in the reviewer prompt,
costing 4+ rework cycles on substantively-done work. Tactical fix owned by the
wave-3 calibration task; the structural design (user-directed): worker writes
complete evidence to a committed worktree file (.proof/), the comment is a
summary + pointer, and the reviewer reads the file from its existing read-only
worktree mount — complete or minimal per criterion. No caps anywhere.
**Unblock when:** wave-3 calibration task lands; scope whatever remains
(worker-side convention in harness prompt injection, `.proof/` in briefs,
promotion assistant reading evidence files).

### 0e. Merge-conflict reports a human can decide from — WS-C · Size S
**Context.** The merge agent's needs_human comment lists bare file paths and two
generic options (user screenshot, task 157) — no plain-english account of WHAT
each side changed, WHY they conflict, or a suggested resolution. The operator
had to open the conflict hunks by hand to learn the sides were complementary.
**Scope.** When a merge needs a human, the report must include per file: one
plain-english sentence per side ("task 155 added a baked-env hint; task 157
added an orientation block to the same function"), the conflict hunk (or its
head), and a suggested resolution when the sides look complementary
(keep-both) vs contradictory (choose). Human decides; report informs.
**Acceptance.** Replaying task 157's conflict produces a report from which a
non-author can decide in <1 minute without opening files.
**Verify.** The replayed report attached; one live conflict handled through it.

### 0f. Unparseable review output must not drive rework — WS-A · Size S
**Context.** Task 159's reflection came back with every section empty and
"Verdict extracted from unstructured output" — the parser salvaged NEEDS_WORK
from malformed reviewer output and dispatched a rework round with no fix list.
An ERROR path exists for garbage reviews but this case slipped past it.
**Scope.** When the reviewer output fails structured parsing (empty
quality/slop/improvements sections, verdict from unstructured fallback), set
verdict=ERROR and re-run the reflection (bounded retries), never NEEDS_WORK;
surface the raw head in verdict_summary for triage. Test with a captured
malformed output.
**Acceptance.** Malformed review → ERROR + reflection retry, no rework
dispatch; well-formed reviews unaffected; tests green.
**Verify.** Replay of task 159's malformed reflection + suite output.

### 0g. Weak-model reviewer re-evaluation — WS-A · Size S
**Status.** The contract half SHIPPED in wave 3 (fenced-JSON reviewer output,
lenient parser, hard-ERROR on garbage — `odin/src/odin/reflection.py`). What
remains: re-evaluate whether haiku (or another cheap model) can safely return
as the default reviewer now that the contract is trivial to emit; measure
parse-failure rate over live reviews before flipping the board default.
**Original context.** Haiku reviews repeatedly failed structured parsing (task 159 ×3),
burning rework cycles until the operator switched the board's reflection_model
to sonnet. The report format (multi-section markdown, exact first line) is
fragile for smaller models — root fix is a contract that's trivial to emit.
**Scope.** Simplify the reviewer output contract (e.g. a single fenced JSON
block: verdict, fix_list, sections) with a lenient parser + tests over captured
weak-model outputs; keep the human-readable report as a rendering of it. Then
re-evaluate whether haiku can safely return as the default reviewer (cost).
**Acceptance.** Captured task-159 haiku outputs parse or hard-ERROR (never
launder); parser suite green; one live review round-trips.
**Verify.** Parser tests + a live reflection report.

### 0h. Per-task proof paths — WS-A · Size S — DISPATCHED (wave 4, W4.1)
**Context.** The evidence protocol writes every task's proof to the SAME
`.proof/proof.md`, so consecutive tasks collide at merge (seen live: task 177
deleted task 181's proof). Change convention to `.proof/task-<id>/` in the
harness prompt injection, reviewer prompt, and promote-check artifact check.
**Acceptance.** Two consecutive tasks' proofs coexist; no merge conflict.

## READY — wave-3 candidates, ranked

### 1. Local verification gate — WS-A · Size M · suggested: minimax
**Context.** GitHub CI was removed by user directive; local gates are the bar —
but "run all four suites" is four commands in three directories with different
env vars, so nobody runs them all.
**Scope.** One entrypoint (`scripts/verify.sh` or `make verify`): odin pytest,
taskit backend, frontend vitest, snapshots — each with correct env
(`USE_SQLITE=True FIREBASE_AUTH_ENABLED=False` etc.), summary table (suite,
pass/fail/count, seconds), nonzero exit on any red, per-suite selection flags.
Document in root CLAUDE.md testing table.
**Acceptance.** One command, four suites, honest summary; a seeded failure turns
the exit red; wall clock printed.
**Verify.** Run it twice (clean + seeded failure), attach both outputs. Then wire
it into promotion spot-checks (ORCHESTRATION §4) and the audit skill.

### 2. Promotion assistant — WS-C · Size M · suggested: minimax, review by claude
**Context.** TESTING→DONE spot-checking is the biggest recurring operator duty
(duty table, ORCHESTRATION §6). The evidence is mechanical: suite green on the
spec branch, acceptance artifacts exist, reflection PASS.
**Scope.** A board-invocable check (celery task or `odin promote-check <id>`)
that runs the verification gate (item 1) on the task's spec branch, confirms the
acceptance artifacts named in the brief exist, and posts a structured
promotion-report comment (evidence links, suite results, recommend/hold).
Operator flips DONE; never auto-promotes in v1. Also wire the existing
`_cleanup_done_task_worktree` into whichever path flips DONE — today it fires
only on spec finalization, so API promotions (the real flow) leave worktrees
behind.
**Acceptance.** For 3 real TESTING tasks the report is complete enough to promote
without opening files by hand; a task with a missing artifact gets "hold" with
the gap named.
**Verify.** The 3 reports + 1 hold case, linked from task comments.

### 3. agy MCP wiring — WS-B · Size S · suggested: glm
**Context.** agy is the only confined provider not in the orchestrator's
`MCP_CONFIG_MAP` — it can't post proof comments, so its task acceptance is
scoped down (ORCHESTRATION §5).
**Scope.** Add agy's MCP config generation (guest-reachable TaskIt URL, same
pattern as opencode-family); include agy in the proof-of-work prompt injection;
regression test beside the existing MCP-config tests.
**Acceptance.** A confined agy board task posts start+proof comments via
`taskit_add_comment`; existing agents unaffected (mock suite green).
**Verify.** Live confined agy run with comment ids + suite output.

### 4. Failure-class tagger v1 — WS-A · Size M · suggested: minimax
**Context.** Auto-redispatch classifies infra failures; operations has produced a
real failure taxonomy (quota exhaustion, silent hang, env missing, sleep-frozen
timeout, invalid status envelope, worktree isolation…). Today classification
logic and evidence live apart and every failed task is triaged by a human
reading traces.
**Scope.** Deterministic tagger on task failure: exit codes, trace signatures,
timeout markers, quota markers (extend the existing quota check), stored as
`metadata.failure_class`, shown in the UI task modal, queryable via
`spec_trace.py`. Unknown → `unknown` (never guess). No LLM classification in v1.
**Acceptance.** Replaying the historical failure inventory (hang, lock race,
crash, quota misfire, sleep timeout) tags each correctly; ≥80% of historical
wave failures get a non-unknown tag.
**Verify.** Tag table over historical failures, human-judged, attached.

### 5. Auto-commit respects gitignore — WS-B · Size S · suggested: glm
**Context.** The harness auto-commit once force-added an ignored 570-file venv
into a task branch, blowing up the merge.
**Scope.** Auto-commit must not add ignored paths (no `-f`, no explicit ignored
paths); regression test on a worktree containing an ignored venv + an ignored
generated config.
**Acceptance.** Test proves ignored files never enter the auto-commit tree; real
work still commits.
**Verify.** Regression test output + one live task whose worktree contains a venv.

### 6. Per-task XDG isolation for opencode-family — WS-B · Size S · suggested: glm
**Context.** Simultaneous glm/minimax cold-starts race a shared local SQLite and
die; the ≥60s stagger rule is operator toil papering over shared state.
**Scope.** Harness staging sets per-task `XDG_DATA_HOME`/`XDG_CONFIG_HOME` for
opencode-family CLIs, seeded with the mounted auth; delete the stagger rule from
RESUME once proven.
**Acceptance.** Two opencode-family tasks dispatched in the same poll tick both
start clean (repeat 3×).
**Verify.** Simultaneous-dispatch log ×3 + the rule-deletion diff.

### 7. Autonomy metrics script — WS-D · Size S · suggested: glm
**Context.** Ratchet rule: metrics from scripts, not hand counts. Audits
currently hand-count autonomy from board reads.
**Scope.** `testing_tools/autonomy_metrics.py`: autonomy rate (agent-authored
merges / total), operator touches (steering comments + manual transitions from
TaskHistory), cost per merged change (report capture gaps honestly),
time-to-verified percentiles. `--brief`/`--json` like the other tools.
**Acceptance.** Reproduces the wave-1 and wave-2 audit figures from data; runs
clean on the current board.
**Verify.** Output over waves 1–2 attached to the next audit.

### 8. Commit trailers + provenance query — WS-D · Size S · suggested: glm
**Context.** Link every merged change to its task/spec so "why does this line
exist" is a query; also gives autonomy metrics mechanical detection.
**Scope.** Task auto-commits and merge commits gain `Task-Id:`/`Spec-Id:`
trailers; `testing_tools/why.py <file>[:<line>]` walks blame → trailer → task →
spec and prints the chain.
**Acceptance.** New merges carry trailers; `why.py` answers for a line merged
through the flow.
**Verify.** Demo on 3 lines from a wave-3 task.

### 10. Breadcrumb staleness checker — WS-D · Size S · suggested: glm
**Context.** Breadcrumbs are the repo's navigation layer and rot silently; at
least one is already known-stale (task-state-machine doc).
**Scope.** Checker maps DETAILS.md file/symbol references → current tree
(exists / grep-able), reports drift per breadcrumb; run manually or at audits.
Fix the known-stale line while there.
**Acceptance.** Flags a known-moved symbol; clean report otherwise; the stale
line corrected.
**Verify.** Checker output on the current tree.

### 11. Host-side platform verification — WS-A · Size M · BLOCKED(design)
**Context.** Agents verify inside the Linux guest, so macOS-host-only failures
are invisible — tasks have truthfully "passed" what the host contradicts (seen
again with tests written against guest-only paths). Mounts on the promotion
assistant (item 2): platform-tagged checks run host-side at promotion.
**Unblock when:** item 2 lands; then scope as a promotion-assistant extension.

### 12. Backend under a production server — WS-B · Size S · suggested: glm
**Context.** The Django dev server drops connections under concurrent load; odin
retries, but the always-on loop still runs on a dev server + SQLite.
**Scope.** `start_services.sh` gains a gunicorn/uvicorn path (dev-mode auth
semantics preserved); document rollback.
**Acceptance.** A full wave on the production server with zero connection-retry
warnings logged.
**Verify.** Celery/backend logs over one wave.

### 13. Token-usage capture + retired-model pricing — WS-A · Size M · suggested: minimax
**Context.** 5 snapshot cost tests are known-red: the golden snapshot prices a
retired model the pricing registry no longer knows, and re-capture cannot fix it
because current runs capture NO token usage (`task_inspect` shows none on any
recent task — a fresh capture fails harder, proven and reverted). Two real gaps:
(a) agent token usage isn't forwarded into task metadata; (b) cost computation
over historical tasks breaks when models retire.
**Scope.** Forward token usage into task metadata for at least the claude +
codex harnesses; keep retired models resolvable for historical cost computation;
then re-capture `full_harness_smoke` from a run with real usage.
**Acceptance.** Snapshot suite fully green against a fresh golden; `task_inspect`
on a new task shows real token counts.
**Verify.** Suite output + `task_inspect` of the capture-source spec.

## Parked task-level items

- **Lint-autofix remainder** — repo-wide autofix parked; configs landed in
  wave 2. Re-scope as ONE mechanical `ruff check --fix` + `eslint --fix`/`prettier -w` task
  on the reconciled lineage, or accept incremental enforcement (pre-commit hooks
  removed by user directive). DECISION(user).
- **Reflection reviewer selection by context size** — re-enter with WS-A
  token-capture work (item 13).
- **`init --force` preserves agent config** — re-enter on the next
  config-clobber instance.
- **Sleep-proof timeouts (monotonic clock)** — RE-ENTERED: an overnight host
  sleep burned task 170's run (watchdog-failed mid-flight, hours lost). Scope:
  executor + VM timeouts measured on a monotonic clock, and/or the executor
  holds `caffeinate` while any task executes. Rank into the next wave.

### Sandbox+worktree leak follow-ups (post-wave-3)

The primary leak fix (per-run `msb remove` in `finally` + startup sweep +
`odin gc`) shipped. Two efficiency items remain open:

- **msb COW / clonefile** — each run still copies the full ~3 GB
  `odin-agents` snapshot. Investigate `msb snapshot --help` (clonefile? APFS
  copy-on-write? sparse options?); if a delta path exists, use it so a run's
  footprint is the per-run diff, not a full copy (also cuts VM cold-start).
  Measure before/after (bytes written + seconds to first agent output) on a
  real `msb` host — `msb` is unavailable in this worktree's CI env, so a
  benchmark needs a Mac/Linux machine with msb installed. If msb offers no
  such option, cleanup-at-run-end is the accepted bound — say so on the
  board rather than silently re-trying.
- **Shared npm cache for frontend worktrees** — every frontend-touching
  worktree pays a ~1 GB `npm install`. Simplest fix: shared host-path npm
  cache mounted into the guest (`-v ~/.npm-cache:/root/.npm`
  `npm_config_cache=/root/.npm`) OR bake `node_modules` for stable deps into
  the `odin-agents` image alongside the python test-deps recipe (see
  `bootstrap/sandbox_image/extend_image_test_deps.sh`). Measure install
  time before/after on a real `odin` repo.

## DECISION(user) — only the user can make these

1. **Linux host.** Clears the forkd-removal gate AND the throughput ceiling.
   Until then: serial dispatch, forkd stays.
2. **Lint-autofix remainder** — one-shot repo-wide autofix vs incremental enforcement.
3. **Mis-attributed comments** (3 posted under the agent identity) — in-place DB
   correction was declined; leave as-is permanently, or annotate?
4. **Quarantined patches** (partial agent work from non-isolated runs, in session
   scratchpads) — re-enter as board tasks with the patches as briefs, or discard.
