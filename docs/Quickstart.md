# Harness-Kit Quickstart

You are probably a coding agent — Claude Code, Codex, or anything else — and
a human just told you something like: "Read the Quickstart at
github.com/deepklarity/harness-kit and help me incorporate the good
practices and skills into my repo."

This page is your instructions. Follow it top to bottom. Everything here
works the same whatever agent you are — nothing depends on a specific model
or CLI.

## What harness-kit is

A toolkit for humans and AI building software together. It has two parts:

1. **Practices, skills, and presets** you can copy into any repo and adapt.
   That's what this page installs. The ideas behind them are in
   [`../odin/docs/philosophy.md`](../odin/docs/philosophy.md) — 20 tenets,
   worth reading before you audit.
2. **The full kit** — a task board (taskit) plus an orchestration CLI (odin)
   that runs agents against the board. Optional, separate track: the root
   [`QUICKSTART.md`](../QUICKSTART.md) covers installing and running it.

Most people want part 1 first. That's the rest of this page.

## Hard rules, before anything else

1. **The audit is read-only.** Never execute anything in the target repo —
   no tests, no package managers, no builds, no scripts, and no git
   commands. You can't know how they're wired or what they touch. Evidence
   comes from reading files and listing directories, nothing else.
2. **You write in exactly one place: `pattern-engineering-audit/`.** During
   the audit, not one project file changes. Not `.gitignore` (propose the
   line in the report instead), not formatting, not a "harmless" typo fix,
   nothing. Other files change only in step 3, only the ones the human said
   yes to.
3. **What you can't verify by reading, you report as "not verified"** with
   the exact command for the human to run themselves. Never run it for them.
4. **Never read secret files.** `.env` and its variants, key files, pem
   files, credential configs — check they exist from the directory listing,
   never open their contents, never quote them in evidence. A secret that
   passed through your context is a secret that leaked.

## The flow: audit, report, decide, adopt, re-score

You don't start by copying files. You start by measuring the repo you're in,
so the human can decide what's worth adopting and you both can see the
score move afterward.

### Step 0 — Set up the working folder

Create `pattern-engineering-audit/` at the root of the repo you're improving. Don't
edit their `.gitignore` — propose the `pattern-engineering-audit/` line in the
report's decision list instead. If the target is a workspace holding
several repos, put the folder at the workspace root and audit each active
repo. Everything you do lives in this folder, so any session — yours or a
future one — can resume from disk without re-reading a chat history.

```
pattern-engineering-audit/
  tracker.md        # where are we, what was decided, what's next — update as you go
  report.md         # the audit report (step 2 output)
  report.html       # the same audit, curated as a single page for human review (step 2 output)
  action-items.md   # the decision list as an editable checklist — the human marks it up (step 2 output)
  evidence/         # file excerpts and listings the report cites
```

`tracker.md` is the resume point. First thing you write, last thing you
update before stopping. If it already exists, read it and continue from
where it says — don't start over.

The tracker also carries a **cost log**. After each step, append a line
with your best approximation of what the step cost so far: model used,
tokens if your harness shows them, otherwise turns and tool calls as a
proxy. Be explicit that it's an estimate and say how you estimated it. The
total goes in both reports — the human should see the price of the loop,
not just the score.

### Step 1 — Audit the repo

Work through [`adoption/CHECKLIST.md`](adoption/CHECKLIST.md) — eleven
areas, each with an explanation of what it means and what to look for. Gather
evidence by reading files and listing directories only (hard rule 1), and
save what each score cites — file excerpts, listings — into `evidence/`.
Checks that can only be settled by running something go in the report's
"not verified" list with the command for the human.

One check worth calling out: does the repo have an agent entrypoint file —
a `CLAUDE.md`, an `AGENTS.md`, or equivalent? Either is fine; which one is
the team's choice. It's a finding only when none exists, or when two exist
and disagree. You, reading this, might have been launched from any CLI —
a repo is agent-ready when any agent that lands in it finds guidance.

### Step 2 — Write the report

Write `pattern-engineering-audit/report.md`. This is for the human, and it has one
job: let them decide quickly. Rules:

- **Lead with what's broken or missing.** Never open with what's fine.
- **Score each of the eleven areas 0–10, honestly.** A 3 that's true beats
  a 7 that flatters. Every score cites its evidence file. Per area, give
  three things that are good and three that most need improvement —
  concrete, with file names (fewer than three? say so, don't pad).
- **One decision list at the end.** Each item: what to adopt, why it matters
  for THIS repo, effort (small/medium/large), and your recommendation.
  The human should be able to answer each with yes/no/later.
- **Write like you talk.** Simple words, short sentences, real file names
  and real numbers. No "leverage", no "utilize", no report-speak. If a
  sentence sounds like a contract, rewrite it. A person who wasn't there
  should understand each finding from the sentence itself.
- **The verdict is one short sentence — 15 words or fewer.** It's set
  large in the HTML; detail belongs in the findings.
- **Use real names, never invented abbreviations.** If the repo is called
  `document-extraction`, write `document-extraction` every time — never
  shorten it to "DX". The reader shouldn't need a glossary you made up.
- **Every line stands alone.** Score rows, strengths, and to-improve
  bullets get read in isolation — never write "(finding 3)" or "(see
  above)". Repeat the two words of context instead.
- **Remember the boundaries while writing, not just scoring:** CLAUDE.md
  vs AGENTS.md is the team's choice — a repo covered by any entrypoint
  (including one at the workspace root) is covered, and "no AGENTS.md" is
  never written up as a gap. CI belongs to its own area only — it never
  appears in another area's why-line, the verdict, or the top 5.

Shape (keep it under ~120 lines):

```
# Adoption audit — <repo name>

**Verdict:** <one line — the single most important thing>
**This audit cost:** <estimate + how you estimated it, from the tracker's cost log>

## What's broken or missing        (worst first, each with evidence)
## Not verified                    (what needs running, with the exact command for the human)
## Scores                          (table: area | score /10 | evidence | one-line why)
## Per area: good and to improve   (each area: 3 good, 3 to improve — real file names)
## Top 5 to work on                (ranked by impact, across all areas)
## Decisions for you               (numbered list, each answerable yes/no/later)
```

Alongside the report, write `pattern-engineering-audit/action-items.md` —
the decision list as a checklist the human edits by hand. One entry per
decision:

```
## 1. <short title>
- [ ] adopt   (check to say yes; write "later" or "no" after the box to defer/decline)
What: <one plain sentence>
Why: <why it matters for this repo>
Effort: small|medium|large    Recommendation: yes|later
Notes:
  (the human writes anything here — scope limits, preferences, questions)
```

This file is the handoff: the human checks boxes and writes notes inline,
and their edited version is what starts step 3. Don't put decisions
anywhere else that isn't also here.

Then render the same content as `pattern-engineering-audit/report.html` —
a single self-contained page the human opens in a browser to review the
whole audit. **Don't design it from scratch, and don't rebuild the file by hand — it
embeds ~60KB of base64 fonts and shell heredocs will break on escaping.**
Work with files:

1. `cp` [`adoption/report-template.html`](adoption/report-template.html)
   to `pattern-engineering-audit/report.html`.
2. Write your filled-in body — everything between the template's
   `BODY:BEGIN` / `BODY:END` marker comments, from `<div class="wrap">` to
   its closing `</div>` — to `pattern-engineering-audit/report-body.html`
   with your normal file-writing tool. Never assemble HTML inside a shell
   heredoc.
3. Splice it in (run inside `pattern-engineering-audit/`):

```
python3 -c "
import re
t=open('report.html').read(); b=open('report-body.html').read()
m=re.search(r'(?s)(<'+'!-- BODY:BEGIN -->).*(<'+'!-- BODY:END -->)',t)
open('report.html','w').write(t[:m.start(1)]+m.group(1)+'\n'+b+'\n'+m.group(2)+t[m.end(2):])"
```

4. Delete `report-body.html`, open `report.html`, and confirm no
   `{{TOKEN}}` is left and the fonts still render.

In the body, replace every `{{TOKEN}}` with real content from `report.md`,
mirror the template's worked-example classes for all eleven areas, and
keep the instruction comments out of the final file. `report.md` is the
source of truth; the HTML is its curated presentation. The rules the
template already embodies:

- **One file, no external anything.** Inline all CSS. No CDN fonts, no
  scripts it doesn't need. It must render from disk, offline.
- **Built for review, top to bottom:** verdict first, big and readable,
  with the audit's cost right under it. Then the score table with the
  worst areas visually loudest — a score is a signal, treat 0–3 / 4–6 /
  7–10 as three visibly different states. Then what's broken, then "not
  verified" with copyable commands, then per-area good/to-improve (3 + 3),
  then the top 5, then the decision list — each decision a row the human
  can answer yes/no/later at a glance, with effort and your recommendation
  right there. Every section from report.md appears — the HTML is the full
  report, not a teaser. The decisions section tells the human exactly how
  to act: "open pattern-engineering-audit/action-items.md, check what you
  want, add notes, then tell your agent to continue."
- **Evidence stays one click away, not in the way.** Put excerpts in
  collapsed `<details>` blocks under the finding they support.
- **Design it properly.** Real typographic hierarchy, room to breathe,
  readable line lengths, works in light and dark. The page should feel
  like a considered report, not a dumped table.

Stop here and show the human both reports. Don't adopt anything they
haven't seen scored.

### Step 3 — Adopt what the human picked

Step 3 starts when the human says continue. Read their edited
`action-items.md` first — checked boxes and their notes are the work
order; their notes override your recommendations. Anything unchecked or
marked "later"/"no" stays untouched. Record what you're acting on in
`tracker.md`, then work the list. What's available
to take, in rough order of value-per-effort:

| What | Where it lives | How to adapt |
|---|---|---|
| Agent entrypoint file | see `CLAUDE.md` here as a shape reference | Write the target repo's own — commands, structure, conventions. Mirror the same content into `AGENTS.md` so every agent CLI finds it. |
| Portable skills | `.claude/skills/` — the portable ones: `hk-rca`, `hk-compound`, `hk-mock-first`, `hk-refine`, `hk-slop-audit`, `hk-autonomy-audit`, `hk-arch-audit`, `hk-skill-creator`, `hk-scout-wiki`, `hk-shift-changelog`, `hk-follow-breadcrumb` + `hk-breadcrumb-creator` (adapt paths) | Copy `SKILL.md`, replace this repo's paths with the target's. Skills are just markdown procedures — an agent without a skill system can follow them as docs. |
| Task prompt presets | `taskit/taskit-backend/data/task_presets.json` — 27 prompts across code-review, ui-ux-audit, documentation, analysis, quality-process, development | They're plain prompt text. Take the ones that fit, drop them in the target's docs or preset system. |
| Verify gate | `scripts/verify.sh` as the shape | One script, repo root, runs every suite, one summary table, non-zero on any red. Build the target's own. |
| Testing philosophy | `docs/testing_process/testcase_process_and_philosophy.md` | The workflow (think → test → code → verify live) and the anti-patterns transfer as-is. |
| Patterns habit | `docs/patterns/` here as the example | Create `docs/patterns/` in the target; the `hk-compound` skill maintains it. |
| Tone rules | `docs/fable_roadmap/TONE.md` | Copy nearly as-is — it's about writing, not this repo. |

Don't install everything. The audit said what's missing; the human said
what matters. Skills like `hk-local-*` and `fable-audit` are wired to this
repo's services — leave them.

### Step 4 — Re-score and close

Re-run the checklist on the areas you touched. Append the before/after
scores to `report.md`, update `tracker.md` to say the loop is closed, and
tell the human: what moved, what didn't, and what you'd do next. An
adoption that can't show its score moved didn't happen.

## If you get interrupted

Everything lives in `pattern-engineering-audit/`. A fresh session reads `tracker.md`
and continues. That's the whole recovery plan, and it's enough.
