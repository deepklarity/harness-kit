---
name: hk-follow-breadcrumb
description: "Read docs/breadcrumb_analysis/ before exploring a flow and answer from it, citing file and function per hop. Triggers on: 'how does X work', 'where does X happen', 'where is this in the code', 'what files handle X', or /hk-follow-breadcrumb, and before spawning exploration subagents for a flow that crosses frontend, backend, worker or CLI."
argument-hint: "<what you're trying to understand, e.g. 'how does task execution work'>"
---

# /hk-follow-breadcrumb — Read the breadcrumb first

<exploration_context> $ARGUMENTS </exploration_context>

If the context is empty, take the question from the conversation.

## Step 1: Load the index

Read `docs/breadcrumb_analysis/_INDEX.md`. Two sections matter: the **Flows** table (one row per breadcrumb folder and what it traces) and **Quick navigation** (symptoms mapped straight to a DEBUG.md). A question phrased as a symptom starts in Quick navigation.

## Step 2: Match the question

Three outcomes.

**A. A breadcrumb covers the flow.** Start with its `FLOW.md`, the 30-second map of which file and function handles each hop; it usually answers "where does X happen". Open `DETAILS.md` when the question is how it works (data in and out, conditionals, side effects) and `DEBUG.md` when something is broken or needs testing (log locations, grep patterns, quick commands, env vars). A folder may hold only `FLOW.md`, or keep the three files inside numbered sub-flow folders; open what is there.

**B. A breadcrumb covers part of it.** Read the covered part, then explore only the gap:

```
The breadcrumb for <X> covers <these parts> of the question.
For <the rest> I'm reading the code directly.
```

**C. Nothing covers it.** Say so and explore the code as usual (grep, reads, haiku Explore subagents), ending with:

```
No breadcrumb covers this flow yet; `/hk-breadcrumb-creator <flow>` will add one.
```

## Step 3: Answer

Answer the question asked, not the breadcrumb's contents:

```
From the breadcrumb for <flow-name>:

<the answer, citing the files and functions the breadcrumb names>

Key files:
- `path/to/file.py` :: `function()` — <what it does here>

<if debugging> Quick commands from DEBUG.md:
<the relevant ones>
```

Every file, function or line the answer cites must be opened in this session before it is cited; the breadcrumb tells you where to look, it is not the evidence. Open each hop the answer leans on and confirm it against the code, and where the code contradicts the breadcrumb, answer from the code and name the stale breadcrumb line. A cited-but-unopened file is a leak, not a shortcut. If the breadcrumb names a file or function that no longer exists, verify that hop against the code, answer from the code, and say which lines of the breadcrumb are stale. If the workspace has a breadcrumb drift checker (CLAUDE.md, Debugging), run it — it lists drift across every DETAILS.md. Done when every hop in the answer cites a file and function that was opened this session.

To write a breadcrumb, use `/hk-breadcrumb-creator`; this skill only reads them.

## Example: in harness-kit

From the repo root, `node scripts/check_harness.mjs` (shipped in `kit/templates/scripts/`) scores breadcrumb coverage and accuracy. If the workspace has its own breadcrumb drift checker, run that too.
