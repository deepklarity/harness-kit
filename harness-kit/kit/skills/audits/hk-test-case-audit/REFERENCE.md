# Test case audit — Reference

## The seven gaps

Each finding carries one `gap`. Read every test file through all seven, in this order. The spine
of all of them is one question: would this test fail if the thing it tests were broken?

### 1. Untested surface

A route, viewset action, background task, CLI command or component that no test names. Found by
the surface map (grep the tests for each exported name and path). Most of it goes on the page
as a table; it becomes a finding only when the untested thing is a guard: a permission check,
a status transition in the state-transition module, a cost cap in the cost-cap module,
a merge gate, anything a later refactor could delete without a red test.

### 2. Weak assertion

The test would still pass with the code broken. `assert response.status_code == 200` and nothing
about the body. `assert len(items) > 0`. A mocked function asserted to have been called, with
the real function's output never checked. A `try: ... except Exception: pass` around the act. A
test with no `assert` at all. A snapshot invariant so loose nobody would notice it changing. A
test that asserts the write happened rather than that the written thing is right is a receipt,
not a test.

Checks: `grep -rn "except Exception" <every test folder>`,
`grep -rL "assert" <the unit test files>`, bare assertions (`assertTrue(`, `toBeTruthy`,
`toBeDefined`, `assert_called()` with no arguments), and for each test read the assert lines
only, asking "what change to the source would turn this red".

### 3. Happy path only

The success case is tested and no failure case is. For a route: no test for 401, 403, 404, or a
bad body. For a background job or workflow runner: no test for the timeout, the worker that never
starts, the harness that exits non-zero. For a parser: no malformed input. Name the specific
failure that has no test.

### 4. Unsafe to run

The test bills a provider, needs a live service, or deletes data, and a plain
run of the verify gate would trigger it. The live tier is a spec run through the harness's own
run command (it bills a provider) and anything that needs a real agent CLI or tmux; the cheap
stand-in is the snapshot suite, which replays a captured run.

Checks: which tests carry the markers the test config registers for a live provider or a real
terminal, and what gates them (a fixture that skips without the binary counts, a docstring does
not); whether anything outside the integration tree calls the harness's run command,
`subprocess` on an agent CLI, or reads a provider API key; whether a backend
test works without the in-memory database flag and so would hit the configured production
database; `grep -rn "rmtree\|unlink\|os.remove" <every test folder>` for anything that deletes
outside a `tmp_path`.

### 5. Leaks between tests

A fixture that creates rows, files, worktrees or env vars and never removes them; a module-level
global mutated by one test and read by another; a test that depends on running after another; the
real clock or the real network; a `sleep`-and-poll loop with no upper bound. Say what the second
run sees.

### 6. Duplicated or dead test code

The same helper or fixture copied into several files instead of one `conftest.py` or
`tests/base.py`; a fixture file nothing loads; a test marked `skip` or `xfail` with no reason or
with a reason that no longer holds; a test whose name promises one thing and whose body checks
another.

### 7. Red, skipped or slow (run mode only)

A test that failed, a test that was skipped, a test that passed in one run and failed in the
other, or a suite slow enough that people stop running it. This gap needs the run, so a read-mode
audit carries none of it and the page does not pretend otherwise. Name the test, its file and
what the runner printed.

Describe the house conventions rather than fighting them: the backend tests' own base classes and
factory helpers, the CLI tests' use of `patch` and `MagicMock`, snapshot tests that check
invariants of a captured run rather than exact values. A hand-built stub that asserts the right
value scores high; a fixture-heavy test that asserts nothing scores low.

## Severity, 1 to 5 (5 worst)

Judged on what breaks unnoticed, not on how the test looks.

**5 — A plain run costs money or destroys data, or a test is red or skipped.** A provider call
with no skip, a test that runs the CLI's real run command outside the ignored integration tree, a backend test
that reaches the real database. A failing test found in run mode is a 5, and so is a skip: marking
the red test skipped is the usual way to hide it.

**4 — A guard has no red test.** A permission check, a status transition, a cost cap or a merge
gate that a refactor could delete with every test green. Also: a suite that does not collect,
so none of it runs for anyone.

**3 — A real path is uncovered or a test cannot fail.** A failure branch with no test, an assert
that passes with the code broken, a leak that makes the second run differ from the first.

**2 — Friction.** A duplicated helper, a fixture nobody loads, a skip with a stale reason, a
misleading test name.

**1 — Nit.** A typo in a test name, a stray print.

`suspected` (you would have to run it to be sure) caps at 3. A finding on lines that a PR did not
touch, when the audit is scoped to that PR, is context, not a finding.

## Score, 0 to 10

One number per suite in scope, or one for the directory or PR in read mode, printed on the first
screen with the sentence that derives it. Suites keep their own scores; averaging unrelated
suites gives a number that means nothing.

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

A red or skipped test is a 5, so a red suite scores 0 or 1 and the sentence says which test.
Inside the band, go to the top when the tests that exist assert on state and cover failure
paths, and to the bottom when they mostly check status codes or that a mock was called. Say which
in the sentence: "7 of 10: one new branch and one task path have no test; everything else asserts
body and database state."

## Word caps

| Field | Cap |
|---|---|
| score sentence | 30 |
| suite sentence | 25 |
| finding `title` | 10 |
| finding `evidence` | 25 |
| finding `what` | 20 |
| finding `why` | 45 |
| finding `fix` | 40 |

## Finding shape

```json
{ "id": "<test-file-basename>-<problem>", "sev": 4, "gap": "untested surface",
  "confidence": "traced", "suite": "backend", "count": null,
  "title": "10 words, a statement",
  "file": "backend/tests/test_x.py", "line": 3509, "fn": "ScheduleViewSet.destroy",
  "evidence": "the literal line, byte for byte",
  "what": "One factual sentence, no [Assumption].",
  "why": "What breaks unnoticed. Inferred parts carry [Assumption].",
  "fix": "The test to write: file, and what it asserts." }
```

`id` is permanent across runs. `count` carries the number of instances when one fact covers many;
`null` when it is one thing. Rank: higher severity first, `traced` before `suspected`, gap order
as above, then file and line ascending.

## Page order

Built in `docs/skills/knowledge/hk-writeup/SHELL.html`, using only what the shell styles.

1. `.kicker` "Test case audit", the scope as the `h1`, `.chips` for the commit, the mode, and
   each suite with its collected test count and how many need a live CLI or tmux.
2. A `.nav` of anchors: Score, Findings, Live tests, Untested surface. Run mode adds What ran.
3. **Score.** One `section`: the number out of 10 in bold with its derivation sentence, then a
   `.tbl` with one row per suite: suite, tests collected, live, one sentence on what it protects
   and what it does not, and "Fix first" naming the weakest gap. Then the band table.
4. **What ran** (run mode only). One `section`: the run strip as a `.tbl`, one row per suite with
   passed, failed, skipped and wall clock, whether the two runs agreed, and one sentence saying
   the suites were run and nothing was changed. In read mode this section is absent and the
   `.foot` says nothing was run.
5. **Findings**, one `section` per band, grouped by test file so one file clears several: Fix
   before running (severity 5), Write these tests first (4 and 3), Friction (2 and 1). Each
   finding is one `details.row`: `.row-name` is the severity in words, `.row-status` the title as
   a sentence; the `.dbody` holds the factual sentence, why it matters, the evidence line in a
   `pre`, the test to write, and `file:line` in `.mono`.
6. **Live tests**, one `details.row`: the summary says how many tests need a live CLI, tmux or
   a provider and what gates them; the body is a `.tbl` of file, count, what it calls, gate.
7. **Untested surface**, one `details.row`: the summary gives the count; the body is the table.
8. `.foot`: read by hand at the named commit, what was collected and what was run, how many
   findings were raised, kept and dropped.

<details>
<summary>Example</summary>

- Untested surface: a status transition in `backend/state_transitions.py`, a
  cost cap in `backend/budget.py`.
- Weak assertion checks: `grep -rn "except Exception" tests backend/tests`,
  `grep -rL "assert" tests/unit/test_*.py`.
- Unsafe to run: the live tier is a run that bills a provider, and anything that needs a real
  agent CLI or tmux; the cheap stand-in is a snapshot suite that replays a captured run.
  Checks: which tests carry the markers your config registers for those (`llm`, `tmux_real`)
  and what gates them; whether anything outside the integration folder calls the CLI,
  runs `subprocess` on an agent binary, or reads a provider API key; whether a backend test
  works without the test-database env var and so would hit the configured production-style
  database; `grep -rn "rmtree\|unlink\|os.remove" tests backend/tests` for anything that
  deletes outside a `tmp_path`.
- House conventions: `APITestCase` subclasses with factory helpers in a shared `base.py`,
  pytest with `patch` and `MagicMock` in the mock folder, snapshot tests that check
  invariants of a captured run rather than exact values.

</details>
