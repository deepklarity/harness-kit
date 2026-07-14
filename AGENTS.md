# AGENTS.md

You are a coding agent in the harness-kit repo. Start here.

## What this repo is

A toolkit for humans and AI building software together: an orchestration CLI
(`odin/`), a task board (`taskit/` — Django API + React UI), a provider quota
checker (`harness_usage_status/`), and portable engineering patterns
(`.claude/skills/`, `docs/patterns/`).

## If you were sent here to adopt the patterns into another repo

Read [`docs/Quickstart.md`](docs/Quickstart.md) and follow it exactly. It is
written for you.

## If you are working on this repo

- Read [`CLAUDE.md`](CLAUDE.md) — the full operating manual (memory policy,
  git safety, testing discipline, subagent strategy). It applies to every
  agent, not just Claude.
- Read [`odin/docs/philosophy.md`](odin/docs/philosophy.md) — the 20 tenets
  every change is judged against.
- Each project has its own `CLAUDE.md`/`README.md` with commands and
  conventions.

## Commands you'll need

```bash
./dev.sh                 # start backend :9100, dashboard :9200, workers
source .venv/bin/activate && odin doctor    # check providers
scripts/verify.sh        # the one gate: all test suites, non-zero on any red
```

## Hard rules

- Never run `git stash` in the main checkout (parallel agents share it).
- Never edit files inside `.odin/worktrees/`.
- Tests pass ≠ done — verify the change live, then show the evidence.
- Write like you talk: [`docs/fable_roadmap/TONE.md`](docs/fable_roadmap/TONE.md).
