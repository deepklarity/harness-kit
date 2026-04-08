---
name: hk-skill-creator
description: "Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy. Triggers on: 'create a skill', 'new skill', 'make a skill for', 'improve this skill', 'test this skill', 'skill eval', 'optimize skill description', or /hk-skill-creator."
argument-hint: "[optional: what the skill should do, or path to existing skill to improve]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, Task, TaskCreate, TaskUpdate
---

# /hk-skill-creator — Create and Improve Skills

Create new skills and iteratively improve existing ones through a structured draft-test-evaluate-improve loop.

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above describes what skill to create or improve, proceed. If empty or unclear, ask the user what they want the skill to do.

## The Core Loop

The process of creating a skill:

1. **Understand** what the skill should do, with concrete examples
2. **Draft** the SKILL.md and any bundled resources
3. **Test** by running Claude-with-the-skill on realistic prompts
4. **Evaluate** outputs qualitatively (does it do what the user wants?) and quantitatively (assertions)
5. **Improve** based on feedback — generalize, don't overfit
6. **Repeat** until the user is satisfied
7. **Optimize** the description for reliable triggering

Figure out where the user is in this process and help them progress. Maybe they want a skill from scratch — help narrow intent, draft, test. Maybe they already have a draft — go straight to eval/iterate. Maybe they just want to vibe — be flexible.

## Communication Style

Skills are used by people across a wide range of technical familiarity. Pay attention to context cues:

- "evaluation" and "benchmark" are borderline but OK to use without explanation
- For "JSON", "assertion", "frontmatter" — look for cues the user knows these before using them without a brief definition
- When in doubt, briefly explain terms inline
- Match the user's register — if they're casual, be casual back

## Step 1: Capture Intent

Start by understanding what the skill should enable. The conversation might already contain a workflow to capture (e.g., "turn this into a skill"). If so, extract answers from context first — tools used, sequence of steps, corrections made, input/output formats. The user fills gaps, then confirms before proceeding.

Questions to resolve:
- What should this skill enable Claude to do?
- When should this skill trigger? (what user phrases/contexts)
- What's the expected output format or behavior?
- Are there concrete examples of input/output?

### Interview and Research

Proactively ask about edge cases, input/output formats, example files, success criteria, and dependencies. Check existing skills in `.claude/skills/` — spawn a haiku subagent to scan for overlapping or related skills. Come prepared with context to reduce burden on the user.

Do not proceed to drafting until the functionality is clearly understood.

## Step 2: Plan Resources

For each concrete example from Step 1, analyze:
1. How to execute from scratch (what steps, what tools)
2. What scripts, references, or assets would help when executing repeatedly

Build a list of reusable resources to include. Common signals:
- If all test cases would independently write similar helper scripts, bundle that script in `scripts/`
- If the skill needs detailed reference docs (API schemas, large examples), put them in `references/`
- If the skill produces output from templates, put templates in `assets/`

## Step 3: Initialize or Locate the Skill

### New skill

Scaffold with:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/init_skill.py <skill-name>
```

This creates the directory structure at `.claude/skills/<skill-name>/` with a template SKILL.md. Follow `hk-` prefix convention for this project.

### Existing skill

Read the existing SKILL.md and understand its current structure before making changes.

## Step 4: Write the Skill

The skill is written for another instance of Claude to use. Focus on non-obvious procedural knowledge — things the model wouldn't know or would get wrong without guidance.

### Implementation order

1. Bundled resources first (scripts/, references/, assets/)
2. Delete unused example files from initialization
3. Complete SKILL.md last (it references the resources)

### SKILL.md Structure

Read `references/writing_guide.md` for the complete writing guide including all frontmatter fields, dynamic context injection, and subagent execution patterns. Key points:

**Frontmatter** — The `description` is the primary triggering mechanism. Descriptions longer than 250 characters get truncated in the skill listing, so front-load the key use case. Include both what the skill does AND specific trigger contexts. Descriptions should be slightly "pushy" to combat undertriggering.

**Invocation control** — Two fields control who can trigger a skill:
- `disable-model-invocation: true` — only the user can invoke via `/name` (use for side-effect workflows like deploy, commit)
- `user-invocable: false` — only Claude can invoke (use for background knowledge, not actionable as a command)

**Subagent execution** — Set `context: fork` to run the skill in an isolated subagent. Pair with `agent:` to pick the agent type (`Explore`, `Plan`, `general-purpose`, or a custom agent from `.claude/agents/`). Only use for task-oriented skills with explicit instructions — guidelines-only skills produce nothing useful in a fork because the subagent has no conversation context.

**Dynamic context injection** — Use `` !`command` `` syntax to run shell commands before the skill content reaches Claude. The output replaces the placeholder. Useful for injecting live data (git status, API state, file listings).

**String substitutions** — `$ARGUMENTS` for all args, `$ARGUMENTS[N]` or `$N` for positional access, `${CLAUDE_SESSION_ID}` for session ID, `${CLAUDE_SKILL_DIR}` for the skill's directory path.

**Body** — Use imperative form. Explain *why* things matter, not just *what* to do. Today's LLMs are smart — when given reasoning they can generalize beyond rote instructions. If you find yourself writing ALWAYS or NEVER in all caps, reframe with reasoning instead. Keep under 500 lines; move detailed content to `references/`.

**Conventions for this project:**
- Follow patterns in existing `.claude/skills/hk-*` skills
- Include `$ARGUMENTS` context block if the skill accepts arguments
- Use `argument-hint:` in frontmatter to show expected arguments
- Use `${CLAUDE_SKILL_DIR}` to reference bundled scripts/files portably
- Reference all bundled resources explicitly with guidance on when to read them

## Step 5: Test the Skill

### Create test prompts

Come up with 2-3 realistic test prompts — the kind of thing a real user would actually say. Not abstract requests, but concrete and specific with detail (file paths, personal context, column names, backstory). Share with the user for approval before running.

Save test cases to a workspace directory:

```
<skill-name>-workspace/
├── evals.json              # Test prompts and expected behaviors
├── iteration-1/
│   ├── eval-<name>/
│   │   ├── with_skill/     # Output from run with skill
│   │   └── without_skill/  # Baseline output (no skill)
│   └── ...
└── iteration-2/
    └── ...
```

### Run test cases

For each test case, spawn two subagents in the **same message** — one with the skill loaded, one without (baseline). Launch all runs at once for parallel execution.

**With-skill run** (sonnet subagent):
```
Execute this task following the skill at <path-to-skill>/SKILL.md:
- Task: <eval prompt>
- Input files: <if any>
- Save outputs to: <workspace>/iteration-N/eval-<name>/with_skill/
```

**Baseline run** (sonnet subagent, same prompt, no skill):
```
Execute this task WITHOUT reading any skill files:
- Task: <eval prompt>
- Save outputs to: <workspace>/iteration-N/eval-<name>/without_skill/
```

### While runs execute, draft assertions

Use the time productively. Draft quantitative assertions for each test case — objectively verifiable checks with descriptive names. Explain to the user what the assertions check.

Good assertions: "output_file_exists", "contains_required_sections", "no_placeholder_text", "follows_naming_convention"
Bad assertions: "output_is_good", "looks_right" (subjective — evaluate qualitatively instead)

## Step 6: Evaluate Results

When runs complete:

1. **Grade each run** — check assertions programmatically where possible (write and run a script), use judgment for the rest
2. **Present to user** — show each test case's prompt, skill output, and baseline output side by side. Ask for feedback on each.
3. **Analyze patterns** — look for assertions that always pass regardless of skill (non-discriminating), high-variance results (flaky), and time/token tradeoffs

Empty feedback from the user means they thought it was fine. Focus improvements on test cases with specific complaints.

## Step 7: Improve the Skill

This is the heart of the loop. Principles for making improvements:

**Generalize from feedback.** The skill will be used across many prompts — if a fix only works for the specific test case, it's overfitting. Rather than fiddly overfit changes or oppressive MUSTs, try different metaphors or recommend different working patterns.

**Keep the prompt lean.** Remove things that aren't pulling their weight. Read the subagent transcripts, not just final outputs — if the skill makes the model waste time on unproductive steps, cut those instructions.

**Explain the why.** Try to transmit understanding, not just rules. The model has good theory of mind and can go beyond rote instructions when it understands the reasoning.

**Look for repeated work.** If all test runs independently wrote similar helper scripts or took the same multi-step approach, that's a signal to bundle that script in `scripts/`.

**Draft, then review.** Write a revision, then look at it with fresh eyes and improve before committing.

After improving: rerun all test cases into a new `iteration-<N+1>/` directory, present results with previous iteration for comparison, get feedback, repeat.

## Step 8: Validate

Run the validator before finalizing:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/validate_skill.py .claude/skills/<skill-name>
```

This checks structure, frontmatter, naming conventions, placeholder detection, resource references, and description quality.

## Step 9: Optimize Description (Optional)

After the skill is working well, offer to optimize the description for better triggering accuracy.

### Generate trigger eval queries

Create 20 eval queries — a mix of should-trigger (8-10) and should-not-trigger (8-10).

**Should-trigger queries**: Different phrasings of the same intent — formal and casual. Include cases where the user doesn't name the skill explicitly but clearly needs it. Cover uncommon use cases and cases where this skill competes with another.

**Should-not-trigger queries**: Focus on near-misses — queries that share keywords but need something different. Adjacent domains, ambiguous phrasing where keyword matching would trigger but shouldn't. Avoid obviously irrelevant queries ("write a fibonacci function" as negative for a PDF skill tests nothing).

Queries must be realistic — concrete, specific, with detail. Not "Format this data" but "ok so my boss sent me this xlsx and she wants me to add a profit margin column, revenue is in C and costs in D".

### Test triggering

For each query, test whether Claude would trigger the skill by examining the description match. Adjust the description to improve accuracy on the eval set, being careful not to overfit.

### Apply

Update the SKILL.md frontmatter with the optimized description. Show the user before/after.

## Anti-patterns

- **Testing your own skill** — if you wrote the skill and you're also running it with full context, the test is compromised. Use separate subagents that don't share context with the drafting process.
- **Skipping baseline runs** — without a baseline, you can't tell if the skill actually helped or if Claude would have done fine without it.
- **Overfitting to test cases** — if the skill only works for 3 specific prompts, it's useless at scale. Generalize.
- **MUST/NEVER overload** — rigid constraints are a code smell in skills. Explain reasoning instead.
- **Skipping user review** — always get human feedback on outputs before revising. The user sees things you can't.
