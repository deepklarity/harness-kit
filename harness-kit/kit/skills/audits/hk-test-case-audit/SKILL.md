---
name: hk-test-case-audit
description: "Write up this repo's test gaps as one HTML page scored per suite: what has no test, which tests pass with the code broken, which bill a provider, what to write first. Reads a directory or PR; runs the verify gate when asked to score. Triggers on: 'test gaps', 'what has no test', 'what isn't tested', 'audit the tests in this PR', 'audit our tests', 'test quality', or /hk-test-case-audit."
generated: true
---

# Test case audit

Reads the tests and the code they are meant to protect, and writes up the gaps for a person who
did not write either. Each finding names a file, quotes the line, and says what breaks unnoticed
because of it. It changes no test, no snapshot and no source.

Two modes, one workflow. **Read mode** is the default and runs nothing: a directory, a PR or a
branch. **Run mode** runs the suites through the verify gate (CLAUDE.md) first and adds a run strip
and a per-suite score to the page; take it when the ask is "audit our tests", "how good is our
test suite" or "test quality", or when the person names suites to score. Running is what a
static read cannot do: a red test, a skipped test or a result that changes between two runs is
evidence, not judgment. Say which mode you took in the first line back.

Three files drive the work:

- `REFERENCE.md` in this skill's base directory — the seven gaps and how to find each here, the
  severity anchors, the band table, the word caps, the finding shape and the page order. Read it
  before writing a finding.
- `finding.example.json` beside it — four findings in the working shape, every field inside its cap.
- `docs/playbook/tone_and_taste.md` — the house voice. Read it before writing any prose.

## Workflow

**1. Fix the scope and the mode.** The suites in scope are the ones the workspace's verify gate
runs (CLAUDE.md's Testing section names each suite, its runner, its directory and any env it
needs). Given nothing, take all of them. Given a directory, only the suite that owns it. Given a
PR or a branch, only the test files in the diff plus the source they cover, and say so on
the page: `gh pr diff <n> --name-only`, or
`git diff --name-only $(git merge-base main HEAD)..HEAD` for a branch — name the base on the page.
A PR is read mode; scoring a PR's diff by running a whole suite measures the wrong thing.

**2. Run the suites (run mode only).** The verify gate runs each suite in the right
directory with the right env and leaves its output in a log; the summary
table gives passed, failed and skipped per suite. Say which suites and roughly how long before
starting. Exclude the tier that bills a provider or needs a live CLI or tmux unless the person named it; say so on the page. Run each scored suite twice: a test that passes once and fails once is a finding, and
the page says exactly that, never "flaky". Fix nothing to make a suite green — a suite's own
known-red tests are a result, not an obstacle. For per-file timing, run the suite's own runner
directly with its duration flag (pytest's `--durations=0`, or the runner's equivalent). A uniform
floor under every file is startup cost, not test work: one finding carrying the floor and the
file count.

**3. Inventory.** From the repo root, collection only — it imports every test module and runs no
test body, so it is safe in both modes. Each suite's runner has a collect-only or list mode
(pytest's `--collect-only -q`, vitest's `list`, or the Django runner's own dry mode); run each
suite's, and grep for the runner's skip marker (`skipUnless`, `skipIf`, `pytest.mark.skip`, or its
equivalent).

A collection error is a finding on its own: those tests never run for anyone. Per test file,
also record what it stubs (`patch(`, `MagicMock`, `vi.mock(`) and how many of its assertions are
about a failure (`grep -c "assertRaises\|pytest.raises\|toThrow\|rejects"` against
`grep -c "assert"` or `grep -c "expect("`). Every count carries the command that produced it.

**4. Map the surface.** List what the code exposes and what the tests name. For a backend: routes
and viewsets from its URL config and views file, background tasks from its task-queue decorator.
For a CLI: its public command methods. For a frontend: its components and hooks directories. For
each, grep the tests for the name or path. The ones with
no hit go on the page as a table, not as findings, unless one of them is a guard: auth, a
permission check, a status transition, a cost cap, a merge gate.

**5. Read the tests through the seven gaps in `REFERENCE.md`,** one area at a time. Split the
reading across sonnet subagents by area (one per suite or per service), one message, in
parallel, each with its file list, the gaps, the severity anchors, the word caps and the finding
shape. An empty list is a correct answer. The verify pass stays in the main conversation.

**6. Verify, then cut.** Group the findings by file and open each file once, checking every
finding that cites it in the same pass. Reopen the quoted line; if it does not say what the
finding claims, drop it. One fact is one finding: eighty untested viewset actions is one finding
carrying a count. Merge duplicates. Drop anything below "a maintainer would write a test for
this". Count what you dropped.

**7. Score and rank.** One score out of 10 per suite in scope, from the band table in
[../README.md](../README.md): the suite's findings set the band, judgment picks the number inside it, and one
sentence says what moved it. A read-mode audit of one directory or one PR carries one score, for
that scope. Name the weakest gap per suite as what to fix first. The rank order and every word
cap are in `REFERENCE.md`; a `why` that will not fit in 45 words is two findings or is not
understood yet.

**8. Build the page** as steps 3 to 5 of `docs/skills/knowledge/hk-writeup/SKILL.md` say, in
`docs/skills/knowledge/hk-writeup/SHELL.html`, in the order `REFERENCE.md` gives, in the voice of
`docs/playbook/tone_and_taste.md`. It goes to
`$(git rev-parse --show-toplevel)/writeup/test-case-audit-<slug>.html`; `<slug>` is the suite
name, or the branch name with slashes turned into dashes when a PR was audited. Read every line a
finding cites off disk into its `pre`; nobody retypes source. Then read the page as someone who
has only the page: what is wrong with these tests, and what do I write first.

**9. Report back** in chat: which mode, where the page went, each score and its sentence, how many
findings at each severity, which tests need a live CLI, tmux or a provider and how they are
gated, and in run mode anything that could not run.

## Writing the findings

- Say what breaks unnoticed, not the rule's name. "Nothing fails if the permission check in
  the delete handler is deleted" beats "missing negative test".
- Quote the literal line and give `file:line`. A finding with no line did not get verified.
- The `fix` is the test to write, named by file and what it asserts, so the next person can
  write it without asking. Never a source change.
- Prefix inferred sentences with `[Assumption]`; a bare sentence is one you checked.
- Read the page out loud before publishing it. Any word you would not say to a teammate comes
  out.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- List the suites, the same ones your verify gate runs, with each runner and the env vars it needs (a test database, auth switched off), and which folders it ignores.
- Collection commands, one per suite:

  ```bash
  pytest --collect-only -q 2>&1 | tail -3                              # count and collection errors
  pytest --collect-only -q -m "llm or tmux_real" 2>&1 | grep -c '::'   # need a real CLI or tmux
  grep -rc "def test_" backend/tests | awk -F: '{s+=$2} END{print s}'  # a runner with no collect-only
  (cd frontend && npx vitest list 2>/dev/null | grep -c ' > ')
  grep -rn "skipUnless\|skipIf\|pytest.mark.skip" tests backend/tests | wc -l
  ```

- Mapping the surface: routes from `grep -n "register\|path(" backend/urls.py`, viewsets and actions from `grep -n "^class .*ViewSet\|@action" backend/views.py`, background tasks from `grep -rn "@shared_task" backend`; the CLI's public methods from `grep -n "^    def [a-z]" cli/main.py`; the frontend surface from `ls frontend/src/components frontend/src/hooks`.
- Sonnet subagents split by area: CLI core, backend views and serializers, backend background tasks, frontend, snapshots.
