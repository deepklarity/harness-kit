

# Harness Kit

**Human + AI task orchestration that compounds.**

A kit for building with AI agents — not just the orchestration, but also the engineering patterns around it. TDD-first execution, structured debugging, knowledge compounding, cost-aware delegation. Each run makes the next one better.

Note:
- This is currently experimental and is not sandboxed. We plan to add sanboxes and worktrees soon.
- This is WIP and a lot of things are rough around the edges and can break. This is an early sketch of the patterns and orchestration we find useful for building with AI agents, not a polished product. We are sharing it in this state to get feedback and contributions from the community as we build it out.
- Code is ephemeral. Fork it, rewrite it, build your own from scratch. The value is in the patterns and the orchestration, not the code itself. 
- The system is only as good as the specs you feed it. Spend time on the spec, not the code.
- We urge everyone to try out all other tools and as many as possible to find what works. This is just one approach that works for us, not the One True Way.


---
<br>




https://github.com/user-attachments/assets/52352361-99ed-4c07-83c8-a28dc3b3ba5c




---


## What it looks like

![Board — kanban with agent assignments and status columns](screenshots/board-view.png)
*Board — drag-and-drop kanban with agent assignments*

![Spec — cost breakdown, task timeline, multi-agent execution](screenshots/specs-view.png)
*Spec run — cost per agent, task timeline, proof of work*

![DAG — dependency graph with wave execution](screenshots/task-breakdown.png)
*DAG view — tasks decomposed into dependency waves*

![Just like humans collaborate](screenshots/task-detail.png)
*Task detail — agent output, comments, evidence*

---

## Quickstart
Start the backend, frontend and celery worker with:
```bash
./dev.sh
```

Harness-Kit has a cli tool needed to execute agents.
```
cd odin/
pip install -e . 
```

Opens at [localhost:5173](http://localhost:5173).

First run creates a venv, installs everything, migrates SQLite, and seeds agent users (~60s). After that, starts in ~3s. `Ctrl-C` stops all services.

Some sample specs are present in odin/sample_specs/web/apps/pomodoro_timer.md These could be run via odin cli.

---

## What's inside

| Directory | What it does |
| :--- | :--- |
| `./`| All the best practices & skills are present here |
| [`odin/`](odin/README.md) | CLI for multi-agent orchestration — plan, assign, execute, reflect |
| [`taskit/`](taskit/README.md) | Task board UI + Django API (kanban, DAG view, timeline, analytics) |
| [`harness_usage_status/`](harness_usage_status/README.md) | CLI to check AI provider quotas |

---

## How it works

**DAG orchestration.** Work decomposes into dependency graphs. Tasks execute in waves — independent work runs in parallel, dependent work waits. Failed task stops its dependents. No wasted compute.

**Suggestive defaults.** The planner recommends agent assignments based on cost, quota and capability. You override when you want. Everything works out of the box. It looks at remaining quota between Claude and Codex and decides to assign to Codex because it's cheaper and has enough quota, but you can override and assign to Claude if you want. The system is smart, but the human is in control.

**Reflection loops.** Plan → execute → review → adjust → execute again. Not one-shot. Human taste is the filter at every checkpoint. Council of LLMs is also there to reflect and catch mistakes before they become expensive. The system is designed for iteration and improvement, not "get it right the first time."

**Proof of work.** Every task carries evidence: agent output, comments, duration, cost. The board is the audit trail. One shouldn't have to go outside this board to understand what happened and why. MCPs already configured for web & mobile apps. Get the proof of work in the board itself.

**Cost-aware delegation.** Cheapest capable agent is the default. No overkill.

**AskUserQuestions.** When in doubt, ask rather than guess. Agents can ask questions during execution using MCP and wait for human input.

**Provider agnostic.** Claude, Gemini, Codex, Qwen, Kilo Code — agents are swappable behind a harness interface.

---

## Engineering patterns

The orchestration is half of it. The other half is the engineering discipline encoded around it — patterns we've found actually work when building with AI agents, not just for running them.

Generating code is the easy part. The hard part is knowing whether it's correct, maintaining it when you've forgotten how it works, and not repeating the same mistakes across sessions. These patterns address the hard part:

| Pattern | What it encodes |
| :--- | :--- |
| **[Red/green TDD](https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/)** | Test-writing agents receive *only* behavioral requirements, never implementation details. Tests must fail before implementation starts. The boundary is structural, not a suggestion. |
| **Mock-first development** | For complex features, mock the UI first, get human acceptance on the experience, then deepen layer by layer. Don't build the backend for an interface nobody validated. |
| **Structured RCA** | 7-step root cause analysis: reproduce → locate → hypothesis → failing test → fix → verify → document. No jumping to fixes. |
| **[Compound what you know](https://github.com/EveryInc/compound-engineering-plugin)** | Every solved problem becomes a searchable doc. Every debugging session can become a breadcrumb trace. Agents and future humans search this before re-exploring from scratch. |
| **[Cognitive debt](https://simonwillison.net/guides/agentic-engineering-patterns/interactive-explanations/) paydown** | Breadcrumb analysis and linear walkthroughs keep code explainable. If you can't trace how a feature works end-to-end, you've taken on debt. |
| **Loop audits** | Can an AI agent autonomously debug this area? If not, find the gaps — missing docs, missing tools, missing logs — before they bite you. |
| **Slop audits** | Codebase hygiene — dead code, misplaced files, security issues. AI output meets the same bar as human code. |

These are implemented as skills in `.claude/skills/` and documented in the CLAUDE.md files throughout the repo. They're not locked to this kit — take the patterns even if you don't use the orchestration.


---
## Motivation

We built this for ourselves. It encodes how we are actually (are figuring out) working and developing AI agents — [current tenets](odin/docs/philosophy.md) we arrived at by shipping, not theorizing. It's opinionated at some places, because that's what makes it useful for us.

The thing we kept coming back to: most AI tooling is one-shot. You prompt, you get output, you move on. Nothing accumulates.

*"Why doesn't it remember what we just said?"*
*"Why does it keep making the same mistake?"*
*"Just maybe this one extra prompt will fix it..."*

We built Harness Kit so that work accumulates. Spec runs produce reflections. Debugging sessions become breadcrumb docs. Solved problems get compounded into searchable knowledge. The system gets smarter because the *context* gets richer, not just because the models get better.

It works today — we ship with it daily. But the vision is bigger than where it is now, and each week we find ways to wire things tighter, compound more, waste less.

**The value isn't in any single feature — it's in the engineering patterns encoded into the system:**

- Writing code is cheap now. Writing *good* code — tested, proven, explainable — is the actual constraint. The kit is built around that distinction.
- Quality of the spec you start with matters more than the LLM's ability to "understand" a vague prompt
- Decompose work into dependency graphs, parallelize what's independent, serialize what depends
- TDD is structural, not aspirational — test-writing agents have no implementation context, so they *can't* skip the red phase
- Hoard what you learn — every solved problem becomes a searchable doc, every debugging session a breadcrumb trace. [Compound on what you know](https://simonwillison.net/guides/agentic-engineering-patterns/hoard-things-you-know-how-to-do/) so agents (and future you) don't rediscover from scratch
- Delegate to the cheapest capable agent — don't burn $0.15/call tokens on $0.01 work
- Keep **humans in control** with the board as the explainable source of truth

Fork it, rewrite it, build your own from scratch. Bespoke software that fits how *you* work is easier to build than ever.

---

---

## Philosophy

Full version: [`odin/docs/philosophy.md`](odin/docs/philosophy.md). The short version:

> **Everything is a task.** Assembly, review, testing — all tasks with dependencies. No hardcoded stages.
>
> **Cheapest capable agent.** Don't use a $0.15/call model when $0.01 works.
>
> **No slop.** AI output meets the same bar as human code.
>
> **Taste is the filter.** LLMs produce volume. Humans curate.
>
> **The board is truth.** If it's not on the board, it didn't happen.

---

## Resources

We keep collecting useful references as we discover them — engineering patterns, orchestration tools, agent harnesses, and write-ups that have shaped how we think about building with AI. Some good ideas.

| Resource | Note |
| :--- | :--- |
| [Agentic Engineering Patterns](https://simonwillison.net/guides/agentic-engineering-patterns/) | Simon Willison's guide (a work in continual progress)— red/green TDD, hoarding knowledge, cognitive debt. Overlaps heavily with what this kit encodes. |
| [StrongDM Software Factory](https://factory.strongdm.ai/) | Software factory approach |
| [Compound Engineering Plugin](https://github.com/EveryInc/compound-engineering-plugin) | Plan → execute → review → document workflow for Claude Code |
| [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) | AI-powered kanban boards |
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | Multi-agent orchestration framework |
| [Agent of Empires](https://github.com/njbrake/agent-of-empires) | Game-based agent coordination |
| [Visual Explainer](https://github.com/nicobailon/visual-explainer) | Agent skill that turns terminal output into styled HTML with Mermaid diagrams, dark/light themes, and real typography |
| [Pi.dev](https://pi.dev) | Minimal coding harness with plugins |
| [DeepWiki](https://deepwiki.com/) | Understand codebases better |

Many people are building in this space. We are excited to see how it evolves.

---

## Roadmap

- **Git Worktree** — Branch per spec, use worktrees for task isolation and easy cleanup
- **Mobile app** — Push notifications for MCP questions and task updates
- **Inbox-style view** — Like Cursor agents / Antigravity. Human only sees what is relevant to them, not the full board
- **More MCP & tools** — Better reflection, debugging and compounding on learnings
- **Analytics & reporting** — Agent performance, cost, and bottleneck insights
- **More skills** — Common workflows (debugging, code review, documentation) out of the box or customizable
- **Digital Twins** — Slack & Gmail for agents to communicate, spawn information collection subagents
- **Streamed agent output** — Faster feedback loops and better debugging on UI
- **More encoded patterns** — Linear walkthroughs for cognitive debt paydown, automated proof-of-work validation, pattern-level analytics (which engineering patterns are actually improving outcomes)

--------

## Troubleshooting:
If you get stuck, check the [troubleshooting guide](TROUBLESHOOTING.md) for common issues and fixes.

Built by [deepklarity.ai](https://deepklarity.ai).
