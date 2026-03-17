---
name: hk-compound
description: "Compound a learning into a reusable pattern. Use after solving a non-trivial problem, discovering a principle worth reusing, or hitting a gotcha that would bite someone else. Triggers on: 'document this', 'capture this learning', 'that was tricky', 'let's compound this', 'compound this', or /hk-compound."
argument-hint: "[optional: brief context about what to capture]"
allowed-tools: Bash, Read, Edit, Write, Task, Grep, Glob
---

# /hk-compound — Compound a Pattern

Extract the transferable principle from what just happened and add it to the pattern library. The goal is not to document an incident — it's to name a reusable pattern that gets richer over time as more instances accumulate under it.

## The unit: a pattern

A pattern is a named, transferable principle. It starts lean — a clear statement and one instance. It grows as you encounter more cases. The value compounds because each new instance sharpens the principle and makes it easier to recognize next time.

This is not a post-mortem filing system. Don't capture "what happened." Capture "what's the reusable insight, and how would I recognize this situation again?"

## When to use

- You solved something and the *principle* behind the fix applies beyond this specific case
- You noticed a recurring shape across multiple situations
- You made a trade-off decision worth remembering the reasoning for
- You hit a gotcha that has a recognizable signal before you fall into it

Skip it for: one-off fixes with no transferable insight, trivial config, things that are obvious once you know them.

## Context hint

<context_hint> #$ARGUMENTS </context_hint>

If the context hint above is empty, scan the conversation for the most significant learning — the thing that took the most investigation or had the most non-obvious outcome. Focus on extracting the transferable principle, not recapping the incident.

## Process

### Step 1: Extract the pattern

Spawn a haiku subagent to analyze the conversation. The subagent returns text only — no file writes.

```
Task(model: haiku, subagent_type: general-purpose)

Prompt: "You have access to the full conversation context. Extract:

1. PATTERN NAME: A short, memorable name for the principle (e.g., 'Verify Preconditions Before Flags', 'Information Density via Progressive Disclosure'). Not a description of what happened — a name for the reusable idea.

2. THE PRINCIPLE: 1-3 sentences. The transferable insight stated clearly enough that someone unfamiliar with the specific situation would understand and be able to apply it. No jargon without explanation.

3. WHY IT MATTERS: 1-2 sentences. What goes wrong when you ignore this? What do you gain by following it? Be concrete.

4. THE SIGNAL: How would you recognize that this pattern applies? What does the situation look like right before you need this? (If unclear from a single instance, say so — this can be added later.)

5. THIS INSTANCE: A brief description of what happened in this conversation that surfaced the pattern. Include specifics — error messages, file paths, code snippets — but keep it to a few lines. Date it.

6. TAGS: 3-5 lowercase hyphenated keywords for grep discovery.

Be concrete and specific. No filler. If you can't articulate a transferable principle — if it's just 'we fixed X by doing Y' with no broader lesson — say so."
```

If the subagent can't find a transferable principle, tell the user and skip. Not everything needs to be compounded.

### Step 2: Check for existing patterns

Search `docs/patterns/` for related patterns:

```
Grep: pattern="<keywords from tags and pattern name>" path=docs/patterns/ output_mode=files_with_matches -i=true
```

Three outcomes:

- **Existing pattern matches**: This is a new instance of that pattern. Go to Step 3a.
- **Related but different**: Create a new pattern. Mention the related one in the doc.
- **Nothing related**: Create a new pattern.

### Step 3a: Add instance to existing pattern

Read the existing pattern file. Append the new instance under `## Instances`. If the new instance sharpens the principle or clarifies the signal, update those sections too — patterns are living docs.

Then skip to Step 5.

### Step 3b: Create new pattern

**File path**: `docs/patterns/<slugified-pattern-name>.md`

No dates in filenames — patterns are living documents, not snapshots. Keep slugs short and descriptive.

```bash
mkdir -p docs/patterns/
```

Write the file using the structure below.

### Step 4: Write the pattern doc

```markdown
tags: tag-one, tag-two, tag-three

# <Pattern Name>

<The principle. 1-3 clear sentences. This is the part someone would quote in a PR review or bring up in a design discussion. State it plainly — if a smart person reading this for the first time can't immediately understand what you mean, rewrite it.>

## Why it matters

<What goes wrong without this. What you gain with it. Keep it concrete — not "improves code quality" but "you'll spend 20 minutes debugging a misleading error message.">

## The signal

<How to recognize you're in this situation. What it looks like right before this pattern is relevant. This is what trains future-you to spot it early.>

## Instances

- **YYYY-MM-DD / short context label**: What happened. Specifics: error messages, the fix, file paths, code if relevant. A few lines per instance — enough to jog memory, not a full writeup.
```

**Adapting the structure:**
- Every pattern needs the principle statement and at least one instance.
- "Why it matters" and "The signal" are nudges, not mandates. If you only have one instance and the signal isn't clear yet, skip "The signal" — add it when a second instance clarifies the shape.
- If the pattern benefits from a code example, add one inline. Don't force it.
- If two patterns are related, mention each other with a simple `see also: docs/patterns/other-pattern.md` line.

### Step 5: Present result

After writing or updating the file:

```
Done — docs/patterns/<filename>.md

<1-2 sentence summary: the pattern name and the core insight>
```

If you updated an existing pattern, mention that it now has N instances.

## Quality bar

A good pattern doc passes this test: **Could a teammate read just the pattern name and principle statement, and immediately know (a) what the idea is and (b) when to apply it?**

Signals of a good pattern:
- Pattern name is memorable and self-explanatory
- Principle statement is quotable — you'd paste it in a code review
- Instances are specific enough to jog memory but short enough to scan
- Tags surface it when someone greps for the general area

Signals of a bad one:
- It's really just a how-to for one specific situation (no transferable principle)
- The principle is vague ("be careful with X") instead of actionable
- It reads like a post-mortem instead of a reusable pattern
- Filler phrases: "It's worth noting that...", "This approach ensures..."
