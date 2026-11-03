---
name: hk-rca
description: "Walk a bug through Reproduce, Locate, Hypothesis, Failing Test, Fix, Verify Live, Document, and state the root cause in chat. A bug that will not reproduce takes Step 1's hard-bug branch: build a pass/fail loop, minimise, bisect, instrument. Triggers on: 'something is broken', 'debug this', 'RCA', 'root cause', 'diagnose', 'flaky', 'intermittent', 'performance regression', 'bisect', or /hk-rca."
argument-hint: "[optional: the symptom]"
---

# /hk-rca — Root cause analysis

Seven steps, in order, each one's block posted in chat before the next starts.

<bug_context> $ARGUMENTS </bug_context>

If the context is empty, take the symptom from the conversation; ask only when there is none.

Before reading any code, look the symptom up in the Quick navigation of `docs/breadcrumb_analysis/_INDEX.md` and run `/hk-pattern-check` against `docs/patterns/`; either may already name the layer.

You will paste commands, outputs and captured artifacts in chat. Write `<REDACTED>` in place of every secret, build loops against env vars so the credential stays in the environment, and quote only the lines of a captured artifact that carry the signal. If the redacted output is not enough to diagnose the bug, say so and ask the user.

## Step 1: Reproduce

Confirm the bug exists and where its edges are.

```
**Reproduce**
- Symptom: [exact error, wrong behaviour, missing data]
- Expected: [what should have happened]
- Actual: [what did happen]
- Input: [the exact command, page, spec or task id that triggers it]
- Deterministic: [yes / no / unknown]
- Environment: [local / staging / production]
```

If it reproduces every run, go to Step 2. If it does not reproduce, reproduces only some runs, or the symptom is a slowdown rather than a wrong answer, take the hard-bug branch below and rejoin at Step 3.

### Hard bugs: build the loop first

A tight pass/fail signal that goes red on _this_ bug is what makes the rest work; bisection, hypothesis-testing and instrumentation all consume it. Reading code without one will not find the cause.

**Ways to build one, roughly in this order.**

1. **Failing test** at whatever seam reaches the bug: unit, integration, e2e.
2. **Diagnostic script**, if the workspace has one for this entity (CLAUDE.md, Debugging section), wrapped in a one-line assertion. Use it in place of an HTTP loop when the API needs auth the script bypasses.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser script** (Playwright, or the Chrome DevTools MCP) that drives the UI and asserts on DOM, console or network.
5. **Replay a captured trace.** Save a real payload or event log to disk; replay it through the code path in isolation.
6. **Throwaway harness.** A minimal subset of the system (one service, mocked deps) that exercises the bug path with a single call.
7. **Property / fuzz loop.** For "sometimes wrong output", run 1000 random inputs and look for the failure mode.
8. **Bisection harness.** If the bug appeared between two known states (commit, dataset, version), automate "boot at state X, check, repeat" so you can `git bisect run` it.
9. **Differential loop.** Run the same input through old and new versions, or two configs, and diff outputs.
10. **HITL bash script.** Last resort. If a human must click, copy `scripts/hitl-loop.template.sh` from this skill's directory to `/tmp/hitl-<bug>.sh`, replace the steps between its edit markers with this bug's clicks, and run the copy.

**Tighten it.** Faster (cache setup, skip unrelated init, narrow the scope), sharper (assert on the specific symptom, not "didn't crash"), more deterministic (pin time, seed RNG, isolate the filesystem, freeze the network). A 30-second flaky loop is barely better than none; a 2-second deterministic one is tight. For an intermittent bug the goal is a **higher reproduction rate**, not a clean repro: loop the trigger 100 times, parallelise, add stress, inject sleeps. A 50% flake is debuggable; 1% is not.

**If you cannot build a loop**, stop and say so, list what you tried, and ask the user for access to an environment that reproduces it, a redacted artifact (HAR file, log dump, core dump, timestamped screen recording), or permission to add temporary production instrumentation.

**Then minimise.** Run the loop, watch it go red, and confirm the failure is the one the user described — a nearby failure leads to a fix for a different bug. Shrink to the smallest scenario that still goes red: cut inputs, callers, config, data and steps one at a time, re-running after each cut. The minimal repro becomes the failing test in Step 4.

**Instrument, one variable at a time.** Prefer a debugger or REPL if the environment has one; one breakpoint beats ten logs. Otherwise put targeted logs at the boundaries that separate your hypotheses, and tag every one with a unique prefix such as `[DEBUG-a4f2]` so cleanup is a single grep. For a slowdown, logs are the wrong tool: take a baseline measurement (timing harness, profiler, query plan), then bisect against it. Measure first, fix second.

```
**Loop:** [the one command, already run once] — [its redacted red output]
**Minimised to:** [the smallest scenario that still goes red]
```

The branch is done when that command is red-capable (drives the bug's path and asserts the user's exact symptom), deterministic (same verdict every run, or a pinned high reproduction rate), runs in seconds, and needs no human except through the HITL copy. Then go to Step 3.

## Step 2: Locate

Narrow to one layer, in this order, and stop at the first discrepancy.

1. **Data layer.** Run the diagnostic script for this entity first (CLAUDE.md, Debugging section) — a good one flags invalid transitions, missing fields and empty sections on its own. Wrong data here means a backend bug (model, serializer, view, worker); right data means it is not reaching the consumer. When the bug has no entity id to inspect (a CLI or orchestration bug), the data layer is the run artefact instead: read it with the workspace's run-log command or the failing fixture, then continue at the consumer layer.
2. **API layer.** Check the response through the page that calls it, or call the endpoint through the framework's test client or a one-off script, with auth bypassed by the stack's test hook so no credentials are needed. Missing fields here mean a serializer or view bug; present fields mean the consumer is at fault.
3. **Consumer layer.** Is the frontend, CLI or downstream code receiving the data, rendering it, or hiding it behind a conditional?

```
**Located:** the bug is in the [data / API / consumer] layer.
Evidence: [what you checked and what you saw]
```

## Step 3: Hypothesis

Write three to five ranked hypotheses before testing any; a single one anchors on the first plausible idea. Each must be falsifiable — "if [X] is the cause, then [changing Y] makes the bug disappear" — and specific enough that someone could judge it without opening the code. "Something is wrong with the parser" is not a hypothesis; "the token parser expects Anthropic-style keys but the new harness sends OpenAI-style keys" is. Post the list in chat and carry on testing in your own order; if the user re-ranks it mid-run, take their order from the next hypothesis on.

```
**Hypotheses (ranked):** 1. [X] because [Y] — 2. … — 3. …
**Standing:** the problem is [X] because [Y]. Changing [Z] should fix it because [W].
**Ruled out:** [each dropped hypothesis and the probe output that killed it]
```

## Step 4: Failing test

Write a test that reproduces the bug and fails now, in the suite that owns the code, at the seam where the bug occurs at the call site. If the best seam is shallow, write it there anyway and say what it cannot catch. If no failing test can be written, the hypothesis is probably wrong; go back to Step 3.

```
**Test:** [file path and test name]
**Result:** fails with [error message]
```

## Step 5: Fix

Change the minimum code that makes the test pass; leave refactors and cleanups for another commit. Run the test, then the verify gate (CLAUDE.md, Testing section) for every suite the change touched and paste its result. Remove every `[DEBUG-...]` line (grep the prefix), and delete throwaway harnesses or move them to `/tmp`; a harness worth keeping becomes a script in the workspace's diagnostic-scripts folder (CLAUDE.md, Debugging section) with a docstring saying what it reproduces.

```
**Fix:** [what changed, one or two sentences]
**Test:** passes
**Suite:** [the verify gate's table, or the failures it listed]
```

## Step 6: Verify live

Tests passing is not the feature working. Check the real system:

- UI bug: load the page (the Chrome DevTools MCP can drive it) and confirm.
- API bug: confirm through the page that calls it, or with the `APIClient` call from Step 2.
- Harness or pipeline bug: run it live through the workspace's run command and read it with the diagnostic script for that run (CLAUDE.md, Debugging section).
- Logic bug: run the scenario that triggered the report.
- Hard-bug branch: run the Step 1 loop against the original un-minimised scenario and watch it go green.

```
**Live check:** [what was exercised and what happened]
```

If nothing is running, start it with the workspace's dev/run commands (CLAUDE.md). Only a bug in code with no runnable surface skips this, and then say which surface is missing.

## Step 7: Document

Paste this block in chat; it is the commit message when the user asks for one:

```
Fix: [what was fixed]
Root cause: [the cause, not the symptom]
Prevention: [the test or check that now catches it]
```

If the bug exposed a gap in the system ("serializers are never tested for new fields"), add it as a follow-up item. Done when the block is in chat (and in the commit message when the user asked for a commit), the verify table and the live check are in chat, no `[DEBUG-` prefix survives, and any follow-up is written down.

## If the fix does not hold

A new error that contradicts the standing hypothesis means the hypothesis was wrong, not that a second fix is needed: go back to the ranked list in Step 3 and take the next one. After two failed hypotheses, zoom out as CLAUDE.md's Methodical Problem-Solving section says.

## Example

- Diagnostic script (Step 1, hard-bug branch, option 2): a script from your testing-tools folder (with `--brief` or `--json`) wrapped in a one-line assertion. If the API needs auth, an HTTP loop will not reach it.
- Data layer (Step 2): run the diagnostic script first, for example the script CLAUDE.md names for that entity, such as `scripts/<area>/inspect <id> --brief`. It should flag invalid transitions, missing fields and empty sections on its own. When the bug has no record id (a CLI bug), the data layer is the run artefact instead: read it with the CLI's log command or the failing fixture.
- Failing test (Step 4): the suite that owns the code, named in CLAUDE.md.
- Fix (Step 5): run the test, then the verify gate for every suite the change touched and paste its SUITE / RESULT table. A harness worth keeping becomes a script in the testing-tools folder.
- Verify live (Step 6): a harness or pipeline bug runs live through the CLI and is read with the trace script. If nothing is running, start it: the frontend dev server, the API server, the CLI run.
