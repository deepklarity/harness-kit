---
name: hk-explain
description: "Build one page that helps a person understand something in this workspace (a change, a flow, a module, a decision) in the style they pick: a one-page summary, a diagram walkthrough, a step-by-step deck or a code tour; every claim cites the file it came from. Triggers on: 'explain this to me', 'help me understand', 'walk me through', 'make me an explainer', or /hk-explain."
---

# Explain

Agents change code faster than people can follow it. This skill turns one thing a person needs
to understand into a page they can read in a few minutes, in the style that suits them, with
every claim traceable to the code.

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, ask what the person wants to understand, in one question.

## Steps

### 1. Settle the question and the reader

Write down the one question the page answers ("how does an upload reach the database?", "what
did this branch change and why?") and who is reading it (the owner, a new joiner, a reviewer).
If either is unclear, ask once, with a recommended answer.

### 2. Pick the style

Offer the four styles with a recommendation for this question, and use the person's choice;
with no answer, use the one-page summary.

| Style | Suits | Shape |
|---|---|---|
| One-page summary | a decision, a change, a status | the answer first, then the evidence in short sections |
| Diagram walkthrough | a flow across files or repos | one inline SVG of the hops, then one short paragraph per hop |
| Step deck | teaching something in order | five to ten full-width sections, one idea each, readable by scrolling |
| Code tour | a module or a diff | the files in reading order, each with the lines that matter and what they do |

### 3. Gather from the source

Read the code, the breadcrumb (`docs/breadcrumb_analysis/_INDEX.md` first), the board and the
diff. Delegate wide reading to cheap subagents as the root `CLAUDE.md` says. Every claim on the
page needs a source you opened: `file:line`, a command and its output, or a board row by title.
A claim without one is cut, not softened.

### 4. Build the page

Write it in the house voice (`docs/playbook/tone_and_taste.md`) inside the shell and rules of
`/hk-writeup` (`docs/skills/knowledge/hk-writeup/SKILL.md`), to `writeup/explain-<slug>.html`.
The first screen answers the question; the rest is evidence. Sources sit beside the claims in
small mono, never in a list at the end.

### 5. Offer the check

Say where the page is in one line, and offer `/hk-quiz` on it for a person who wants to check
they have understood it.

## Done when

`writeup/explain-<slug>.html` exists, its first screen answers the question from step 1, and
every claim on it names the source it came from.
