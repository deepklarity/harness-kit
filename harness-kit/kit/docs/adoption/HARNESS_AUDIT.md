# Harness Audit

Before any proof is judged, the harness has to be good on its own: the breadcrumbs true and useful, the tools able to do what the app's interfaces do, the docs linked, the tests able to catch a break. The script scores what a machine can; the quiz scores whether a breadcrumb actually helps.

```bash
node docs/harnesskit/templates/scripts/check_harness.mjs --html   # one line per area, writeup/agent-readiness/harness-audit.html
```

Add `--json` for machine output and `--no-gate` to skip running the test gate. Exit 1 means an area is below 7. It reads only; it runs each tool's `--help` and the workspace's own declared gate (`scripts/verify.sh` or a test command in `docs/testing_process`), nothing else. A language it does not know reads "not detected", and a job that does not apply reads "n/a".

## The script (mechanical)

| Area | Scored on | Gap that becomes a row |
|---|---|---|
| interfaces | not scored; lists routes, commands, pages, jobs, tables | none, it is the inventory the rest use |
| breadcrumbs | share of interfaces a breadcrumb cites times share of its citations that are true (path, line, symbol) | an uncovered interface, a wrong citation |
| connections | share of detected cross-component links (imports or path references between sibling packages, HTTP calls to another component's route, queue producers and the task another component defines, tables written in one component and read in another) that some breadcrumb page names at both ends; n/a with one component | each uncovered link |
| index | the tree: each entry file (`docs/breadcrumb_analysis/_INDEX.md`, `scripts/README.md`) has only area rows, at most 12, each pointing at a folder with its own index; area index rows carry an answer or a runnable command; every page and entry-point script is reachable entry to area to leaf; `docs/_INDEX.md` rows point at existing paths | a leaf row in an entry file, an area without an index, an unreachable page or script |
| tools | read-only DB query, inspect a record, logs, stack up/down/status, call each route; for each component with a database or queue, an inspect tool and a logs tool reachable in the catalogue tree; each found tool answers `--help` | a missing or broken job |
| docs | links resolve, CLAUDE.md passes its audit; the fenced commands in `docs/testing_process` are listed for you to run | a broken link, a failed check |
| tests | interfaces a test exercises, the gate passes, a unit tier apart from live, share of tests that assert something real | untested interfaces first; "no tests" scores 0 and its row is: write the first tests for the riskiest interfaces |

## Closing connection and tool gaps comes first

The scenarios written later must be able to use the breadcrumbs and tools, and a scenario usually crosses components. So before any scenario is written, every `connections` link that no breadcrumb names, every `index` depth fault, and every component with a database or queue that lacks an inspect tool or a logs tool becomes a board row: a breadcrumb written with `/hk-breadcrumb-creator` (both ends of the hand-off in one page, filed under an area), or a tool added to its area's `scripts/<area>/README.md`. Work the rows, rerun the audit, then write scenarios.

## The quiz (usefulness, model-graded)

A breadcrumb that cites true lines can still answer nothing. For each breadcrumb, with the cheapest capable model:

1. **Write.** A subagent with the code reads the breadcrumb and writes 3 questions a developer would ask that it claims to answer, each with the answer verified in the code (file and line).
2. **Answer.** A second subagent given ONLY that breadcrumb, no code and no search, answers the questions.
3. **Grade.** A third subagent compares each answer with the verified one: right, partly, wrong.

Score is correct answers over questions times 10. Write one row per breadcrumb to `writeup/agent-readiness/harness-audit.csv`:

```csv
area,item,score,gap
quiz,<breadcrumb folder>,<0-10>,<the question it could not answer>
```

The script reads those rows into its `quiz` area; rerun it after the quiz.

## Closing it

Every area scores 7 or more, or the person accepted the gap in a decision row. Each area below 7 becomes board rows from its gaps column, then rerun until the line is clean. The proof confirmation shows these scores.
