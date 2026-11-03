# CLAUDE.md

Guidance for Claude Code when working in this repository.

<!-- BUDGET: the finished file is 150 lines and 1,500 words or fewer (check_claude_md.mjs enforces both). This file is level 1: it routes ("kind of task -> one entry file"). There are three entry files, each listing only areas (folders), one line each, 5 to 12 rows, no answers and no path:line: docs/breadcrumb_analysis/_INDEX.md (flows), scripts/README.md (tools), docs/_INDEX.md (guides, testing, playbook, patterns). Each area folder has its own index (_INDEX.md for breadcrumbs, README.md for scripts) listing its pages or scripts, one line each with the answer or command. Pages and scripts hold the detail. An agent reads one branch. Before adding a line, cut one; detail an index holds is deleted here. -->

<!-- FILL: one line per top-level repo or app directory, from the folder listing. -->
## Repository Structure

{{repo_list}}

<!-- FILL: which repo tracks docs, skills and scripts, and which are gitignored — from the git remotes and .gitignore of each folder. Ask the person if it is not obvious. -->
**Which repo owns which docs.** {{docs_ownership}}

<!-- FILL: sibling directories, data stores and gitignored scratch, then the log files a person tails during a live run — from the logging config and the start script, confirmed on disk. -->
{{siblings_and_logs}}

<!-- COPY the lead-in and the fixed rows; drop a fixed row whose entry file this workspace does not have. The three entry files are `docs/breadcrumb_analysis/_INDEX.md`, `scripts/README.md` and `docs/_INDEX.md`; the routing table names each one that exists. FILL the extra rows: one per action an agent routinely gets wrong here, pointing at an entry file, or at a `## Read first` section (5 to 10 lines) of the doc that owns the rule. Drop the slot row when there are none; never write "none yet". Never say what a doc or index holds ("no breadcrumb yet", "covers X"): this file is read every session and updated last, so a status here goes stale and steers the agent away from what exists. -->
## Read before you act

Find the row first. Before the action on the left, open the entry file on the right and follow it down: the entry file lists areas (folders), each area has its own index, the index names the page or the command. Read one branch, not the tree.

| About to... | Open first |
|---|---|
| Debug a failure, explore or change a flow | `docs/breadcrumb_analysis/_INDEX.md`, then the area's `_INDEX.md` |
| Inspect state, or seed, create, export, reset | `scripts/README.md`, then the area's `README.md` |
| Run or write tests, write a doc, delegate, start an initiative, or look for a learning or a term | `docs/_INDEX.md`, then the area it names |
| {{action}} | {{doc}} |

<!-- COPY: kit rule. One entry link to the docs tree; never a list of the docs in it. -->
`docs/_INDEX.md` maps the docs tree. Everything below links into it rather than repeating it.

<!-- COPY: kit rule. Keep verbatim; `{{cli_activation}}` below is the only slot. -->
## Durable State & Memory Policy

**Anything that must survive this session belongs in a file.**

- **Each initiative has a folder `writeup/<initiative>/`**, updated in the turn the state changes and read first on "resume" (`docs/playbook/mission-control.md`).
- **One fact lives in one place**; when docs and reality diverge, fix the one doc. No `MEMORY.md` files: context goes in this file, `docs/`, or `docs/patterns/`. "Always X" is written into the right file the same turn, and you say where.
- **A copied rule that proves wrong is a local note, not an edit.** `docs/harnesskit/manifest` lists the copied files; add one line to `writeup/agent-readiness/kit-notes.md` (local, read by no one but you): the kit file, what happened, what should change.
- **Never run `git stash`** in a shared checkout; use a branch or a temporary commit. Inside your own task worktree it is fine.
- Breadcrumbs (`docs/breadcrumb_analysis/`) and patterns (`docs/patterns/`) are permanent: append to the page, never fork one.

<!-- FILL: how to enter the project's shell — venv activation, nvm, container exec. Each Bash call is a fresh shell. Drop this line if the repo needs no activation. -->
**{{cli_activation}}**

<!-- COPY: kit rule. Keep verbatim. -->
## House Voice & HTML Pages

Everything a person reads is written against `docs/playbook/tone_and_taste.md` and the visual rule beside it. HTML deliverables go through `/hk-writeup` into `writeup/`.

<!-- FILL: the one command that brings the stack up, its ports, and the runtimes and env files it needs. From the start script, docker-compose, package.json or Makefile. Ask the person what to call it if there is no single script. {{setup_doc}} is the file that actually documents first-run setup, a README or a repo's own CLAUDE.md; if none does, drop the sentence rather than name a plausible path. -->
## Quick Start

```bash
{{start_command}}
```
Requires {{runtimes_and_services}}. Env files: {{env_files}}. First-time setup: {{setup_doc}}.

<!-- FILL: the gate and the per-suite commands, from CI config or the existing docs; keep a documented command verbatim if it still works. What suite bills a provider, how a test id is obtained, how a fixture is seeded: that belongs in the testing doc, not here. If the workspace has no testing doc yet, leave {{testing_doc_link}} out, drop the Testing-doc-first line, and put "write the testing doc" on the readiness board. -->
## Testing

```bash
{{verify_gate}}
{{test_commands}}
```

<!-- FILL: the one-line runner fact from the testing doc, e.g. "pytest is not installed; run `<command>`". Quote it, do not restate the doc; drop the line if the doc has none. -->
{{runner_fact}}

**The gate is the definition of done.** End work with the exact commands that verify it. Find the row in {{testing_doc_link}} before reading test files.

- While iterating, run only the tests for what you changed (`scripts/verify.sh <suite>`); the full gate runs once, before you say done.
- Scenario matrix first, then failing tests, then the code. Assert on values the real code produced, cover the failure path, never test your mocks.
- Tiers: **mock** (default) touches no network, stack or provider; **stack** needs local services; **live** reaches a paid or shared provider. A plain run is mock only.
- A test that fails without your change is not yours: show it with `scripts/clean_run.sh '<command>'`, note it, move on.
- Passing tests are not a working feature: drive the flow with `/run`, then `/code-review` the diff.
<!-- FILL: anything this stack needs before a change is really live — a worker restart, a rebuild, a re-seed, a cache flush. -->
- {{live_verification_steps}}

<!-- FILL, and drop this subsection if nothing in this workspace bills anyone. What costs real money, the default size or cap a session stays under, the code that enforces it if any, and that a session never goes past it without the person's word in that conversation. Four sentences at most, then one link to the doc listing which suites and commands bill — never the list itself here. -->
**What costs money.** {{billing_rule}} Approval for one paid run does not carry to the next; smoke one item before a batch. What bills, and how to run each suite without it: {{testing_doc_link}}.

<!-- FILL: the request path in one sentence, then the stack per layer. From the entry points, the router, the queue config and the frontend build file. Name the entry point and say which repo's own CLAUDE.md describes it; never function or constant names, never a recipe for adding one — that lives in the repo. -->
## Architecture

Flow: {{request_flow}}

{{stack_summary}}

<!-- BLEND: at most six conventions, each one an agent would otherwise get wrong. A convention with a doc of its own is one line and a link. Recipes live in the repo that owns them, never here. -->
## Coding Conventions

{{conventions}}

<!-- COPY: kit rule. Keep verbatim. -->
## Design Principles

- **Simplicity is a feature**: fewer moving parts; every abstraction needs a one-sentence justification.
- **Solve the actual problem**: no config for a decision you can make; if the code feels awkward, question the data model.
- **No slop**: match surrounding naming and idiom. **Default first**: it works out of the box.

<!-- COPY: kit rule, except the two slots for this repo's diagnostics and logs. -->
## Methodical Problem-Solving

**Observe before acting. Treat the cause, not the symptom. Change one thing at a time.**

1. Read the actual error; run the diagnostic before theorizing.
2. State the hypothesis: "X because Y; changing Z should fix it because W."
3. Trace the data flow to where actual diverges from expected.
4. A fix that produces a new error needing its own fix means the first diagnosis was wrong.

**Breadcrumb-first:** before exploring how a flow works, read `docs/breadcrumb_analysis/_INDEX.md`, open the area it names, then the page that area's index names. Half-covered: explore only the gaps. Absent: explore, then suggest /hk-breadcrumb-creator.

<!-- FILL: the entry file for the tools, `scripts/README.md`, which lists areas; each area README lists scripts with the complaint each answers. Never paste invocations here. If the workspace has no state to inspect and no scripts (a library), replace this whole paragraph with one sentence: how a failure is reproduced here (a failing test, the command) and that the breadcrumb index maps a complaint to a trace. If scripts should exist but do not, say so in one sentence and leave the row on the board. -->
**Diagnostics before code inspection.** `scripts/README.md` lists the areas; each area's README lists the read-only debug scripts and the job scripts (seed, create, ingest, export, reset) by task, with the command and its flags. Run what ships before writing your own script against the database or the ORM.

<!-- COPY: kit rule. Keep verbatim. -->
## Tool Rules

- **Find the row first.** When a row matches, open its page or run its tool before any grep; grep only for what no row covers, and when you had to, add the row.
- **A script a later run would also need is a tool, not a throwaway.** Put it in the repo, add its row (task, command and flags, what it prints) to its area's README (and the area to `scripts/README.md` if new), and run what ships before writing a new one. Never inspect state with raw `curl` or ad-hoc queries when a catalogued script answers.
- **Foreground calls finish in seconds.** Anything over a minute (a full suite, a live run, a build) runs with `run_in_background: true` or in a subagent, writing to a log; no `sleep` loops. "Stuck" means no new log output for about three minutes: find what it waits on, then kill it. Check, report, end the turn.
- **Independent calls go out in one message.** Read with the file reader, edit with the editor, search with `grep -rn` (or `rg`); use absolute paths.
<!-- FILL from `echo $SHELL`: when zsh, keep the line below; when bash, drop it. -->
- {{shell_rule}}

<!-- COPY: kit rule. Keep verbatim, including the subagent guard in the first paragraph. -->
## Subagent Strategy: Parallel by Default

**This section applies ONLY to the top-level (main) conversation. If you are a subagent, launched via the Agent tool for a specific task, it does not apply to you: do the work directly and NEVER spawn further subagents.** (Every agent loads this file; without the guard, subagents recurse.)

Data-gathering and mechanical work go to subagents, independent ones in one message; synthesis and decisions stay here. **Always pass `model:` explicitly** (an omitted one inherits the top-tier model):

<!-- BLEND: the rows are this harness's agents and models; keep the routing, correct the names. -->
| Work | Agent + model |
|---|---|
| Exploration / search / read | `Explore`, **haiku** |
| Implementation | `general-purpose`, **sonnet** |
| Planning / architecture | `Plan`, **opus** (never downgrade) |

Start cheapest, escalate only on a shallow result. Never two subagents on one file in a wave. Pattern: `docs/playbook/subagents.md`.

<!-- COPY: kit rule, except the last bullet, a FILL: add the one or two stop conditions specific to this workspace (a shared branch, a deploy, a data store) or drop that bullet. -->
## Stop and Ask

Stop and put the question to the person, with your proposed answer, when:

- the next step bills past the cap, deletes data that has no copy, or writes into a protected folder;
- the checkout holds uncommitted changes you did not make and the step would switch branches or discard them;
- a fix has failed twice: the diagnosis is wrong, so zoom out and report what you observed;
- two docs, or a doc and the code, disagree and a command you can run does not settle it;
- {{workspace_stop_condition}}

<!-- FILL: the write boundary and the never-do list, from the repo listing and the guard hook's `.claude/hooks/protected.txt`. Plain nouns, one line per slot; a rule with no why is dropped. Setup-time rules (a setup-only guard list, `protected.txt`, "while harness setup runs") never go in the final file. -->
## Write boundary and never-do

Agents may edit: {{writable_paths}}. Agents never touch: {{protected_paths}}. Agents never run: {{never_run}}. Reading, searching and running anything inside a protected folder is expected; the boundary is on writing only.

<!-- FILL: which env file belongs to which service, and one link to the doc that says what each variable is for. Variable names are in the .env.example next to each file and are not copied here — a list here is read as the whole set and is wrong within a release. Where a service has no .env.example, its variable names go in the env-var doc, not here. Never a value. -->
## Configuration

Every setting is an environment variable. {{env_files_by_service}}, each with an `.env.example` naming its variables; what each is for: {{env_var_doc_link}}. Never open, quote or commit a secret file or put a value in a doc.

<!-- COPY: kit rule. The skills line points at the catalogue because a roster here goes stale; the definitions of the workspace's own words go in the glossary, never here. -->
## Language and Skills

Skills are catalogued in `docs/skills/skills.csv` (`/hk-rca` for a bug, `/hk-breadcrumb-creator` for a flow with no breadcrumb); the workspace's own words are in `docs/glossary.md`, reached from `docs/_INDEX.md`.
