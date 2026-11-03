# harnesskit

**Build an agent harness for your repo, then prove it helps: the same tasks, before and after, graded blind.**

[![npm](https://img.shields.io/npm/v/@deepklarity/harnesskit)](https://www.npmjs.com/package/@deepklarity/harnesskit)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![node >= 22](https://img.shields.io/badge/node-%3E%3D22-brightgreen)

Most agent kits hand you files and ask you to trust them. Trust is not enough: a 2026 study of repository context files found that they [raise inference cost by over 20%](https://arxiv.org/abs/2602.11988), and whether they raise success depends on the repo. harnesskit treats the harness like code. It is built from your repository, audited against it, and measured on it.

## Not a scaffold

A scaffold copies the same files into every repository. That part is easy, and on its own it does not help. harnesskit splits the work in three:

| Step | Who does it | What comes out |
|---|---|---|
| **Seed** | `harnesskit init`, in seconds | A setup plan, a board, a skills library, ready-to-install workflow files, the checks. Nothing about your code yet. |
| **Build** | Your coding agent, following the plan | The harness for *this* repo: a routing `CLAUDE.md`, flow breadcrumbs that cite `file:line`, an inspect and a logs script per component, tests for the riskiest interfaces, one verify gate. |
| **Prove** | `harnesskit prove` | The same scenarios run twice, in worktrees before and after the harness, then graded by hidden tests, a blind judge and a quality rubric, under a budget you set. |

The seed is generic. Everything the agent reads at work time is written from your code, and the audit fails it when a citation does not resolve or a component has no tool.

## Quick start

```bash
cd your-repo                          # a git repository with a clean tree
npx @deepklarity/harnesskit init      # seeds the plan
claude "Set up this repo with harnesskit - follow docs/harnesskit/entry.md"
```

`init` touches only `docs/` and `writeup/`; setup asks before each change to `CLAUDE.md`, `AGENTS.md`, `.claude/` and `.gitignore`.

Setup asks a few questions up front (budget, runner, autonomy, which repos are in scope). It then scores the workspace, builds the harness row by row on a board in `writeup/agent-readiness/`, and stops at every decision that spends money or cannot be undone. When the board is done and the gate is green, it writes scenarios and asks before proving:

```bash
npx @deepklarity/harnesskit prove <scenario.md> --dry-run   # cost estimate against the budget
npx @deepklarity/harnesskit prove <scenario.md>             # run both halves
npx @deepklarity/harnesskit prove render                    # writeup/proof/index.html
```

In a session that is already open, paste the quoted part. JavaScript repos that want the version pinned can `npm i -D @deepklarity/harnesskit` and run `npx harnesskit init`.

Re-run `init` later to pick up kit fixes. It upgrades only the files you have not edited, and `init --check` lists the drift.

## What the harness is

**Progressive disclosure, like folders.** `CLAUDE.md` routes rather than explains. It points to three entry indexes (breadcrumbs, scripts, docs). Each entry lists areas, each area lists pages, and the pages hold the detail with `file:line` citations. An agent reads a few hundred words to find the one page it needs, not the whole manual.

| Piece | What it gives the agent |
|---|---|
| Breadcrumbs | One page per end-to-end flow: every hop, its file and function, and what breaks there |
| Scripts | An inspect and a logs tool per component, so the agent stops grepping raw data |
| Tests and gate | Harness-written tests on the riskiest interfaces, and one command that says green or red |
| Board | `board.csv` plus a rendered page: plans, state, and owner decisions with a proposed answer |
| Guard hook | Blocks edits outside scope during setup, then steps aside |
| Skills | About 40 reusable skills: root-cause analysis, PR review, audits, write-ups ([index](kit/skills/README.md)) |

`check_harness.mjs` scores the result: interfaces covered, breadcrumbs whose citations resolve, a tool for every component, connections between components traced, and tests in the gate.

## Results so far

Across 19 scenarios on two repos, the blind judge preferred the harness in 16, and the harness broke no checks (the bare agent broke 4). The gain is in quality and safety; time drops only where a bare agent gets lost. These are small samples: run `prove` on your own repo before you believe them.

## How it differs

| Project | Focus | What harnesskit adds |
|---|---|---|
| [AGENTS.md](https://agents.md) | One shared context-file format | A routed tree written from the code, and a measurement of whether it helps |
| [spec-kit](https://github.com/github/spec-kit) | A spec-driven workflow for new features | Prepares an existing repo, then tests the preparation |
| [superpowers](https://github.com/obra/superpowers), [mattpocock/skills](https://github.com/mattpocock/skills) | Process skills for any repo | A repo-specific layer, plus before/after evals |
| [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | Agile roles and personas | No personas: context, tools and proof |

## Standing on

- Birgitta Böckeler, [Harness engineering for coding agent users](https://www.martinfowler.com/articles/exploring-gen-ai/harness-engineering.html): guides and sensors, and the open question of how to evaluate a harness. `prove` answers that question per repo.
- Mitchell Hashimoto, [My AI adoption journey](https://mitchellh.com/writing/my-ai-adoption-journey): fix every agent mistake so it cannot recur.
- OpenAI, [Harness engineering](https://openai.com/index/harness-engineering/): humans steer, agents execute.
- Anthropic, [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) and [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): progress files and just-in-time retrieval.
- HumanLayer, [Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md): a short root file and progressive disclosure.
- Gloaguen et al., [Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988): why context files need measuring.
- Matt Pocock, [skills](https://github.com/mattpocock/skills): the grill-me, TDD, domain-modelling and writing-for-agents skills are adapted from his.
- Simon Willison, [Agentic engineering patterns](https://simonwillison.net/2026/Feb/23/agentic-engineering-patterns/).

## Requirements and limits

- **Nothing leaves your machine.** harnesskit has no telemetry and makes no network calls. Proof runs go through your own `claude` CLI and account; notes setup keeps about the kit stay in `writeup/agent-readiness/kit-notes.md` in your repo.

- macOS or Linux (Windows through WSL), Node 22 or later, git, and [Claude Code](https://claude.com/claude-code). herdr is optional, for watching proof runs in panes; without it, runs are headless.
- Proof runs bill your Claude account. Every run is estimated first, and a run is refused once the budget is spent.
- Built and measured with Claude Code. Other agents can read the harness, but `prove` drives the `claude` CLI.

## Develop

```bash
npm install
npm run verify           # typecheck and tests
npm run check:generic    # no private workspace names in the kit
```

The CLI is `src/` (TypeScript). The kit it installs is `kit/`: `entry.md` is the setup plan, alongside `docs/`, `skills/`, `templates/` and `proof/`.

harnesskit is 0.x: the kit layout may change before 1.0. Re-run `init` to upgrade; it updates only the files you have not edited. Changes are in [CHANGELOG.md](CHANGELOG.md).

MIT licensed.
