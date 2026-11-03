# <one line: the question or task, as a developer would type it>

Tier: <cheap | mid | strong>  (what a person would spend on this)
Difficulty: <easy | mid | complex>  (easy: one file, one fact; mid: crosses a repo or needs the stack; complex: chains tools or repos, proof reads the database or logs)
Kind: <question | outcome | feature>  (a question is answered; an outcome is done and then checked; a feature is built, then graded by hidden tests and a judged quality score, see scenario-archetypes.md)
Bills: <no | yes>  (yes only when the task cannot avoid a paid provider call through the code; the proof skips it unless told otherwise)
Exercises: <the harness piece this scenario should benefit from: a breadcrumb, a diagnostic script, a verify gate, a CLAUDE.md rule; and why a bare agent would likely miss it>

## The prompt

<the exact text given to the agent, in the person's words: the situation and what they want done, never the how. For outcomes, say what to create, change or run and where the result must land (under writeup/, the local database, an output folder; never inside an inner repo). The proof command will verify the result.>

## The proof

<outcome only: one shell command, run from the workspace root after the
agent's run, that exits 0 when the outcome is real: a query against the
local database, a test on the output folder, a grep on a log. It reads;
it never calls a provider>

```
<command>
```

## Hidden tests

<feature only, optional: the folder under `writeup/scenarios/hidden/` that
holds acceptance tests the agent never sees (worktrees drop `writeup/`).
After the run `prove` copies its files into the worktree at the same
relative paths they sit at in the folder (`tests/hidden/test_x.py`), runs
`## The proof` (which runs them), then removes them again.>

```
<folder name, e.g. report-feature>
```

## Suite

<feature only, optional: the repo's own test command, run in each worktree
after the run. Exit 0 means no regression.>

```
<command>
```

## Lint

<feature only, optional: the repo's lint command, run the same way.>

```
<command>
```

## Reset

<optional: one shell command run before each agent run so the before and
after start from the same state (drop the row, remove the folder)>

```
<command>
```

## If the agent asks

<optional: the reply a person would give if the agent stops to ask a
question, verbatim. `prove` sends it once, in the same session, and only
if the agent asks. Leave the section out when the agent should not need to
ask. The proof checks only what the prompt states: an agent that does not
ask never sees the reply, so a file name or format named only here must not
decide pass or fail (match `writeup/out/*.csv`, not one name).>

## A correct answer must mention

<Hints for the judge only, not the score. The proof command determines whether
the outcome succeeds.>

- <a file path, a command, a number, a name; one per line>
- <...>

## Why this scenario

<one or two sentences: when this question really comes up, and what a wrong
answer costs. If the workspace was built from the same repos as an earlier
proof, reuse its scenarios so the judge compares like with like.>
