# CLAUDE.md

Guidance for Claude Code when working in this repository. Read `odin/docs/philosophy.md` once per session: its twenty tenets are the filters every design choice and review runs through.

## Repository Structure

- **`odin/`** — multi-agent orchestration CLI (task-board model, suggestive agent assignments).
- **`taskit/`** — task dashboard (Django REST + React/TypeScript/Vite).
- **`harness_usage_status/`** — CLI for AI provider usage quotas.
- **`prompt-library/`** — prompt templates (separate git repository).
- **`harness-kit/`** — the npm package `@deepklarity/harnesskit` and the kit it ships. `kit/` is what another workspace copies to adopt it: `kit/entry.md`, `kit/docs/` (index, checklist, playbook), `kit/skills/` (skills tree with `skills.csv`), `kit/templates/`, `kit/proof/`; `src/` and `bin/` are the TypeScript CLI (`npm run verify`). The root's `initiate-scaffold.md`, `docs/_INDEX.md`, `docs/adoption`, `docs/playbook`, `docs/skills` and `.claude/skills` are symlinks into it.

**Which repo owns which docs.** This root tracks `docs/`, `.claude/skills/` and `scripts/`; cross-project material (breadcrumbs, patterns, playbook, skills, testing process) lives in the root `docs/` (map: `docs/_INDEX.md`). A project folder keeps only the docs next to its code and its own `CLAUDE.md`, which Claude Code picks up when run from that folder. The skills tree exists once, in `harness-kit/kit/skills/` (reached from the root through symlinks). `writeup/` holds initiatives and generated pages and is gitignored.

## Durable State & Memory Policy

**The disk is the memory, not the conversation.** Anything that must survive a session belongs in a file.

- **Never use `MEMORY.md` files.** Persistent context goes in `CLAUDE.md` files or, for learnings, `docs/patterns/` via `/hk-compound`.
- **Every plan is a mission-control folder** `writeup/<initiative>/` (RESUME.md + board.csv + mission-control.html + log.md, per `docs/playbook/mission-control.md`). Never a plan under `docs/`; `docs/` holds durable reference only. Every state change lands in `board.csv` and `node harness-kit/kit/templates/mission-control/render-board.mjs writeup/<initiative>` runs in the same turn.
- **Questions for the owner are decision rows** on the initiative's board with the proposed answer, rendered before they are mentioned in chat. Anything with an obvious default is decided, not asked.
- **Encode standing instructions immediately.** "Always X" goes into the right durable file the same turn, and chat says where it landed.
- **Documentation stays minimal.** One fact lives in one place; when docs and reality diverge, fix the one doc. No dates in prose, no session logs, no status documents.

| Content type | Location | Lifecycle |
|---|---|---|
| Resume point, plans, trackers | `writeup/<initiative>/` | Read first, updated last |
| Compounded patterns | `docs/patterns/` | Permanent; append, never fork |
| Breadcrumb analyses | `docs/breadcrumb_analysis/` | Permanent |
| Loop audit reports | `docs/loop_audits/` | Permanent |
| Iteration scratch (`/hk-refine`) | next to the thing refined | Cleaned when the loop closes; summary stays |
| Temp debugging output | scratchpad or `/tmp` | Never committed |

## Git Safety: No Stash

Never run `git stash` in the main checkout or any shared context: agents work in parallel here and a stash is invisible shared state. Use branches, temporary commits, or files on disk. Inside an isolated task worktree (`.odin/worktrees/...`) stash is allowed and never fails a review on its own.

## Design Principles

- **Simplicity is a feature.** Between two correct approaches, pick the one with fewer moving parts. Abstraction and extensibility points are costs that need a one-sentence justification.
- **Default first.** Everything works out of the box; configuration and agent assignments are suggestive, overridable, never required.
- **Same field in many files is a table.** A key that would land in more than a handful of files goes into one registry (a CSV or index) with a check script. Bookkeeping never goes into a file another tool parses. Before fanning out a change over more than five files, say in one sentence what it will look like on disk.
- **Solve the actual problem.** No framework where a function will do; no config for a decision you can make; if the code feels awkward, question the data model first.
- **harnesskit asks before it touches the daily workflow.** Adopting the kit is a journey the person walks, never a surprise. `init` writes only reference material (`docs/`, the board in `writeup/`); every change to a file that shapes day-to-day work (`CLAUDE.md`, `AGENTS.md`, anything under `.claude/`, `.gitignore`) is a step the person sees as a preview and approves first. No magic commands.
- **No slop.** Generated code meets the hand-written bar. A tenet violation is a code smell even when tests pass: name it and fix it.

## Methodical Problem-Solving

**Observe before acting. Treat the cause, not the symptom. Change one thing at a time.**

1. Read the actual error; run the diagnostic script (`--brief`, then `--full`) before theorizing.
2. State the hypothesis visibly: "X because Y; changing Z should fix it because W."
3. Verify assumptions before building on them; trace the data flow to where actual diverges from expected.
4. If a fix fails twice, the diagnosis is wrong: zoom out. A fix, new error, fix cascade means the first diagnosis was wrong.

**Breadcrumb-first:** before exploring a flow, read `docs/breadcrumb_analysis/_INDEX.md`; it maps symptoms to end-to-end traces.

**Scripts before code inspection.** Never inspect TaskIt data with `curl` or raw API calls; the scripts in `taskit/taskit-backend/testing_tools/` use the ORM and surface problems automatically. The table of which script answers which question, and the output modes, is `docs/testing_process/diagnostic_scripts.md`.

```bash
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --brief
tail -f taskit/taskit-backend/logs/taskit.log     # app log
odin logs -f                                       # running tasks
```

## Subagent Strategy: Parallel by Default

**This section applies ONLY to the top-level conversation. If you are a subagent, launched via the Agent tool for a specific task, do the work directly with your own tools and NEVER spawn further subagents.**

The main conversation coordinates; it does not read five files to understand a codebase. Data gathering and mechanical work go to subagents, and independent subagents launch in one message. **Always pass `model:` explicitly.**

| Work | Agent + model |
|---|---|
| Exploration / search / read | `Explore`, **haiku** (always) |
| Implementation | `general-purpose`, **sonnet** |
| Planning / architecture | `Plan`, **opus** (never downgrade) |

Start cheapest and escalate only on a shallow result. Implementation runs in waves: tests first from a subagent that has no implementation context, a gate that confirms they fail, then parallel implementers who receive the failing tests. Never two subagents on one file in a wave. The full pattern, with the plan-mode fan-out and the wave example: `docs/playbook/subagents.md`.

## Operator Communication

While working autonomously: a checkpoint at every transition (what happened, what it means, what is next; a dispatch says what the change will look like on disk), a structured status at every phase boundary, and a one-sentence flag before any change of course. Every checkpoint names
work by what it does, never by a step number, phase name or row id. Detail: `docs/playbook/operator_contract.md`.

**Watching another session or long run:** never wait on it blind. Watch with a stall detector that reports when the screen has not changed for about a minute, when it waits on a prompt or question, or when a message sits unsent in its input; check in at least every few minutes, and step in as soon as it looks stuck rather than when a long timeout ends.

## Testing & Proof

**Tests pass ≠ feature works.** Every change closes the loop: scenario matrix in chat, failing tests from a subagent with no implementation context, implementation, then live verification. Bugs go Reproduce → Locate → RCA → failing test → fix → verify live (`/hk-rca`). Never test your mocks, never assert only string presence, never test only the happy path.

```bash
scripts/verify.sh                    # every suite, summary table, non-zero on any red
scripts/verify.sh odin snapshots     # a subset
```

| Change type | Run | Also verify |
|---|---|---|
| Logic | `odin/tests/unit/` | |
| Harness or pipeline | `odin/tests/mock/` + snapshots | live spec run |
| Model or serializer | `taskit-backend/tests/` + snapshots | API response |
| Frontend | `taskit-frontend/ npm run test:run` | browser |

Checklist and snapshot workflow: `docs/testing_process/testing_checklist.md`; the full framework: `docs/testing_process/testcase_process_and_philosophy.md`. End work with the concrete commands that verify it.

## House Voice, Skills, Knowledge

- Everything a person reads is written against `docs/playbook/tone_and_taste.md`; every page against `docs/playbook/visual_clarity.md`; HTML goes through `/hk-writeup`'s shell into `writeup/`.
- Skills live under `docs/skills/<category>/<name>/`, tiers in `docs/skills/skills.csv`; `.claude/skills/<name>/SKILL.md` is a stub. New skills go through `/hk-skill-creator`; edit the copy under `docs/skills/`.
- `/hk-compound` captures reusable patterns in `docs/patterns/`: living docs, a new instance is appended to the pattern it belongs to.
