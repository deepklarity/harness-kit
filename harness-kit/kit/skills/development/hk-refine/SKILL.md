---
name: hk-refine
description: "Improve an output that isn't good enough (a reflection, plan, prompt, pipeline result) by an observe-hypothesize-change-rerun loop kept on disk. Triggers on: 'close the loop', 'this output isn't good enough', 'iterate on this', 'refine this output', 'improve this reflection', or /hk-refine."
argument-hint: "[what to improve, and what produced it]"
allowed-tools: Bash, Read, Edit, Write, Agent, Grep, Glob
---

# Iterative output refinement

Something produced an output, the output isn't good enough, and the loop from critique to change to re-run is open. This skill closes it.

Everything lives on disk, not in the conversation. The scratch directory is the record of what was tried, what worked, and why; subagents read from it, write to it, and report back in two or three lines. The conversation holds one iteration's hypothesis and verdict; for earlier ones, re-read `loop.md`.

## Context

<loop_context> $ARGUMENTS </loop_context>

If the context is empty or unclear, ask what produced the output (command, prompt, pipeline step) and what is wrong with it ("not good enough" is a fine start). The scratch directory is `<thing>-refine/` beside the file or prompt being refined; for an output with no file of its own (a pipeline result), beside the prompt or config the loop will change.

## The loop

### Phase 0: set up (first invocation only)

1. Create the scratch directory laid out under 'The scratch directory' at the end of this file.
2. Capture the baseline output to `baseline/output.md`, from the user or by running the command the workspace's testing guide names, and put that command in `run_command.txt` beside the one that regenerates it.
3. Write `baseline/run_command.txt`, and in `loop.md` the cost per run. If the run bills a provider (a reflection, an LLM pipeline step, an eval), get a go-ahead from the user before the first re-run and again after every three runs; a free run (a script, a test, a local build) needs none.
4. Write `loop.md` with the initial state.

### Phase 1: observe

Dispatch the critique so the conversation sees only its summary:

```
Agent(model: sonnet, subagent_type: general-purpose)

Read <scratch_dir>/[baseline or iter-N]/output.md and write a critique to
<scratch_dir>/[baseline or iter-N]/critique.md with these parts:
Strengths: what's working (keep these).
Weaknesses: what's not, ranked by impact.
Missing: what should be there and isn't.
Excess: what's there and shouldn't be (noise, wrong focus).
Root issue: the single biggest thing to fix, one item.
Return three lines: the root issue, the top weakness, one strength to preserve.
```

### Phase 2: hypothesize

From the critique summary, decide one change. This is the judgment call and stays in the conversation. Write `iter-N/hypothesis.md`:

```markdown
# Hypothesis for iteration N

## What to change
[Which file, which prompt section, which config value]

## Why this should help
[The link from the change to the root issue]

## What to watch for
[What might get worse when this gets better]
```

Someone else should be able to apply the change from this file alone. "Make the prompt better" is not a hypothesis; "add a structured output format to the reflection prompt, because the current output is unstructured prose that is hard to evaluate" is.

### Phase 3: change

Apply the hypothesis (a prompt file, a config value, the code that generates the output, a model or parameter) and log it in `iter-N/changes.md`: each file modified with a one-line what, then the diffs.

### Phase 4: run

Run `run_command.txt` (with the go-ahead from Phase 0 if it costs money) and capture the output to `iter-N/output.md`. If the command needs an interactive process or a long-running service that cannot run here, give the user the copy-paste command, set `loop.md` status to `running`, and continue when they paste the output back.

### Phase 5: compare

Copy the previous best and the new output to `iter-N/compare/a.md` and `b.md` in a coin-flip order and record which is which in `hypothesis.md`. Dispatch the comparison so the judge sees only the two outputs, not the hypothesis, the changes or which is newer:

```
Agent(model: sonnet, subagent_type: general-purpose)

Compare two outputs on this quality signal: [from loop.md]. You do not know which is older.
Output A: <scratch_dir>/iter-N/compare/a.md
Output B: <scratch_dir>/iter-N/compare/b.md
Write <scratch_dir>/iter-N/comparison.md with:
Winner: A, B, or tie.
Evidence: three to five specific examples.
Trade-offs: what got worse in the winner.
Confidence: obvious, marginal, or unclear.
Return the winner, the confidence, and one line of evidence.
```

### Phase 6: verdict

Write `iter-N/verdict.md`:

```markdown
# Verdict: iteration N

Result: [better, worse, or mixed]
Confidence: [obvious, marginal, or unclear]
Evidence: [one or two lines from the comparison]
Keep: [what to preserve from this iteration]
Revert: [what to undo]
```

Update `loop.md`: iteration, best so far, the hypothesis log, what we've learned, and next. Report to the user in two or three lines: what changed, the verdict, what's next. Then back to Phase 1 on the new output, or close.

### Closing

Close when the quality signal is met, when three consecutive iterations show no improvement, when the user says stop, or when another paid run would cost more than the improvement it could bring. Write `summary.md`:

```markdown
# Refine summary: [description]

## Result
Iterations: [N]
Best: [iter-N]
Closed because: [quality met | no improvement in three iterations | user stopped | cost]

## What worked
- [Change that improved the output, with evidence]

## What didn't
- [Change that didn't help or made it worse]

## Final state
Run command: [the command with all improvements applied]
Output quality: [against the original quality signal]

## If reopening later
The key changes: [one or two sentences]. The remaining weakness: [if any].
```

Then delete everything in the scratch directory except `summary.md`, `baseline/output.md` and the best `output.md`, and tell the user where they are. The loop is closed when `summary.md` exists and the improvements are applied in the real files.

## The scratch directory

```
<thing>-refine/
├── loop.md                # Live loop state (template below)
├── baseline/
│   ├── output.md          # The original output
│   ├── critique.md        # Structured critique of what's wrong
│   └── run_command.txt    # The exact command that produced the output
├── iter-1/
│   ├── hypothesis.md      # What to change and why
│   ├── changes.md         # What was changed (file paths and diffs)
│   ├── output.md          # New output after the change
│   ├── compare/           # a.md and b.md, previous best and new output in coin-flip order
│   ├── comparison.md      # Before against after
│   └── verdict.md         # Better, worse or mixed, with evidence
├── iter-2/ ...
└── summary.md             # Written when the loop closes
```

### loop.md

The single source of truth for where the loop is. Read it at the start of every iteration; update it after every verdict.

```markdown
# Refine: [short description]

## Target
What we're improving: [one line]
Run command: [the exact command to re-run]
Cost per run: [none, or the provider bill]
Quality signal: [how we know it's better, specific and measurable where possible]

## Current state
Iteration: [N]
Best so far: [baseline | iter-N]
Status: [observing | hypothesizing | changing | running | comparing | closed]

## Hypothesis log
- iter-1: [hypothesis], [verdict: better, worse or mixed]

## What we've learned
- [What definitely helps or definitely doesn't, carried forward]

## Next
[What to try next, or "closed: [reason]"]
```

## Example

- For a stored report, capture it with the inspect script in your testing-tools folder, run from the backend folder.
