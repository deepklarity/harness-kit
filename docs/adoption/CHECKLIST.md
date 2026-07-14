# Repo Readiness Checklist

The audit behind [`../Quickstart.md`](../Quickstart.md). Eleven areas, each
scored 0–10. Derived from the 20 tenets in
[`../../odin/docs/philosophy.md`](../../odin/docs/philosophy.md) and the
practices harness-kit runs on.

## The one hard rule: read-only

**You are auditing someone else's repo. Never execute anything in it.**
No test runs, no package managers, no build commands, no git commands, no
scripts — you can't know how they're wired or what side effects they have.
All evidence comes from reading files and listing directories. The only
place you write is `pattern-engineering-audit/`.

When a check can't be settled by reading (do the tests actually pass? is
the venv healthy?), don't run it — write it in the report as **"not
verified — ask the human to run: `<command>`"**. An honest "unverified"
beats evidence gathered by executing code you don't understand.

Evidence still gets saved: copy the relevant file contents, directory
listings, and excerpts into `pattern-engineering-audit/evidence/` so every score
has something to cite.

Scoring guide: 0–2 nothing there, 3–5 exists but only its author can use
it, 6–8 present and documented, 9–10 an agent landing cold could use it
today. If the target is a workspace with several repos, score each active
repo per area and report the worst, saying which repo dragged it down.

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

## 2. Proof and verification

**What it means:** the repo can prove it works. "Tests pass" is the floor;
one command should answer "is the repo green?", and changes should carry
test changes with them. Tenets: Proof of Work, No Slop. This area is about
what a developer can prove locally — CI belongs to area 10 and is never
mentioned in this area's score or why-line.

**What to look for (by reading, not running):**

- [ ] Test files exist and cover more than happy paths (read a few)
- [ ] The entrypoint file or README documents exactly how to run them
- [ ] A single verify-gate script exists (one command, all suites, non-zero
      exit on any red)
- [ ] The gate looks honest — no broad skip/xfail lists hiding known breakage
- [ ] Docs say live verification is a step, not just tests passing
- [ ] Not verifiable read-only: whether the suites actually pass, and
      whether recent changes carried tests. List the exact commands for the
      human to run and mark these "not verified".

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

## 4. Knowledge compounding

**What it means:** nothing gets learned twice. Solved problems become
patterns; traced flows become breadcrumb docs; repeat errors get answered
from history. This is the Compounding Amplifiers tenet — the environment
gets smarter with every task.

- [ ] A `docs/patterns/` (or equivalent) holds reusable learnings
- [ ] Patterns are living docs — new instances appended, not new files
- [ ] Repeated errors have a ledger answered from history
- [ ] End-to-end flow traces exist for the trickiest flows

## 5. Disk-anchored work

**What it means:** work survives the session that started it. Multi-step
work leaves a trail on disk a future session can resume, instead of living
in a chat history that dies.

- [ ] Multi-step work uses a tracker file: where are we, what was decided,
      what's next
- [ ] Scratch/temp locations are conventioned and gitignored
- [ ] Finished workflows leave a self-contained summary; intermediates get
      cleaned up

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

## 7. Cost and delegation

**What it means:** if AI agents do work here, the cheapest capable model
does each job, and someone can see what a run cost. Pareto-Driven
Delegation. Mark N/A if the repo doesn't use agents at all.

- [ ] Written guidance on which model tier does which work
- [ ] Exploration and mechanical work delegated cheap; judgment kept strong
- [ ] Cost of a run is visible somewhere

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

## 9. Skills and presets

**What it means:** recurring procedures — review, RCA, audits, recurring
task shapes — are written down where an agent can trigger them, instead of
being re-invented (differently) every time.

- [ ] Reusable procedures exist as skills, prompts, or docs an agent can
      follow
- [ ] Prompt presets exist for recurring task shapes
- [ ] A self-audit skill/doc exists — this checklist counts once installed

## 10. Git workflows and CI

**What it means:** the path from a change to the main branch is defined and
guarded — branch conventions, review, and automated checks that run on
every change, not just on the author's machine.

**Scoring note (user directive):** CI findings stay in this area. Having no
CI at all is a legitimate choice for plenty of teams — score it here
honestly, but CI never drives the overall verdict, never appears in the
top 5, and a missing CI is not "what's broken" — it's a fact of this area
and nothing more.

**What to look for (by reading only — never run git or CI tooling):**

- [ ] CI config exists (`.github/workflows/`, `.gitlab-ci.yml`,
      `Jenkinsfile`, or equivalent) and runs tests, not just lint
- [ ] CI matches reality — the commands it runs are the same ones the docs
      tell humans to run
- [ ] Branch/PR conventions are written down (contributing doc, entrypoint
      file, or PR template)
- [ ] Commit history reads like a log a human can follow (read recent
      messages in the merge history files if visible, or note "not
      verified" — inspecting history needs git)
- [ ] Release/deploy path is documented — how does a merged change reach
      users?

## 11. Local development

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

## Scoring the report

One row per area in `report.md`: score /10, the evidence file, one plain
sentence why. For every area, also give **three things that are good and
three that most need improvement** — concrete, with file names, not
generalities (if an area genuinely has fewer than three of either, say so
rather than padding). Checks you couldn't settle read-only go in a "not
verified" list with the exact command for the human. Score those as
present but unproven: the thing existing on disk earns a middling score,
not a high one — a high score needs the human's run to confirm it works.
Don't zero it either; unproven is not broken. The overall score is the
average of the area scores, rounded to the nearest whole number — leaving
out area 10 (git workflows and CI) and any area marked N/A. The verdict
sentence still names the most important problem when there is one; the
number just doesn't collapse to it. Area 10 findings live in their own
section only and never enter the top 5. Close with the top 5 things to work on across all areas, ranked by
impact. Present area scores in checklist order (1 through 11) — the order
is by impact of the area, never re-sorted by score.
