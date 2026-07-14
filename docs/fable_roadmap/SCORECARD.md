# SCORECARD

What we measure and how. Mission Control, audits, and anyone grading
progress read this file first and grade against it. The scores themselves
live in exactly one place: the table in
[`fable_roadmap.md`](fable_roadmap.md). Bucket files carry the WHY and the
ideas, never a score. Scores move only at audits, with named evidence.

## The overall score

The plain average of the eleven scored categories, one decimal. No weights.
If a category matters more right now, that shows up in what we choose to
work on, not in the arithmetic.

## The categories

Each category answers one question a person would ask about the kit.
For each: the question, where the evidence comes from, and what 0, 5,
and 10 look like. An audit that moves a score names which anchor the
evidence supports.

### Trust
**When the kit tells me something, is it true?**
Evidence: outcomes traced to run artifacts, false alarms counted, silent
failures counted.
0 — reports are guesses. 5 — honest most of the time, but at least one
class of silent failure remains. 10 — a full audit period with no false
alarm and no silent failure.

### Autonomy
**How much work lands without a person touching it?**
Evidence: the hands-free counter, hand-fixes counted per audit period.
0 — everything needs a person. 5 — routine tasks land hands-free, failures
still need a person. 10 — whole specs close with the human only reviewing.

### Routing and cost
**Is each task done by the cheapest agent that can do it, chosen from data?**
Evidence: cost per merged task, and whether assignments come from measured
data or a list someone typed.
0 — gut calls. 5 — costs recorded, assignments still opinion. 10 — data
assigns, overrides are rare and recorded as overrides.

### Memory
**Does the kit learn, or do we pay for the same lesson twice?**
Evidence: repeat failures answered from history; lessons appearing in
briefs and measurably helping.
0 — every failure diagnosed fresh. 5 — history exists and answers repeats,
effect on results unmeasured. 10 — measured: tasks that carry memory fail
less and cost less than tasks that don't.

### Getting oriented
**How fast does an agent find what it needs in a repo?**
Evidence: share of tokens spent searching (the profiler), doc-pointer read
rate.
0 — agents wander. 5 — pointers exist, effect unproven. 10 — the search
share falls audit over audit and stays low.

### Watching
**Can a person see what the kit is doing right now, in one look?**
Evidence: a live page whose numbers have scripts behind them; stalls
noticed by the machine before the human.
0 — logs only. 5 — a live page exists but a person still catches some
things first. 10 — no state change a person learns about outside the
product.

### The human's seat
**Is the human asked only when needed, and can they answer in seconds?**
Evidence: interrupts per spec, time from question to answer to resumed
work.
0 — babysitting. 5 — questions reach a person, but answering takes
digging for context. 10 — every ask arrives with its options inline and
one reply resumes the work.

### Audits
**Does the kit check itself, on a schedule, with numbers a stranger can
rerun?**
Evidence: audits that ran without being asked; metrics from scripts, not
hand counts.
0 — no checks. 5 — good audits, but only when a person asks. 10 —
scheduled audits, script-backed numbers, findings that become tasks.

### Getting started
**Can a stranger switch it on?**
Evidence: timed setup on a fresh machine, adoption runs on outside repos.
0 — only the builders. 5 — one documented path, proven once. 10 —
strangers set it up routinely and time-to-first-merged-task is measured
and short.

### Runs anywhere
**Does it run in more than one place?**
Evidence: instances alive, setup repeated on hardware that is not ours.
0 — one laptop. 5 — a second instance works. 10 — instances are routine,
each knows its own identity, none is special.

### Project powers
**Does it ship real work that is not itself?**
Evidence: external projects with merged results a person actually uses.
0 — the kit only builds the kit. 5 — one external project ships something.
10 — several projects running, humans mostly reviewing.

## Not scored

**Moonshots** is an idea funnel, not a capability. It keeps its bucket
file and its ideas, but it carries no score and is excluded from the
overall.

## Rules

1. Scores live in the `fable_roadmap.md` table and nowhere else. A score
   found in any other file is stale by definition — treat the table as the
   truth, and replace the stray number with a link to the table.
2. Scores move only at audits, and every move names its evidence and the
   anchor it supports.
3. If reality and a score diverge between audits, note it in RESUME. The
   score itself waits for the audit.
