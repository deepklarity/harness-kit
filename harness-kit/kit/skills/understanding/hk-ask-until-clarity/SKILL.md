---
name: hk-ask-until-clarity
description: "Before starting a task that could be read two ways, ask the person numbered questions, each with a recommended answer, until the goal, what done looks like and what is out of scope are settled; then write the agreed brief down. Triggers on: 'ask until clear', 'make sure you understand first', 'what do you need from me', 'clarify before you start', or /hk-ask-until-clarity."
---

# Ask until clarity

Most wasted agent work starts from a request that meant one thing to the person and another to
the agent. This skill closes that gap before any work starts: it asks until the task can be
stated back in a way the person agrees with, and writes that statement down.

It is shorter than `/matt-grill-me` (under `docs/skills/development/`). Grill-me stress-tests a
design until it holds; this skill only makes a request unambiguous, and stops the moment it is.

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, the task is the one the person asked for last in this
conversation.

## Steps

### 1. Look before you ask

Read what the request points at: the files, the board row, the page, the error. A question the
workspace can answer (which file, which command, what the code does today) is never put to the
person; find the answer and state it as a fact in the next step.

### 2. Say what you understood

Write back, in three to five plain sentences: what you think the goal is, what you think done
looks like, and what you found in step 1. The person corrects this faster than they answer
questions, so it comes first.

### 3. Ask one round

List what is still ambiguous as numbered questions. Each question carries your recommended
answer, so the person can reply "yes to all" when you guessed right:

```
**Q1. <the question>** <one or two sentences of why it matters>
→ Recommended: <your answer>
```

Ask only what changes the work: the goal, what done means, what is out of scope, a constraint
(time, cost, a file that must not change), who the result is for. Three to five questions a
round, never more than seven. Then stop and wait.

### 4. Repeat until the brief passes the clarity test

After each answer, restate the brief. It passes when every line below has one reading:

- **Goal:** what the work is for, in one sentence.
- **Done when:** the check that proves it is finished.
- **Out of scope:** what this work will not touch.
- **Constraints:** anything that limits how it is done.
- **For:** who reads or uses the result.

If a line still reads two ways, that line is the next round. When all five pass, ask the person
to confirm the brief. Bare agreement on every round is a warning sign; say so and ask which
recommendation they would have changed.

### 5. Write the brief down

A confirmed brief for work that outlives this session goes into the initiative's `RESUME.md`
under "What this initiative is", or a new `writeup/<initiative>/` folder when there is none
(`docs/playbook/mission-control.md`). A brief for work done in this session stays in the chat.
A standing rule the person stated along the way ("always X") is written into the right durable
file in the same turn, as the root `CLAUDE.md` requires.

## Done when

The person has confirmed a brief whose five lines each have one reading, and it sits in
`RESUME.md` or the chat as step 5 says. No work on the task itself starts before that.
