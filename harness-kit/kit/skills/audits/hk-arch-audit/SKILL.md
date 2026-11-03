---
name: hk-arch-audit
description: "Audit a codebase against eight agent-native principles and write one page in writeup/ with a score out of 10. Triggers on: /hk-arch-audit."
argument-hint: "[optional: one principle, e.g. 'action parity' or '1']"
disable-model-invocation: true
---

# Agent-native architecture audit

Eight principles, one sub-agent per principle, one page at `writeup/arch-audit-<repo>.html`
where `<repo>` is the basename of `$(git rev-parse --show-toplevel)`.

| # | Principle | What it asks |
|---|---|---|
| 1 | Action parity | Whatever the user can do, the agent can do |
| 2 | Tools as primitives | Tools provide capability, not behaviour |
| 3 | Context injection | The system prompt carries live app state |
| 4 | Shared workspace | Agent and user work on the same data |
| 5 | CRUD completeness | Every entity has create, read, update and delete |
| 6 | UI integration | Agent actions show up in the UI at once |
| 7 | Capability discovery | Users can find out what the agent can do |
| 8 | Prompt-native features | Features are prompts defining outcomes, not code |

The eight are this kit's own working definition of an agent-native codebase, not a cited standard; the coverage count each sub-agent returns (X of Y) is evidence for a gap, never the score.

## 1. Pick the principles

`$ARGUMENTS` empty means all eight. Otherwise match it to one row: a number, or a word from the
name (`action parity`, `tools`, `primitives`, `context`, `injection`, `shared`, `workspace`,
`crud`, `ui`, `integration`, `discovery`, `prompt`, `features`). No match: list the eight names and stop.

## 2. Run the sub-agents

Read `references/agent-prompts.md` in this skill's directory. Launch one sub-agent per chosen
principle, all in a single message, with the Agent tool, `subagent_type: general-purpose`,
`model: sonnet`, passing the output shape from the top of that file plus the principle's own
section. The main conversation re-judges every severity in step 3. Each returns an inventory table, a coverage line `### Coverage: X/Y`, the gaps each
with a severity, and what to change.

Done when every launched sub-agent has returned a `### Coverage:` line. A sub-agent that returns
prose without one is re-run once with the same prompt.

## 3. Severity and score

Severity is 1 to 5, 5 worst, by what an agent cannot do because of the gap: **5** a whole class
of user actions or entities the agent cannot reach at all, or an agent action that corrupts
shared data; **4** a gap that blocks a common job (an entity with no update, a tool that
bundles business logic so the agent cannot compose it); **3** a gap the agent works around at
a cost (state not injected, UI that lags the agent's change); **2** an inconsistency or a rare
action missing; **1** a naming or discoverability nit. Re-judge each sub-agent's severities
yourself against this scale, drop duplicates across principles, and keep at most ten findings.

Score 0 to 10 from the band table in [../README.md](../README.md): for the repo when all
eight ran, for the one principle otherwise.

## 4. Build the page

Read `docs/playbook/tone_and_taste.md`, then build the page as
`docs/skills/knowledge/hk-writeup/SKILL.md` steps 3 to 5 say, in
`docs/skills/knowledge/hk-writeup/SHELL.html`. On the page, in order:

1. Kicker "Agent-native audit", the repo name as the title, chips for the commit and the
   principles audited.
2. One bold sentence with the score out of 10 and what set it. Then a table with one row per
   principle audited: the coverage (X of Y), the worst severity, and one sentence saying what
   it means in this codebase ("the agent can do 12 of 20 things a user can; the eight missing
   are all board edits").
3. What to change, ranked by severity: one sentence per item, only items a sub-agent found,
   each with its severity, the principle and the file.
4. What already works, only where it is worth copying elsewhere; skip the section otherwise.
5. Each sub-agent's report as a `<details class="row">` with the principle as `.row-name` and
   its coverage line as `.row-status`.

Done when `writeup/arch-audit-<repo>.html` exists with one row per principle audited. Say in
chat where it is, the score and the count at each severity.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.
