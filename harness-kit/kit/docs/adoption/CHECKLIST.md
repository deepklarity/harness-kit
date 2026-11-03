# Repo Readiness Checklist

Twelve areas, each scored 0 to 10, derived from the eleven tenets in
[`../playbook/tenets.md`](../playbook/tenets.md).

Score each area from what's there (a listing, a cheap model) and what's
any good (a mid-tier model reading content), per the tier the area names.
Scoring guide: 0–2 nothing there, 3–5 exists but only its author can use
it, 6–8 present and documented, 9–10 an agent landing cold could use it
today. If the target is a workspace with several repos, score each active
repo per area and report the worst, saying which repo dragged it down.
An area whose fixes all sit inside the repos the scaffold may not touch
(hygiene, mostly) carries two numbers: the score as it is, and in
brackets the score the suggestions on the board would give if their
owners acted on them, `5 (8)`. The bracket moves when suggestions are
taken, so the area is not stuck by design.

This checklist seeds the agent-readiness board (`docs/harnesskit/entry.md`):
every checked-off gap becomes a row. Each area below names which
of its checks are is-it-there and which are is-it-any-good, and gives the
board rows to write when the area comes up short — each row names the
tenet it serves, by heading from `../playbook/tenets.md`. A row that names
no tenet is rejected. A check the scaffold can't settle by reading becomes
a board row that runs the command and proves it, not a request for the
human to run it themselves.

**Root or inside.** Every row below also carries a Where: root or
inside. A root row is something the scaffold builds around the workspace
without touching any repo in it — a root CLAUDE.md, a gate or stack
script at the root that calls the repos' own commands, docs/patterns,
breadcrumbs, guides, testing docs, skills, audits, scenarios, the proof,
a root .gitignore, a never-do list, hooks in the root .claude/. An
inside row means changing files inside one of the workspace's repos.
Root rows become tasks on the readiness board. Inside rows are never
tasks and never decisions — they become suggestions on the board, one
sentence each on what was noticed and where, for the repo's owners to
take or leave. The scaffold builds around the code and does not change
it.

## 1. Agent entrypoints

**What it means:** a new agent (or new human) landing in the repo finds its
footing without anyone explaining anything — how to build, test, run, and
where things live. This is harness-kit's Crystal Clear Handover tenet.

**What to look for:** an entrypoint file at the repo root — `CLAUDE.md`,
`AGENTS.md`, or equivalent. **Either one is fine; which file is the
company's choice and costs no points. It's only a problem if none exists,
or if two exist and disagree.** A workspace-root entrypoint that covers a
sub-repo counts as coverage. Never write "missing AGENTS.md" (or missing
CLAUDE.md) as a finding or a to-improve item when an entrypoint exists —
the gap worth naming is a repo no entrypoint covers at all.

- [ ] At least one entrypoint file exists at the root
- [ ] It states how to build, how to test, how to run, and the project layout
- [ ] If more than one exists, they agree
- [ ] Sub-projects with their own conventions have their own entrypoint file
- [ ] Spot-read three of its claims against the code — none stale
- [ ] A glossary defines the workspace's own words — any term that's
      contested or fuzzy inside this repo (what counts as a "task", what
      "done" means here, what a house-specific noun refers to). It lives in
      `docs/glossary.md` behind the docs index, with one line in the
      entrypoint pointing at it, not as a section of the entrypoint

**Scoring tier.** Whether an entrypoint file exists at all is a listing
check (cheap). Whether it's complete, whether its claims still hold, and
whether a fuzzy term actually gets defined needs a mid model reading the
file against the code.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write the root entrypoint | a CLAUDE.md or AGENTS.md exists at the root, covering build, test, run, layout | Progressive disclosure | mid | root |
| Give the sub-project its own entrypoint | each sub-project with its own conventions has a file covering them | Progressive disclosure | mid | inside |
| Reconcile two disagreeing entrypoints | the two files say the same thing, or one points at the other | One fact, one place | mid | inside |
| Write the glossary | `docs/glossary.md` defines the workspace's own contested or fuzzy words and the entrypoint links to it in one line | Progressive disclosure | mid | root |

## 2. Proof and verification

**What it means:** the repo can prove it works. "Tests pass" is the floor;
one command should answer "is the repo green?", and changes should carry
test changes with them. Tenets: Proof of Work, No Slop. This area is about
what a developer can prove locally — CI belongs to no area here; harness-kit
does not audit it.

**What to look for (by reading, not running):**

- [ ] Test files exist and cover more than happy paths (read a few)
- [ ] The entrypoint file or README documents exactly how to run them
- [ ] A single verify-gate script exists (one command, all suites, non-zero
      exit on any red; the skeleton is
      `docs/harnesskit/templates/scripts/verify.sh`, and
      `check_doc_links.mjs` beside it catches broken doc links)
- [ ] The gate looks honest — no broad skip/xfail lists hiding known breakage
- [ ] Docs say live verification is a step, not just tests passing
- [ ] Whether the suites actually pass, and whether recent changes carried
      tests, becomes the verify-gate row below: it runs the exact commands
      and proves the answer instead of asking the human to.

**Scoring tier.** Whether test files, a gate script, and a documented run
command exist is a listing check (cheap). Whether the gate hides breakage
behind a skip list, and whether the tests go past the happy path, needs a
mid model reading the tests and the gate script.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write one verify gate, then run it | a root gate script runs every suite (by calling each repo's own commands) and exits non-zero on any red, and it has actually been run: writing it proves nothing, and the failing tests, dependency conflicts and one-machine-only paths it turns up are on the board as suggestions | Proof before done | mid | root |
| Document how to run the tests | the root entrypoint names the exact commands | Progressive disclosure | cheap | root |
| Stop hiding breakage behind skip lists | the gate reports failures honestly instead of masking them | Worst first, scored honestly | mid | inside |
| Write a test past the happy path | at least one test per area covers an edge case or failure mode | Proof before done | mid | inside |

## 3. Debugging readiness

**What it means:** could an agent alone take a bug from symptom to verified
fix here? That needs findable logs, tools to inspect state, and a written
protocol — not tribal knowledge.

- [ ] Logs have a documented location and a documented tail command
- [ ] Diagnostic scripts exist to inspect an entity's state without
      spelunking source
- [ ] Common failures map to a doc: symptom → where to look
- [ ] A written debugging protocol exists (reproduce → locate → root cause
      → failing test → fix → verify)

**Scoring tier.** Whether a logs section, diagnostic scripts, and a
protocol doc exist is a listing check (cheap). Whether the symptom-to-doc
map actually points at the right places needs a mid model tracing a
symptom through it.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Document where the logs are | a doc names the log location and the exact tail command | Progressive disclosure | cheap | root |
| Build a tool to query the database without the API | a script reads entity state directly, no auth or API round-trip needed | Nothing learned twice | mid | root |
| Write the debugging protocol | a doc walks reproduce → locate → root cause → failing test → fix → verify | Proof before done | mid | root |
| Map common failures to where to look | a symptom-to-doc table exists and each row points at the right file | Nothing learned twice | mid | root |

## 4. Knowledge compounding

**What it means:** nothing gets learned twice. Solved problems become
patterns; traced flows become breadcrumb docs; repeat errors get answered
from history. This is the Compounding Amplifiers tenet — the environment
gets smarter with every task.

- [ ] A `docs/patterns/` (or equivalent) holds reusable learnings
- [ ] Patterns are living docs — new instances appended, not new files
- [ ] Repeated errors have a ledger answered from history
- [ ] A breadcrumb exists for each kind of question people bring here, not
      only for the product flows: every test suite, the complaints the
      debug scripts answer, the CLI tooling, the storage layer, the ops
      tasks
- [ ] The breadcrumb index is findable by a search: its Trigger and
      What-it-covers columns carry the names someone would grep for —
      script names, flags, status values, table names — so a subagent that
      greps the tree lands on the right row without being pointed at it

**Scoring tier.** Whether a patterns folder and a breadcrumbs folder with
an index exist is a listing check (cheap). Whether patterns are living
documents that get appended to, rather than one-off notes, needs a mid
model comparing entries over time.

**Score this area by breadcrumb coverage.** Count the kinds of question
the index covers against the kinds this workspace actually gets, read off
its own testing docs, test suites, scripts and ops runbooks. Three product
flows and nothing else is a low score however well written they are: a
question about a test suite or a debug script finds nothing, and the
session explores from scratch. Full marks need the index to answer the
common kinds and to be greppable.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Start the patterns folder | a patterns folder exists with its first entry | Nothing learned twice | cheap | root |
| Write a breadcrumb for each kind of question people bring here | the index covers the product flows, every test suite, the complaints the debug scripts answer, the CLI tooling, the storage layer and the ops tasks, each with a doc tracing it end to end, file and function | Nothing learned twice | mid | root |
| Make the breadcrumb index findable by a search | every row's Trigger and What-it-covers columns name the scripts, flags, status values and tables someone would grep for | Nothing learned twice | cheap | root |
| Start a repeated-error ledger | recurring errors are logged once, with the fix, in a place future runs read first | Nothing learned twice | mid | root |

## 5. Disk-anchored work

**What it means:** work survives the session that started it. Multi-step
work leaves a trail on disk a future session can resume, instead of living
in a chat history that dies.

- [ ] Multi-step work uses a tracker file: where are we, what was decided,
      what's next
- [ ] Scratch/temp locations are conventioned and gitignored
- [ ] Finished workflows leave a self-contained summary; intermediates get
      cleaned up

**Scoring tier.** Whether a tracker convention and a gitignored scratch
location exist is a listing check (cheap). Whether trackers actually
answer where things stand and what's next needs a mid model reading one.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Adopt a tracker convention | multi-step work writes a tracker file: where are we, what was decided, what's next | The disk is the memory | cheap | root |
| Gitignore the scratch location | temp and intermediate work lands in a conventioned folder covered by the root .gitignore | Read before write, never write what you weren't asked to | cheap | root |
| Write a summary on close | a finished workflow leaves one self-contained summary and its intermediates are cleaned up | The disk is the memory | mid | root |

## 6. Hygiene

**What it means:** the repo doesn't lie to its readers. No secrets in the
tree, no temp debris, no docs describing behavior the code lost months ago.

**What to look for (file listing and reading only):**

- [ ] No committed secrets — scan the file listing for `.env`, key and pem
      files, credentials in config. **From the listing only — never open a
      secret file's contents, never quote them in evidence.**
- [ ] No temp debris — files named like `temp_*`, `scratch_*`, `old_*`,
      `backup_*`, download copies like `name (1).md`. Judge intent: a
      deliberate, referenced `scripts/debug/` tool is not debris.
- [ ] `.gitignore` exists and covers the obvious (env files, caches,
      build output)
- [ ] TODO/FIXME/HACK comments are few and current (read a sample; skip
      vendored/third-party code)
- [ ] Docs don't contradict code — spot-check the README's claims

**Scoring tier.** Spotting secrets, temp debris, and a missing .gitignore
is a listing check (cheap). Whether the README's claims still match the
code needs a mid model reading both.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Add a .gitignore covering the obvious | the root .gitignore excludes env files, caches, and build output | Read before write, never write what you weren't asked to | cheap | root |
| Clear out temp debris | files named like temp_*, scratch_*, old_*, backup_* are removed or moved to a conventioned location | Worst first, scored honestly | cheap | inside |
| Fix a stale doc claim | the README or entrypoint no longer describes behavior the code lost | One fact, one place | mid | inside |

## 7. Cost and delegation

**What it means:** if AI agents do work here, the cheapest capable model
does each job, and someone can see what a run cost. Pareto-Driven
Delegation. Mark N/A if the repo doesn't use agents at all.

- [ ] Written guidance on which model tier does which work
- [ ] Exploration and mechanical work delegated cheap; judgment kept strong
- [ ] Cost of a run is visible somewhere

**Scoring tier.** Whether a model-routing section exists in the entrypoint
is a listing check (cheap). Whether exploration actually goes cheap and
judgment actually goes strong in practice needs a mid model reading recent
runs against the guidance.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write model-routing guidance | the entrypoint says which tier of model does which kind of work | Spend tokens where judgment is | cheap | root |
| Show run cost | a run's cost or token usage is visible somewhere a person would look | Spend tokens where judgment is | cheap | inside |
| Name what bills a provider | the entrypoint lists every command that spends real money (live evals, paid APIs) with its cap and who may run it | Spend tokens where judgment is | cheap | root |

## 8. Human legibility

**What it means:** a person who wasn't there understands the docs, the
reports, and the failures from the sentences themselves. Plain words, real
file names, decisions surfaced where humans actually look.

The writing test, inlined (from harness-kit's tone rules): write the way
you talk. If you wouldn't say the sentence out loud to a teammate, rewrite
it. No "utilize/leverage/facilitate", no incident nicknames, no system
slang. Give the real example, the real number, the real file name.

- [ ] Docs pass the writing test above
- [ ] Reports and status output lead with what's broken, not what's fine
- [ ] Anything a human must decide is surfaced where they look, options
      inline
- [ ] No dead docs describing a state that's long gone

**Scoring tier.** Whether docs exist at all is a listing check (cheap).
Whether they pass the tone document needs a mid model reading them against
it.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Rewrite a report to lead with what's broken | status output puts the problem first, not the passing items | Worst first, scored honestly | mid | root |
| Remove a dead doc | a doc describing a state the code no longer has is deleted or fixed | One fact, one place | mid | inside |
| Surface a pending decision where humans look | an open decision is a row on the initiative's board, with options, rendered before it's mentioned in chat | Written for the reader who was not there | mid | root |
| Write the first system guide | `docs/guides/<slug>/` holds one guide built as the site `docs/playbook/readthedocs.md` describes (its layout, sidebar and build, not a single markdown page) for the part of the system people arrive at cold most often; the row is not done while any part of that shape is a follow-up row | Written for the reader who was not there | mid | root |

## 9. Skills and audits

**What it means:** recurring procedures — review, RCA, audits, recurring
task shapes — are written down where an agent can trigger them, instead of
being re-invented (differently) every time, and the kit's own audit skills
have actually been pointed at this repo at least once.

- [ ] Reusable procedures exist as skills, prompts, or docs an agent can
      follow
- [ ] Prompt presets exist for recurring task shapes
- [ ] The audit skills that ship with the kit (security, slop, test-gap,
      and the rest) have each been run once against this repo, and their
      findings are rows somewhere, not just a report nobody read
- [ ] A self-audit skill exists that can re-score this checklist itself,
      so readiness gets checked the same way twice

**Scoring tier.** Whether a skills folder and a presets file exist is a
listing check (cheap). Whether the written procedures are actually
followable as written, and whether the shipped audits' findings turned
into real rows, needs a mid model walking one.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write a skill for a recurring procedure | a review, audit, or RCA an agent already does by hand is written down as a followable skill | Nothing learned twice | mid | root |
| Run the security, slop, and test-gap audits once | each audit has run once and its findings are triaged; each audit leaves `writeup/audits/<name>/findings.md`, one line per finding as `severity N: what is wrong — path/to/file`, which the proof report reads | Proof before done | mid | root |
| Install a self-audit skill | this checklist (or equivalent) is runnable as a skill that rescores itself, not just a doc someone reads | Nothing learned twice | cheap | root |

## 10. Running it locally

**What it means:** everything a developer (or agent) needs runs locally —
setup, services, seed data — without touching shared or cloud
infrastructure, ideally from one command.

- [ ] Local setup is documented end to end: prerequisites, install, run
- [ ] One command (or close) brings the stack up locally — a dev script,
      docker compose, or Makefile target
- [ ] Local config has a template (`.env.example` or equivalent) so setup
      doesn't require asking a teammate — judge from the listing and the
      template, never open real env files
- [ ] Seed/sample data exists so the app is usable right after setup
- [ ] Dev workflow doesn't depend on shared/cloud resources for everyday
      work, or the doc says exactly which ones and why

**Scoring tier.** Whether a setup doc, env template, and one-command start
exist is a listing check (cheap). Whether the doc really takes someone
from nothing to a running stack needs a mid model walking it.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write a script for stack up, down, status, logs | a root stack script handles each of those four, documented in the root entrypoint | Progressive disclosure | mid | root |
| Add an env template | `.env.example` (or equivalent) lists every variable setup needs, with no real values | Default first, override by tier | cheap | inside |
| Add seed or sample data | the app is usable right after setup, no manual data entry | Default first, override by tier | mid | inside |
| Write a testing guide end to end | a doc walks setup through running the suites, with the exact commands | Progressive disclosure | mid | root |

## 11. Guardrails

**What it means:** what an agent must never do here is written where every
agent reads it, and enforced by a script or hook wherever that's possible
— not left as prose an agent can skim past under pressure.

- [ ] The entrypoint states what an agent must never do here: shared-state
      git operations (like `git stash` in a shared checkout), touching
      production, touching secrets, deleting data, spending money over a
      cap. Each rule sits in the section it belongs to — the spend rule
      with testing, the stash rule with the git rules — rather than in a
      list of its own at the end, which is read as a block and skimmed
- [ ] Each of those rules says why in one sentence, not just what
- [ ] The rules that can be enforced mechanically are — a hook, a
      permission rule, a confirmation gate in the CLI — not left to an
      agent remembering to read the prose
- [ ] The entrypoint states an agent's own blast radius: which folders it
      may write, which repos or systems it may not touch

**Scoring tier.** Whether a never-do list exists at all, and whether hooks
or permission rules back any of it, is a listing check (cheap). Whether
each rule actually says why, and whether the stated blast radius matches
what the tooling actually permits, needs a mid model reading the
entrypoint against the hook and permission config.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write the never-do rules | the root entrypoint names the operations an agent must never run here, each with a one-sentence why, each in the section it belongs to rather than in a list of its own | Read before write, never write what you weren't asked to | mid | root |
| Enforce the enforceable rules | the rules that can be caught mechanically are backed by a hook in the root .claude/, a permission rule, or a confirmation gate, not prose alone; a hook whose script is missing lets the call through and says so, because a hook that blocks everything when its file moves locks the session out of fixing it | Read before write, never write what you weren't asked to | mid | root |
| State the write boundary | the root entrypoint says which folders an agent may write and which repos it may not write to, and says in the same breath that reading, searching and running anything anywhere is expected: the boundary is on writing, and an agent that stops exploring a repo because it may not edit it answers worse | Read before write, never write what you weren't asked to | cheap | root |

## 12. Measured readiness

**What it means:** the workspace can show, with numbers, that an agent
works better here than in a bare clone with none of this scaffolding —
not just claim it.

- [ ] `writeup/scenarios/` holds the number of real scenarios the profile
      asks for (three by default, one per difficulty) an agent here would
      actually be given, each naming the harness piece it exercises on its
      `Exercises:` line; questions carry a must-mention list, outcomes make
      the agent create, change or run something and carry a proof command
      that checks the result from the database, the output folder or the
      logs and never reaches a paid provider (the format is
      `docs/harnesskit/templates/scenario.md`; the procedure is
      `docs/proof.md`)
- [ ] A proof run exists at `writeup/proof/index.html`, showing cost,
      time, and correctness before this scaffolding and after it
- [ ] The proof gets rerun when the board changes materially, not left to
      go stale next to a repo that's moved on
- [ ] The report carries findings the bare clone never surfaced — the
      audits this checklist asked for in area 9 show up here as evidence,
      not just as a separate report

**Scoring tier.** Whether the scenarios folder and the proof report exist
at all is a listing check (cheap). Whether the scenarios are real
questions with checkable must-mention lists, whether the outcome scenarios'
proof commands only read and reach no provider, and whether the before/after
numbers actually hold up, needs a mid model reading the report against the
scenarios.

**When missing, add these rows.**

| Row | Closes when | Tenet | Tier | Where |
|---|---|---|---|---|
| Write the scenarios | `writeup/scenarios/` holds the profile's count of scenarios, one per difficulty, each with an `Exercises:` line, questions with a must-mention list and at least one outcome with a proof command that fails on the untouched workspace, none billing a provider through the code | Proof before done | mid | root |
| Run the proof | `writeup/proof/index.html` shows cost, time and the judge's preference for each scenario, its before half and its after half, per `docs/proof.md`, within the profile's `budget_usd`; one scenario at a time, each estimate shown to the person before it runs | Proof before done | mid | root |
| Rerun the proof after the board is worked | the proof report reflects the current board, not a stale one from before the work landed | Proof before done | mid | root |
