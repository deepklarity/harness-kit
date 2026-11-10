# Changelog

## 0.1.1

- The npm page links the walkthrough video instead of showing a bare link.
- `init` works in a folder that is not a git repository; measuring before and after needs git, and running `init` again after the first commit records it.

## 0.1.0

First release.

- `harnesskit init` seeds a repo with the setup guide, templates, checks and a board. It writes only `docs/` and `writeup/agent-readiness/`, and prints the setup journey and the one prompt to give your agent.
- Setup (`docs/harnesskit/entry.md`) asks six questions with recommended answers, then builds the harness from your code: a routing `CLAUDE.md`, breadcrumbs per flow, inspect and logs scripts per component, tests and a verify gate. Every change to `CLAUDE.md`, `AGENTS.md`, `.claude/` or `.gitignore` is shown and approved first.
- `check_harness.mjs` audits the harness before any proof.
- `harnesskit prove` runs scenarios before and after the harness in git worktrees, graded by hidden tests, a blind judge and a quality rubric, under a budget.
- About 40 skills for audits, root-cause analysis, review and write-ups, installed only if you choose them.
