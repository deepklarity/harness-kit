---
name: hk-pattern-check
description: "Check docs/patterns/ for learnings that apply to the current problem and relay the ones that fit in under 200 words. Triggers on: 'think about what's wrong', 'this isn't right', 'we're going in circles', 'analyze deeper', or /hk-pattern-check. Use unprompted when you have tried the same fix twice, are about to bypass a parent method, or are reaching for a workaround."
argument-hint: "[optional: what's not working]"
---

# /hk-pattern-check — Pattern library check

Reads `docs/patterns/` through a haiku subagent and relays the patterns that apply, so the main conversation pays for the answer and not for every file.

<problem_context> $ARGUMENTS </problem_context>

## Step 1: Name the problem

Take the problem from the context above or, if it is empty, from the conversation: what is being attempted, what is failing or misaligning, what has been tried. Write it as one or two sentences; that is the search context.

## Step 2: Read the patterns through haiku

Spawn one subagent so the main conversation does not read every pattern file:

```
Agent(model: haiku, subagent_type: Explore)

Prompt: "Read all markdown files in docs/patterns/. For each pattern, decide whether it bears on this problem:

[problem summary from Step 1]

For each relevant pattern (skip the rest), return only:
- Pattern name (from the # heading)
- The core principle (1 sentence, from the file)
- How it applies here (1 sentence, specific to this problem)

If none apply, say 'No patterns match.'
Keep the whole response under 200 words."
```

## Step 3: Relay

Post the subagent's answer in this shape, without adding to it:

```
Pattern check for: [problem summary]

- **[Pattern name]**: [principle]. Applied here: [specific advice].
```

If nothing matched: "No patterns match this situation. Once it is solved, append the learning to the matching file in `docs/patterns/` if it transfers (append, never fork)." Done when the relay is in chat.
