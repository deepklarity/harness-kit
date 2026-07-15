# How to write a task brief

Every task on the board follows this shape, whether a human, the operator
session, or `odin plan` writes it. Read `docs/fable_roadmap/TONE.md` first.
The test: a person who has never seen the code reads the title out loud,
wants to know more, reads the brief once, and knows what to build, why,
and how they'll prove it. If any sentence sounds like a contract, a log
line, or an insider note, rewrite it.

Every heading below reads like a question the reader is already asking
when they land on the task — "what's wrong today", "where do I see it",
"how do I know it's done" — not a category label. If a heading needs a
glossary, it's the wrong heading.

## The shape

```
Title: one sentence you'd say to a teammate out loud, describing what
changes. No system slang — words like "reap", "materialize", or
"fingerprint" mean nothing to someone who doesn't live in the code; say
what actually happens instead ("retry the task after its trace goes
stale", not "reap the stale trace"). No codes or bucket labels in the
title body — a short id like W9.9 may sit at the end in parentheses as a
tag, never as part of the sentence.

## What is wrong today
Two to four sentences a person would say out loud — written so a
stranger reads the first sentence and wants to keep reading, not so a
teammate nods along. What hurts today, who hit it, what gets better.
Real numbers and real file names beat labels. Name the roadmap area in
passing if it helps ("this is trust work"), never as jargon the reader
must decode.

## What to do
Numbered steps, each one a decision or a piece of work. Point at real
files. Say what to reuse and what NOT to build. If the implementer must
choose between approaches, say the choice is theirs and ask them to write
down why.

## Where a human sees it
One or two sentences: which page, card, or modal shows the result, and
who notices it changed. If nobody outside the code sees this yet, say
that plainly instead of inventing an audience.

## Done means
Two or three checks someone can actually run or see. Not "works correctly"
— the exact command, the exact page, the exact number that must move.

## Prove it
What evidence to leave and where a human will see it. Every claim in
`.proof/task-<id>/proof.md` needs the raw output that proves it —
prose alone is not proof. `"tests pass"` needs the pytest tail, not
the sentence. `"page renders"` needs the render log or a screenshot.
A one-line docs task shouldn't need a wall of logs — the rule is
output for claims, not volume — but every claim still needs raw
output behind it. Screenshots for anything visual.
```

## Standing rules (identical in every brief — paste, don't rewrite)

```
## Standing rules
- Fix the cause, not the symptom. If your fix only covers this one case,
  look one level up.
- Write the failing test before the fix or feature.
- Put your evidence in .proof/task-<id>/proof.md inside your workspace and
  post a short comment pointing at it. Don't commit the .proof folder —
  the system uploads it to the task for you. Only capture a test run's
  output after the run has finished.
- You are already on your own branch in your own workspace. Don't create
  or switch branches, and don't touch anything outside your workspace.
- Never use git stash.
```

## What changes per task vs what never does

Always the same: the Standing rules block, the six-section shape, the
plain voice, headings phrased as questions. Varies with the task:
everything else — the why, the steps, the checks, the evidence. When
context needs more (a replay recipe, an API contract, a warning from a
past failure), it goes inside "What to do" as its own numbered step,
still in plain words.

## Why this exists

Nine waves of briefs drifted into insider shorthand ("Working protocol
(applies to every fable task)", bucket scores in parentheses, acceptance
written like legal text). The user read task 259 and called it gibberish.
He was right. A brief is a conversation with the person doing the work.

The same drift showed up in how the task page reads once a brief is
dispatched: a task page that shows a stale failure with the wrong next
step, repeats the same system message a dozen times, and lists a failure
as key/value fields instead of a sentence, fails the same test as a
gibberish brief — a person can't tell what happened or what to do next.
The fix is upstream, in the templates every future brief is generated
from: plain titles, question headings, and a section that says in plain
words where a human will actually see the result.
