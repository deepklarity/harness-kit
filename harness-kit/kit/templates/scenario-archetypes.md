# Scenario archetypes

Pick from this table, fill each in with this workspace's own setup, data and
commands, and write the result into `writeup/scenarios/` from `scenario.md`.
Grading is the answer only: the scenario's proof command plus the blind
judge. Every archetype therefore ends in something checkable: a file under
`writeup/out/`, a command that exits 0, a database or state check, or a
fact the judge verifies in the code.

**The rule: the answer must depend on this workspace.** Its setup, data,
commands and conventions, never public knowledge a model already has. If an
agent could pass from memory with no tool call, the scenario shows nothing.

Pick as many as the profile's `scenarios`, spread over the difficulties.
Write the proof command first and run it once on the untouched workspace to
see it fail.

| # | Archetype | Kind | Difficulty | Friction it probes | Why a bare agent misses it | Shape of the proof | Reply? |
|---|---|---|---|---|---|---|---|
| 1 | Where is X configured, who changed Y, are these two ids the same | question | easy | Answers from a guess or one grep, no citation | Takes the first plausible hit; a planted decoy looks right | Judge checks the named file and commit in the code; hints list the decoy as wrong | no |
| 2 | Housekeeping: put each repo on the branch this workspace uses and merge main | outcome | easy | Switches with a dirty tree, commits to a shared branch | Does not read the branch convention; forces through | Script compares each repo's branch and HEAD; the planted dirty repo is byte-identical | no |
| 3 | Audit a branch for leaked internal references | question | easy | Misses hits in prose, flags ignored folders | Greps code only, or everything | Judge checks every planted leak is named and nothing from an ignored folder is | no |
| 4 | Run the paid step on one item first, then scale | outcome | mid | Starts the full run, ignores the spend cap | Does not know this workspace's smoke command or cap | Proof counts processed records in the local store: exactly one, plus a cost note under `writeup/out/` | no |
| 5 | Fix one thing, touch nothing else | outcome | mid | Edits adjacent blocks while there | No rule against it in the prompt | Proof diffs against the baseline: only the allowed file changed, protected files byte-identical, the fix's test passes | no |
| 6 | Build a small feature with a compound brief: build, test, show | outcome | mid | Skips the test or the live check | Stops at code that compiles | Proof runs the workspace's test command (exit 0) and a hidden test the agent never saw | no |
| 7 | Write a one-page report from a data file in the house voice | outcome | mid | Fluff, repeated sections, undefined numbers | Does not read the tone guide | Proof script: the file exists under `writeup/out/`, every number appears in the source data, under the length cap, no repeated block | no |
| 8 | Resume an initiative from its resume file and board; say what needs the owner | question | mid | Rereads everything, misses the decision rows | Does not know where the resume point lives | Judge checks the answer lists exactly the planted open decisions; proof confirms the board re-rendered | no |
| 9 | Make reusable material generic from a specific example | outcome | mid | Leaves names, paths and fixed values in | Copies the example and renames a few things | Proof greps the planted identifiers: none remain; the example's own check still exits 0 | no |
| 10 | Ambiguous request that a short question settles | outcome | complex | Builds the broad reading at once | Guesses instead of asking | The scripted reply narrows it; proof checks the narrowed result exists and the broad one does not | yes |
| 11 | Diagnose a slow or hung job from partial logs, then fix the cause | outcome | complex | Blames the obvious component, no evidence | Does not know this workspace's diagnostic script or log place | Proof checks the state the fix changes (the job completes or the planted config is corrected); judge checks the cited log line | no |
| 12 | Read a PR list across repos, judge each, sample-test one claim | question | complex | Summarises PR text instead of checking the code | Trusts the description | Judge checks the planted false claim is flagged and the planted unrelated edit is named | no |

| 13 | Feature build with hidden acceptance tests | feature | complex | Builds something that passes its own tests but not the request; unrelated rewrites; no tests added | Reads the request literally, skips the repo's conventions and its own verification | Hidden tests (public behaviour only) run through the proof; the repo's suite and lint run after; the judge scores quality from the diff | no |
| 14 | End-to-end feature that needs discovery | feature | complex | Greps for a single file, changes one side of the boundary, never finds how the components connect | Reads the request literally and stops at the first component; does not open the breadcrumb or run the catalogued tool that shows the path | Hidden tests exercise the behaviour across the boundary at the mock tier; the repo's suite and lint run after; the judge scores the diff | no |

An archetype that needs a reply gets a `## If the agent asks` section in its
scenario, with the reply a person would give, verbatim. The reply reaches
the agent only if it asks, so the proof never depends on a detail only the
reply names; it checks the substance the prompt asked for.

Two more rules every scenario passes before it is kept:

- **The baseline can succeed.** Everything the task needs exists at the commit before the harness: a scenario that asks to run a gate, script or doc the harness added measures the install, not the agent, and cannot be lost.
- **The proof accepts every reasonable reading of the prompt.** Run it against two different good answers, not one (a list that leaves out test rows and one that keeps them); if a sensible answer fails, the proof encodes an assumption the prompt never stated. Fix the proof or say it in the prompt.

Not gradable by answer, left out: visual and page feedback, "slop" and
clarity corrections, wanting the finished page opened or the state
surfaced, "why did this run slow" challenges, and not-finished complaints.
They live in how the agent behaves along the way, or in a person's taste,
and leave no checkable result.

## Feature build with hidden acceptance tests (archetype 13)

The setup agent writes all of it, in this order:

1. **The request**, in a person's words: what they want and why, never how.
   No file, function or class names the agent has to guess or must match.
2. **Hidden tests** under `writeup/scenarios/hidden/<slug>/`, at the paths
   they will have in the repo (`tests/hidden/test_<name>.py`). They exercise
   public behaviour only: the command, endpoint or function the request
   names. They never import an internal name the setup agent chose itself,
   because a good implementation will name things differently.
3. **Proof the tests are fair.** In a scratch copy of the workspace, show
   each hidden test fails on the untouched code, then write two different
   implementations (different structure and names) and show each hidden
   test passes on both. Throw the scratch copies away. A test that fails on
   a sensible second implementation encodes an assumption the request never
   stated: fix the test or the request.
4. **The commands** for `## Suite` (the repo's own tests) and `## Lint`,
   run once on the untouched workspace to see they pass.
5. **`## The proof`** runs the hidden tests only; `Kind: feature`; the
   default cap is 30 minutes per run.

## End-to-end feature that needs discovery (archetype 14)

Same rules as archetype 13: the hidden tests fail on the untouched code,
pass on two different implementations, the baseline can succeed, no detail
lives only in a reply, and the fast suite and lint pass on the untouched
workspace. Follow its steps; these are what differs.

1. **The request** is a feature in a person's words that spans at least two
   components or repos, for example a value added at the API boundary that
   must reach a worker or a second service before it shows in the result.
   It never names the components, the queue or the files that connect them:
   finding how they connect is the task.
2. **Hidden tests** drive the behaviour across the boundary at the mock
   tier (no network, stack or provider): the request goes in at the public
   entry, the assertion is on what comes out at the far side. A test that
   checks only one component passes an agent that never found the link.
3. **The connection is findable** from the harness: a breadcrumb page or a
   catalogued script shows it. If neither does, the scenario measures the
   code's obscurity, not the harness; add the row first.
4. **`Kind: feature`**, `Exercises:` names the breadcrumb index row or
   tools catalogue row that leads to the connection.
