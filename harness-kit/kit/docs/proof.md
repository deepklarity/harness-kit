# Prove the harness: the same task, before and after

The install is worth keeping only if a developer would rather have what the
agent produced with the harness than what an agent produced in the workspace
as it was before init: the answer they would act on, judged blind, with cost
and time on the row. Correct is vague on its own, so a judge decides which
of the two a developer would act on. This runs after the board has been
worked; before then there is nothing to compare. Every rule has its reason
beside it.

## Scenarios

`writeup/scenarios/` holds one markdown file per scenario, from
`docs/harnesskit/templates/scenario.md`. Three kinds (a proof needs a question and an outcome or feature):

- **A question** (`Kind: question`): where is the log for the worker, why
  did this run fail, how do these two repos call each other.
- **An outcome** (`Kind: outcome`): create, change or run something. The
  file carries a proof command, run from the workspace root after the
  agent's run, that exits 0 only when the result is real. A question can
  only tell whether the agent can explain; an outcome tells whether it can
  do.

- **A feature** (`Kind: feature`): a build graded by hidden acceptance tests
  (`## Hidden tests`, copied into the worktree after the run and removed
  again), the repo's own `## Suite` and `## Lint`, and a judged quality
  score of five rubric parts. The run cap defaults to 30 minutes. The judge
  packet carries the diff each half made. The page then leads with hidden
  tests passed, average quality and regressions (suite failed after the
  change). See the last section of `scenario-archetypes.md`.

The `Exercises:` line names the harness piece the scenario should benefit
from (a breadcrumb, a diagnostic script, a verify gate, a CLAUDE.md rule)
and why a bare agent would likely miss it. A scenario that exercises
nothing the install added cannot show a difference, so it is not worth its
cost.

Easy is one file and one fact; mid crosses a repo boundary or needs the
stack up; complex chains several tools or repos and has a proof that reads
the database or the logs. The profile's `scenarios` count says how many;
the default is one of each.

**A scripted reply.** When an agent may stop to ask (an ambiguous
request), the scenario's `## If the agent asks` holds the reply a person
would give. Headless, if the final answer asks something, `prove` resumes
the same session once with that reply and sums both legs into the row
(`replied: true`). In herdr, run `prove reply <slug>` when a pane is
waiting on the question; it types the reply into both panes.

**The prompt is what a person would type.** It names the situation and what
they want, with nothing that names the harness's answer path. The text is
identical for both halves. The "must mention" hints are for the judge only
and never reach either agent.

Outcome guardrails: the proof command only reads and never calls a
provider; a scenario that must bill says `Bills: yes` and runs only when
the person names it; writes land at the workspace root (a local database,
`writeup/out/`), never inside an inner repo. Run the proof command once on
the untouched workspace and see it fail, so a proof that passes on nothing
is caught.

## The two halves

`harnesskit prove` runs each scenario twice, in git worktrees under
`.harnesskit/proof/<slug>/`:

- **before** is checked out at `baseline_commit`, the workspace's HEAD that
  init recorded before it wrote anything.
- **after** is checked out at HEAD.

`writeup/` is removed from both, so neither sees the scenarios, earlier
answers or the board. Nothing is hidden or moved in your working tree, so
the run cannot leave the workspace half-stripped. If harness paths have
uncommitted changes, `prove` refuses and says to commit first; a baseline
needs a commit before init, and `prove` explains when there is none.

The scenario's Reset command runs in each worktree before its run, and its
proof command runs in each afterwards; the exit codes are recorded.

Both halves use the model and effort in `docs/harnesskit/profile.md`, set
explicitly on the command line and never inherited from a machine-wide
setting, with `--permission-mode auto`, the same prompt, and nothing else
typed. A global low-effort default silently changes every pair, and a
driver that omits the model hands the runner its own tier. A half is its
own session, never a subagent of the session driving it, because a nested
run crosses its recording with the parent's.

## Runners

```
docs/harnesskit/harnesskit prove <scenario.md> [--runner herdr|headless] [--budget <usd>] [--dry-run]
docs/harnesskit/harnesskit prove reply <slug>
docs/harnesskit/harnesskit prove collect <slug>
docs/harnesskit/harnesskit prove judge <slug> [--again]
docs/harnesskit/harnesskit prove render
```

**headless** runs `claude -p` in each worktree, one after the other, and
waits. Cost, tokens, turns and duration come from the final result event.
Use it when nobody needs to watch.

**herdr** opens one pane per half running interactive `claude`, prints what
it opened and returns at once, so the session driving it is never blocked
for minutes. Watch the panes; a run is finished when its screen shows the
completion line above an empty prompt (an empty prompt with a background
shell still running is not finished). Then `prove collect <slug>` reads
both session transcripts under `~/.claude/projects/`, computes tokens, and
prices them from a small table in the code. Past a few minutes of wall
time with no change on screen, report no progress to the person and let
them decide; do not poll in silence. Close the panes when done.

`--dry-run` prints what would run and its estimate and spends nothing.

## The budget

`writeup/proof/ledger.csv` has one row per half: `slug`, `half`,
`cost_usd`, `tokens`, `seconds`, `proof_exit`, `started_at`. Before a run,
`prove` refuses when spent plus the estimate passes `budget_usd` in the
profile. The estimate is the largest earlier run's cost, or 0.75 when there
is none. What remains is printed after every run.

Approval for one run does not carry to the next. Show the person the
estimate against what remains, run one scenario, report it, and ask again.
At 80 percent of the budget stop and put a decision row on the board:
"Spend the remaining amount on another scenario, or stop?" with stop as the
proposed answer.

## The judge

`prove` writes `writeup/proof/<slug>/judge.md`: the two answers shuffled as
A and B, the files each run opened (harness files called out), and the
scenario's must-mention hints. The mapping is sealed in
`.harnesskit/proof/<slug>/mapping.json`, outside `writeup/`. One judge session, on the strongest model available and blind to
which answer is which, follows `docs/harnesskit/proof/judge.md` and writes its verdict json
next to the packet.
`prove judge <slug>` runs that session headless in the after worktree, counts it
against the budget (ledger half `judge`), and renders; it refuses when a
verdict exists unless `--again`. The preference is decided in order:
achieved beats not achieved; between two that achieved, the cheaper and
faster one that used what the workspace ships wins; only when both are
close does completeness decide.

## The page

`prove render` rebuilds `writeup/proof/index.html` from every run record:
one row per scenario with the harness piece it exercises, the proof exit
for each half, cost, time, tokens and the verdict once judged. Render after
any run or judgment, in the same turn.

When the two halves differ by under 20 percent on cost and both achieved
the outcome, write "no measured difference", not a winner. A scenario the
after half gets wrong is reported as wrong; the page is not a brochure.

## The loop

Change one thing in the harness, rerun only the affected scenario's after
half (the before half is unchanged), judge, record. Keep earlier run
records so the page shows the loop. During a loop, commit subjects say what
changed and where, never what was learned: Claude Code injects the last few
commit subjects at session start, so a subject that states a finding hands
it to the next run.
