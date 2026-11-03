# docs/: what each folder is for, and when to use it

Read this once when you arrive, and again whenever you are about to write
something and are not sure where it goes. Every folder below answers three
questions: what lives there, when you write there, and which skill does the
writing so you do not have to remember the shape. "Kit" means the folder
arrives when harness-kit is installed and is replaced on upgrade; "yours"
means the workspace owns it and nothing ever overwrites it.

## The moment-by-moment guide

| You are about to... | Go to | Use |
|---|---|---|
| Land in this workspace cold | `CLAUDE.md` at the root, then this file | read |
| Find out how a flow works before touching it | `breadcrumb_analysis/` | `/hk-follow-breadcrumb` |
| Trace a flow nobody has written down | `breadcrumb_analysis/` | `/hk-breadcrumb-creator` |
| Debug something broken | the protocol in `testing_process/`, then the diagnostic tools `CLAUDE.md` names | `/hk-rca` |
| Notice you are grepping the same logs again | a script next to the other diagnostic tools | `/build-debug-tool` |
| Finish something that was tricky, or hit a gotcha | `patterns/`, append to the matching file | none |
| Feel stuck, or about to reach for a workaround | `patterns/` | `/hk-pattern-check` |
| Start any work that will outlive this session | `writeup/<initiative>/` at the root (a mission control) | copy the files from `writeup/agent-readiness/` (RESUME.md, board.csv, log.md, the page template; render with the installed `docs/harnesskit/templates/mission-control/render-board.mjs`, never a copy), placed there when the kit was scaffolded in |
| Meet a word this workspace uses its own way | `glossary.md` | read; add the word there when it is missing |
| Write anything a person will read | `playbook/tone_and_taste.md` first | read |
| Produce a page or a report | `writeup/` at the root | `/hk-writeup` |
| Check how ready the workspace is for agents | `adoption/CHECKLIST.md` | `harnesskit/entry.md` |
| Add a recurring procedure | `skills/<category>/<name>/` | `/hk-skill-creator` |

## The folders

| Folder | Kit or yours | What lives here | Write here when | Read here when |
|---|---|---|---|---|
| `playbook/` | kit | How we write and lay out pages: `tenets.md` (the rules everything obeys), `tone_and_taste.md` (voice), `visual_clarity.md` (what a screen shows), `mission-control.md` (the board and page contract), `readthedocs.md` (system guides), `guide_audit_rubric.md` | Never, in a workspace. Propose changes to the kit. | Before writing anything a person reads; before making a page or a board |
| `skills/` | kit, plus yours | Recurring procedures, one folder per skill, indexed in `skills/README.md` and tiered in `skills/skills.csv`. `.claude/skills/` at the root holds the one-line stubs Claude Code discovers. | A procedure you have done twice by hand | When the task matches a skill's trigger words |
| `proof.md` | kit | How the before/after proof runs: scenarios, worktrees, runners, budget, judge | Never, in a workspace | When writing scenarios or running `harnesskit prove` |
| `adoption/` | kit | The twelve-area readiness checklist, the audit report template and the root CLAUDE.md audit | Never, in a workspace | When scoring the workspace or seeding its readiness board |
| `patterns/` | yours | Reusable learnings: a named principle, how to recognise it, what to do, and the instances that taught it. Living files: a new instance is appended, not a new file. | You solved something non-trivial, or a fix took more than two tries, or a gotcha would bite the next person | Before a second attempt at anything; when the problem feels familiar |
| `breadcrumb_analysis/` | yours | End-to-end traces of flows: `FLOW.md` (the hops), `DETAILS.md` (file and function per hop), `DEBUG.md` (logs, commands, where it breaks), indexed in `_INDEX.md` with a symptom table | A flow crosses two or more of frontend, backend, worker, CLI and you traced it | Before exploring a flow; the symptom table maps a complaint to the right `DEBUG.md` |
| `testing_process/` | yours, with an index seeded by the kit | The testing workflow (scenario matrix, failing tests first, live verification), the debugging protocol, and the test shortcuts for this workspace | A test command or path was worth writing down | How do I run, test, or get an id |
| `glossary.md` | yours, seeded by the kit | The workspace's own words, one line each: what the word means here and where it lives in the code | A word is used its own way here and is not on the list | A term in a doc or a row reads two ways |
| `guides/` | yours | Longer how-tos a person reads to learn a system: setup, deployment, a subsystem end to end | A question was answered in chat for the second time | When onboarding to a subsystem |
| `../writeup/` (root, gitignored) | yours | Mission-control initiatives (`RESUME.md`, `board.csv`, the page, `log.md`), audit pages, one-off reports. Transient by design. | Any multi-step work; any generated page | On "resume": list `writeup/*/RESUME.md` |

## What is deliberately not here

- No `MEMORY.md`. Persistent context goes in `CLAUDE.md`, this tree, or `patterns/`.
- No plans or roadmaps under `docs/`. They are initiatives in `writeup/`.
- No second copy of a design, a number, or a status. One fact lives in one place; the others link to it.

## When the kit is installed elsewhere

`npx @deepklarity/harnesskit init` copies the kit folders above, records
each file's hash in `docs/harnesskit/manifest`, and seeds the "yours" folders
with empty index files. Running it again upgrades kit files the workspace
has not edited and reports the drifted ones; `--check` only reports.
"Yours" folders are never touched.
