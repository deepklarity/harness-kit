---
name: hk-quiz
description: "Check that a person understands a change, a flow or a decision by asking them questions one round at a time, grading each answer against the code, and explaining every miss with the file that shows it; ends with what is understood and what to reread. Triggers on: 'quiz me', 'test my understanding', 'do I get this', 'check I understood the change', or /hk-quiz."
---

# Quiz

An owner who approves work they do not understand is not really in the loop. This skill checks
understanding the way a good reviewer would: questions about what changed, why, and what would
break, graded against the code rather than against the agent's memory of it.

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, the subject is the last change, page or decision in this
conversation; say which one you picked.

## Steps

### 1. Build the answer key first

Read the subject: the diff, the breadcrumb, the `/hk-explain` page, the board rows. Write six to
eight questions, each with the answer and the `file:line` or command that proves it. Keep the
key to yourself. Questions come from four kinds, at least one of each:

- **What:** what changed or what the thing does.
- **Why:** the reason behind a decision, from the board, the commit or the pattern it follows.
- **What if:** what breaks, or what a user sees, when a part fails.
- **Where:** where a person would look to debug or extend it.

No trivia: never a variable name, a line count or anything a person would look up rather than
know.

### 2. Ask in rounds

Ask two or three questions at a time, open-ended, never multiple choice, and wait. Do not hint
before the answer.

### 3. Grade against the code

For each answer, say right, partly right or wrong in one word, then one sentence on what the
code actually does with its source. A miss is explained, not repeated as a new question.

### 4. Close with the gaps

After the last round, write three short lines: what the person clearly understands, what they
missed, and the one file or page to reread for each miss. When the misses point at a document
that says something the code does not, that is a finding: add a row to the initiative's board,
or a line in `writeup/agent-readiness/kit-notes.md` when the document came from the kit.

## Done when

Every question has been asked and graded with its source, and the person has the three closing
lines. Nothing is written to disk unless a gap turned into a board row.
