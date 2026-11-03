---
name: skill-audit
description: "Audit a skill under docs/skills and write up, as one HTML page, whether an agent can run it as written: a verdict, a score out of 10, and ranked findings each with a severity out of 5 and the fix to apply. Applies the rewrite when asked. Triggers on: 'audit this skill', 'is this skill any good', 'review the skill', 'improve this skill', 'skill audit', or /skill-audit."
---

# Skill audit

Reads one skill and says whether an agent running it would do the work the skill promises, in
this repo, in the house voice. The verdict comes first, because the question is always "can I
trust this skill", and the rest of the page explains it. A skill that is short, plain and works
is a finished audit with no findings, and the page says so. A new skill, or an eval of one, is
`hk-skill-creator`.

**Run it on the strongest model in the session:** telling a no-op
sentence from a load-bearing one, or a sharp completion criterion from a vague one, is what a
cheaper model gets wrong, and every finding here is that call. If the reading must be
delegated, pass the top model explicitly.

Two files in this skill's base directory drive it, plus three docs elsewhere in the repo:

- `REFERENCE.md` — the seven lenses, the severity anchors, the checks that need a command, the
  word caps and the page order. Read it before writing a finding.
- `finding.example.json` — three findings in the working shape, every field inside its cap.
- `docs/skills/knowledge/matt-writing-for-agents/SKILL.md` and its `SKILL-MECHANICS.md` — how a
  document an agent consumes is written. The vocabulary on the page (pointer, ladder, completion
  criterion, leading word, no-op, cache, sediment) comes from there.
- `docs/skills/knowledge/hk-writeup/SKILL.md` and `SHELL.html` — what a page may carry and the
  shell it goes in.
- `docs/playbook/tone_and_taste.md` — the house voice. It governs every string on the page and
  every string the audited skill makes a person read.

## Workflow

**1. Name the skill and read all of it.** The directory under `docs/skills/<category>/<name>/`
(`.claude/skills/<name>` is a stub), every file in it, and every file it points at. Note the
`generated:` and `imported:` frontmatter: a generated skill's body is the live source, so a
promise the skill no longer keeps is a finding whose fix is in the body.

**2. Read the three docs above in full** before forming a view. Judging a skill against a
half-remembered rule is how audits drift.

**3. Read the skill through the seven lenses in `REFERENCE.md`, one at a time.** Purpose,
runnability, structure, voice, output contract, repo fit, overlap. Each lens has the questions
to ask and the checks that need a command (does the cited file exist, does the trigger collide
with another skill's, is the output path ignored). Run the commands.

**4. Write the findings.** One fact is one finding. Every finding quotes the literal line it is
about, says what an agent running the skill does wrong because of it, and gives a fix that can
be applied without a follow-up question. Severity 1 to 5 on the anchors in `REFERENCE.md`. A
finding you would have to run the skill to be sure of is `suspected` and caps at 3.

**5. Verify, then cut.** Reopen each cited line and check it says what the finding claims. Merge
duplicates. Drop anything below the bar of "a maintainer would change this". Count what you
dropped; the count goes on the page.

**6. Decide the verdict and the score.** The verdict is one of three from the table in
`REFERENCE.md`: *runs as written*, *needs a pass*, or *rewrite*. The score is out of 10, from
the band table there. The findings set both ceilings; say in one spoken sentence why.

**7. Build the page.** Read `docs/playbook/tone_and_taste.md`, drop the content into
`docs/skills/knowledge/hk-writeup/SHELL.html` in the order `REFERENCE.md` gives, and change
nothing else in the shell. Write it to
`$(git rev-parse --show-toplevel)/writeup/skill-audit-<skill-name>-<run>.html`, where `<run>` is one more than the highest existing `skill-audit-<name>-N.html`, or 1. The verdict,
the score and the severities are what the person asked for, so they go on the page as plain
sentences and plain tables.

**8. Report back** in chat: where the page went, the verdict, the score and its sentence, how
many findings at each severity, and anything you could not check.

**9. When asked to improve the skill, apply the rewrite and audit it again as a new run.**
Rewrite the skill's files in place under `docs/skills`, then refresh its stub with `python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category> --stub-only` so the harness loads the new description. Keep its name and its trigger words unless a finding
says otherwise. Then run steps 3 to 8 on the rewrite and write a second page. One page is one
audit of one version of the skill; it never carries a before and after. Name the pages by run:
`writeup/skill-audit-<skill-name>-1.html`, `-2.html`, and so on, and put the commit or the
change the page describes in the header chips. A rewrite that raises a finding the original did
not have is not done.

## Writing the findings

The skill under audit was written by a colleague, and the page is read by one. Every finding
says what an agent does because of a line and what to change, in the register of a code review
comment you would be comfortable receiving. Describe the line, never the person who wrote it.
"The skill tells the agent to run `compile_audit.py`, which is not in this repo, so the last
step fails every time" beats "dangling reference".

- Prefix inferred sentences with `[Assumption]`; a bare sentence is a fact you checked.
- Keep to the word caps in `REFERENCE.md`. A `why` that will not fit in 45 words is two
  findings or is not understood yet.
- Read the page out loud before publishing it. Any word you would not say to a teammate comes out.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- A skill generated from a preset names the preset in its frontmatter, and the audit reads that preset's description.
