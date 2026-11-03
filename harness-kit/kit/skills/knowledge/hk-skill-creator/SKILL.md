---
name: hk-skill-creator
description: "Create a skill in this repo's layout (the body under docs/skills plus the .claude/skills stub) or run evals on one: draft it, run it against a baseline on realistic prompts, judge the outputs, tighten the description. Triggers on: 'create a skill', 'new skill', 'make a skill for', 'skill eval', or /hk-skill-creator. An existing skill that reads badly is /skill-audit."
argument-hint: "[what the skill should do, or the path of the skill to eval]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent
---

# Create a skill, then prove it helps

A skill is a document another instance of Claude follows. This skill drafts one in this repo's layout, runs it against a baseline, and keeps improving it until the with-skill runs are the ones a person would pick.

## Context

<skill_context> $ARGUMENTS </skill_context>

Start at the step the user is at: no skill yet means step 1; an existing SKILL.md means step 5, after reading it and every file it points at. If the context is empty and there is no skill named, ask what the skill should do. A skill that exists and reads badly is `skill-audit`'s job; this skill evals one and creates new ones.

## Where a skill lives here

The skill is `docs/skills/<category>/<name>/` (categories: audits, reviews, development, debugging, git, knowledge; `docs/skills/README.md` lists them). Claude Code only discovers `.claude/skills/<name>/SKILL.md`, so each skill also has a stub there: the same frontmatter and a paragraph pointing at the real directory. Bundled files are named by their real path (`docs/skills/<category>/<name>/scripts/x.py`); `references/writing_guide.md` says why.

## Step 1: Capture intent

If the conversation already holds the workflow ("turn this into a skill"), take the tools, the step order, the corrections and the input and output shapes from it. Then resolve, asking only for what the conversation does not answer:

- What the skill makes and how the agent tells it is done.
- The phrases a person says when they want it.
- Two or three concrete input and output examples.

Spawn a haiku subagent to read `docs/skills/*/*/SKILL.md` (the stubs in `.claude/skills` carry only the description) and report which existing skills already cover part of the job or claim the same trigger phrases. A collision means one skill owns the phrase and the other drops it; decide which before drafting.

Done when the examples and the trigger phrases are written down and no existing skill owns the job.

## Step 2: Plan bundled files

For each example, list what the agent would do from scratch and what it would rewrite every time. A helper script every run would write goes in `scripts/`; reference the agent consults on demand goes in `references/`; templates the output is built from go in `assets/`. Reference material lives in a sibling file only when it is long enough to earn the pointer.

Done when each example has its list and every recurring helper is assigned to `scripts/`, `references/` or `assets/`, or ruled out.

## Step 3: Scaffold

```bash
python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category>
```

This writes `docs/skills/<category>/<name>/SKILL.md` with `scripts/` and `references/`, and the stub at `.claude/skills/<name>/SKILL.md`. Use the `hk-` prefix for skills built for this repo.

## Step 4: Write it

Read `docs/skills/knowledge/matt-writing-for-agents/SKILL.md` and its `SKILL-MECHANICS.md` first: they say how the description (the context pointer), the steps, the completion criteria and the reference are arranged. `references/writing_guide.md` beside this skill covers the harness mechanics: frontmatter keys, `$ARGUMENTS`, `!`command`` injection, `context: fork`.

Write bundled files first, delete the scaffold's unused directories, and write SKILL.md last so it can name them. What matters most:

- The description is one line under 400 characters, valid YAML in quotes: what the skill makes, then the trigger phrases, then `/name`. It is the only part of the skill loaded on every turn.
- Steps in order at the top, each ending where the agent can tell done from not done; reference below or behind a pointer.
- Every path, script and command the skill names exists at the repo root (`test -e`).
- A skill that makes a page builds it in `docs/skills/knowledge/hk-writeup/SHELL.html`, writes it to `writeup/<skill>-<subject>.html`, and puts on it only what was asked for. Voice comes from `docs/playbook/tone_and_taste.md`.
- The skill ends by reporting where its output went, never with a yes/no question.

## Step 5: Run it against a baseline

Write two or three prompts a real user would say, concrete and specific (file paths, column names, backstory), and save them with what each should produce as `evals.json` in the workspace, in the shape `scripts/run_evals.py` reads (its docstring shows it):

```
$SCRATCHPAD/<name>-workspace/        # the session scratchpad directory from the system prompt; nothing here is committed
├── evals.json
└── iteration-1/eval-<n>/{with_skill,without_skill}/
```

For each prompt, spawn two sonnet subagents in one message, so every run goes in parallel:

```
Execute this task following the skill at docs/skills/<category>/<name>/SKILL.md:
- Task: <prompt>
- Input files: <if any>
- Save outputs to: <workspace>/iteration-N/eval-<n>/with_skill/
```

```
Execute this task without reading any skill files:
- Task: <prompt>
- Save outputs to: <workspace>/iteration-N/eval-<n>/without_skill/
```

While they run, add the assertions each output must pass to `evals.json` as `checks`: `file_exists`, `contains`, `not_contains` (a placeholder string, a wrong filename). A check that needs an opinion ("the output is good") is a judgment call for step 6, not an assertion.

## Step 6: Judge the runs

Run the assertions:

```bash
python3 docs/skills/knowledge/hk-skill-creator/scripts/run_evals.py <workspace> iteration-N
```

It runs every check against both output directories and marks the checks that pass without the skill too, which measure nothing. Then read the outputs and the subagent transcripts, and put each prompt's with-skill and baseline outputs side by side in chat. Note results that vary between runs of the same prompt, and steps the skill made the agent spend time on for no gain in the output. Done when every prompt has a with-skill result, a baseline result and the assertion table.

## Step 7: Improve

Change the skill for the pattern behind the feedback, not for the one prompt that showed it: a fix that only works for the test prompt is overfitting. Explain the reason for a rule instead of writing a MUST; the agent generalises from a reason and not from a constraint. Cut every instruction the transcripts show doing no work. If every run wrote the same helper, bundle it in `scripts/`.

Rerun step 5 into `iteration-<N+1>/` and compare with the previous iteration. Stop when every assertion passes and, for each prompt, you would hand a colleague the with-skill output over the baseline; say which you picked and why in the report.

## Step 8: Validate

```bash
python3 docs/skills/knowledge/hk-skill-creator/scripts/validate_skill.py docs/skills/<category>/<name>
```

It checks the frontmatter, the name against the directory, leftover TODOs, the description length and trigger phrases, that bundled files are referenced and referenced files exist, and that the stub in `.claude/skills` carries the same frontmatter. Fix everything it prints; a clean run prints `Valid!` and no warnings.

## Step 9: Tighten the description

Write twenty queries: eight to ten that should trigger the skill (different phrasings, formal and casual, cases where the user does not name the skill, cases where another skill competes) and eight to ten near misses that share words but need something else. Each query is something a person would type, with detail. For each, decide from the description alone whether the skill would fire, adjust the description until it fires on the first set and not the second, then refresh the stub:

```bash
python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category> --stub-only
```

Skip this step only when the user said the description is settled.

## Done

Report the skill path, the stub path, the iteration count, and how the final with-skill runs compared with the baseline. Add the skill to the table in `docs/skills/README.md`. Nothing is committed unless the user asked.
