<!-- Generated from harness-kit/README.md by scripts/sync-readme.mjs; edit that file, then run the script. -->

# harness-kit

harness-kit is an open-source CLI and setup guide for [harness engineering](https://www.martinfowler.com/articles/exploring-gen-ai/harness-engineering.html): your coding agent writes a `CLAUDE.md`, flow guides, inspect scripts and tests for your repo, and harness-kit checks with before-and-after runs whether they made the agent's work better.

https://github.com/user-attachments/assets/242e34b7-6106-483b-952c-912e8c95669e

## Quick start

In any git repo, with [Claude Code](https://claude.com/claude-code) installed:

```bash
npx @deepklarity/harnesskit init
claude "Set up this repo with harness-kit - follow docs/harnesskit/entry.md"
```

Setup is one long Claude Code session. It asks a few questions first, then shows its progress as it goes.

## Why do coding agents (e.g. Claude Code) struggle in a real codebase?

It knows the language, not your codebase.

| Problem | Fix |
|---|---|
| Can't find the code behind a feature | A table-of-contents `CLAUDE.md` and a guide per flow, at file and line |
| Guesses what's in the data or logs | An inspect and a logs script per component |
| Stops when it *looks* right | Tests and one pass/fail command; `/hk-rca` for bugs |
| Forgets between sessions | Guides, scripts and tests live in your repo |
| Starts before it understands you | `/hk-ask-until-clarity`, `/matt-grill-me` |
| You don't know what it built | `/hk-explain`, `/hk-quiz` |

## Skills you'll use every day

Installed only if you say yes during setup. [Full list](harness-kit/kit/skills/README.md).

| To | Run |
|---|---|
| Fix a bug from root cause | `/hk-rca` |
| Find where something happens | `/hk-follow-breadcrumb` |
| Map a new flow | `/hk-breadcrumb-creator` |
| Turn a repeated search into a script | `/build-debug-tool` |
| Review a branch or PR | `/hk-pr-review` |
| Find dead code and slop | `/hk-code-quality-audit` |
| Check security | `/hk-security-audit` |
| Find missing tests | `/hk-test-case-audit` |
| Agree on the plan first | `/matt-grill-me` |
| Understand a change | `/hk-explain`, `/hk-quiz` |

## Is it safe to run on my repo?

| | |
|---|---|
| Files | `init` writes only `docs/` and `writeup/`; changes to `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.gitignore` wait for your yes |
| Code | Untouched during setup |
| Data | Stays on your machine; no telemetry |
| Cost | One Claude Code session; measuring runs stay inside your budget (USD 10 default) |

## Learn harness engineering

The reading behind harness-kit, in the order we'd read it:

| Read | What you'll learn |
|---|---|
| [Harness engineering for coding agent users](https://www.martinfowler.com/articles/exploring-gen-ai/harness-engineering.html), Birgitta Böckeler | The core idea: guides that steer the agent and sensors that check its work |
| [My AI adoption journey](https://mitchellh.com/writing/my-ai-adoption-journey), Mitchell Hashimoto | Fixing every agent mistake so it can't happen again |
| [Harness engineering](https://openai.com/index/harness-engineering/), OpenAI | What changes when agents write most of the code |
| [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), Anthropic | Context as a budget, and loading it just in time |
| [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), Anthropic | Progress files and how long tasks stay on track |
| [Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md), HumanLayer | Keeping the root file short and pointing to the rest |
| [Agentic engineering patterns](https://simonwillison.net/2026/Feb/23/agentic-engineering-patterns/), Simon Willison | Working patterns for agents, such as red-green testing |
| [Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988), Gloaguen et al. | Evidence on when context files help and what they cost |
| [Lauren Tan](https://x.com/poteto) | Verification skills and feature maps for large apps |
| [Matt Pocock](https://x.com/mattpocockuk) | Small, composable skills; several of ours are adapted from his |

## FAQ

**Does it work with Codex, Cursor or other agents?** What it writes is plain Markdown and scripts that any agent can read. Setup and the skills are built for Claude Code.

**How is this different from writing a `CLAUDE.md` myself?** You can, and you should keep it short. harness-kit has the agent write the parts that take longest by hand (a guide per flow, a script per component, the tests) from your actual code, and checks that every file and line it cites exists.

**What do I need?** macOS or Linux (Windows through WSL), Node 22+, git and Claude Code.

**How do I upgrade?** Run `npx @deepklarity/harnesskit@latest init` again. Files you've edited are left alone.

**How do I remove it?** Delete `docs/harnesskit/`, `writeup/agent-readiness/` and anything setup told you it installed.

---

Contributions welcome: `npm install && npm run verify` runs the checks. harness-kit is 0.x, so things may still change ([changelog](harness-kit/CHANGELOG.md)). MIT licensed. Made by [DeepKlarity](https://deepklarity.com).

---

This repository also holds odin, a multi-agent orchestration CLI, and taskit, a task board: [their README](docs/odin-and-taskit/README.md).
