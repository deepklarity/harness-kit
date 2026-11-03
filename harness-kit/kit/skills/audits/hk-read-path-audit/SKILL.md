---
name: hk-read-path-audit
description: "Audit the documents an agent reads for a job, as one HTML page scored out of 10: their token cost, what they state twice, where they contradict each other or the code, what is read and never used, each with the exact edit; applies the edits when asked. Triggers on: 'what does a session read', 'token cost of the docs', 'find contradictions', 'simplify the read path', or /hk-read-path-audit."
argument-hint: "[a folder, files, or a vague reference like 'the mission control convention'; add 'apply' to make the edits from the last page]"
allowed-tools: Bash, Read, Grep, Glob, Write, Edit, Agent
---

# Read path audit

The documents an agent reads before it does a job are its read path. This skill sizes that
path, finds what it says twice, where it disagrees with itself or with the code, and what it
loads for nothing, and writes the edits up worst first. It reads only, until the user says
apply. Code is `hk-code-quality-audit`'s; one skill is `skill-audit`'s; a transcript is
`audit-agent-efficiency`'s. This skill owns the documents.

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context is empty, audit what a session in this repo reads at start: the root
`CLAUDE.md` and every file it tells every session to read.

## Steps

**1. Resolve the reference to files.** A file is itself. A folder holding a `RESUME.md` is
that file plus every file its read order names, plus every file the nearest `CLAUDE.md`
tells every session to read. Any other folder is its documents. A vague reference ("the
mission control convention", "what a deployed project reads") is resolved by grepping
`CLAUDE.md`, `docs/playbook/` and `docs/skills/` for its words and taking the files that
define the thing and the files that point at them. Run

```bash
python3 <this skill's folder>/scripts/read_path.py <files>
```

and put the table in chat, one line saying what the set is, before reading further. Done
when the set is named in chat with its total.

**2. Read every file in the set, in the order an agent would.** Note, per file, which
sentences a later step of the job actually needs, which it never reaches, and which
sections are hand-written copies of something structured elsewhere on the path (a
settled-decisions list beside a board with decided rows, a status vocabulary beside the
script that defines it). Done when every file has been read once, whole, and those notes
exist for each.

**3. Look through four lenses.** The script's restated passages are the start of the first;
the rest is reading.

- **Restated.** The same rule or fact in more than one file, verbatim or in other words
  (the read order, a column list, "reread the tone doc first"). One copy is the source; the
  others become a pointer or go. The tenet is `docs/playbook/tenets.md`, one fact one place;
  the mechanics are `docs/skills/knowledge/matt-writing-for-agents/SKILL.md`, pruning.
- **Contradicted.** Two files that disagree, or a file that disagrees with reality: a named
  path, script, flag or command that does not exist (`test -e` every path; run every script
  with `--help`), a status stated two ways, a rule the code no longer follows. Where a
  convention already says which side wins (mission-control: the page wins, RESUME gets
  fixed), the finding names the loser.
- **Unused.** A file in the read order no step of the job needs; history loaded as if it
  were state; a paragraph restating what the environment already says (a directory
  listing, a command's own help). Progressive disclosure, tenet one: what only some runs
  need goes behind a pointer.
- **Shape.** A hand-written section that is a view over structured data, so it drifts:
  a decisions list that mirrors the decided rows of `board.csv`, a skill table that mirrors
  `skills.csv`. The edit is to generate it or delete it, never to shorten it. The same lens
  catches a generated file in the read order beside its source, a rendered page beside the
  csv it renders: the agent reads the source and checks the render is fresh, it does not
  read both. Size that one first; it is usually the largest saving on the path.

Done when each file has been held against all four lenses and every finding quotes its lines.

**4. Set severity and score.** Severity is 1 to 5, 5 worst, by what it costs the next session:

| Severity | Finding |
|---|---|
| 5 | two files an agent reads in one session that contradict each other; a named path, script or command that does not exist |
| 4 | one rule stated in three or more files; a hand-written section that mirrors structured data; a generated file read beside its source |
| 3 | one rule stated in two files; a file in the read order that no step uses |
| 2 | a paragraph that restates the environment; history loaded as state |
| 1 | a sentence that says what the agent does by default |

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

**5. Build the page.** Read `docs/playbook/tone_and_taste.md` and
`docs/skills/knowledge/hk-writeup/SKILL.md`, then copy
`docs/skills/knowledge/hk-writeup/SHELL.html` to
`$(git rev-parse --show-toplevel)/writeup/read-path-<slug of the reference>.html` and
replace only its `<!-- CONTENT -->` line and its `<title>`. The shell's style block stays as
it is, so this page looks like every other page under `writeup/`; a stylesheet of your own
is the wrong page. In order:

1. Kicker "Read path audit", the reference as the title, chips for the file count and the
   total tokens. The score is a sentence in the Overview, never a chip.
2. **Overview.** Two sentences: what job this read path serves, and the score with the
   sentence that derives it. Then the tokens now and the tokens after every edit below is
   applied, as one sentence. Then the file table from step 1 as a `.tbl` in `.tblwrap`, and
   the band table.
3. **Findings**, one `section` per lens, worst first inside it, one `details.row` each,
   numbered F1 onward with the number in a `<span class="sn">`: `.row-name` the severity in
   words, `.row-status` the title as a sentence. The body holds `file:start-end` in `.mono`
   for every place involved, the lines in a `pre`, one sentence on what it costs the next
   session, and the edit as an instruction someone could carry out without the page:
   which lines go, what sentence replaces them, which file keeps the source.
4. Footer: read by hand at the named commit.

**6. Report back** in chat: where the page went, the score, tokens now and after, and the
three edits that save the most. Done when that message is sent; the page is the whole
output until the user asks for the edits.

## Apply

When the argument ends with `apply`, or the user asks for the edits after a page exists:
read the page, make each edit in severity order with `Edit`, and after each one rerun the
script. State files are never edited for content: `board.csv` rows and `log.md` entries
are what happened, and a finding about them is a finding about the template that produced
them. Then add to the page's Overview one sentence with the tokens before and after as
measured, and to the footer the list of files changed. Report in chat the diff stat and the
measured saving. Nothing is committed.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line whose `score` is the score of the audited page.

## Example

- The session-start set: `CLAUDE.md`, the philosophy or tenets doc, the tone doc, plus the initiative's `RESUME.md`, `OBJECTIVE.md` and `board.csv` under `writeup/<initiative>/`.
- The convention files a vague reference usually lands on: `docs/playbook/mission-control.md`, the readiness folder's `RESUME.md`, the root `CLAUDE.md`.
- The generated view that already exists: `node docs/harnesskit/templates/mission-control/render-board.mjs writeup/<initiative>` turns `board.csv` into `mission-control.html`; a RESUME section that mirrors decided rows is the shape finding to expect.
