---
name: hk-debug-readiness-audit
description: "Rank the flows in this codebase that most need a debug page, up to five, with a build brief for each, as one page in writeup/. Triggers on: 'debug page audit', 'suggest debug pages', 'what should have a debug page', or /hk-debug-readiness-audit."
---

# Debug page audit

A debug page runs one flow's production code on one input, with the flow's config editable
on the screen, shows the raw input and output the main flow hides (the prompt sent, the raw
reply, the parsed result, tokens, cost, time), and saves nothing. This audit finds the flows
that most need one, up to five, and writes `writeup/debug-readiness-<repo>.html`, where
`<repo>` is the basename of `$(git rev-parse --show-toplevel)`. Whether a failure leaves evidence in the logs is
`debuggability-audit`; whether an agent could close the loop on an area is
`hk-autonomy-audit`; this skill asks which flows need a page to see them run.

## 1. Scan

Dispatch one haiku Explore subagent to list flows where data transforms, branches or leaves
the process and the result is hard to see: LLM and model calls, multi-stage pipelines, async
jobs, parsers, matchers, scorers, validators, external-service clients. It skips plain CRUD
and any flow that already has a debug page or a CLI replay script, and returns, per flow: the
entry file, the stages, what leaves the process, and how the result is observed today. Take
back that list, not file dumps. No flows found: say so in one chat line and write no file.

## 2. Rank

Before ranking, open each candidate's entry file and confirm the stages it names; drop any
that do not check out. Count every trait that applies; more traits rank higher. Order by debugging pain, highest first: LLM-involved, then multi-stage, then
non-deterministic, then external-service. A flow whose failures cannot be reproduced by
reading the code, only by seeing the real input and output, ranks above one that can. An
existing CLI replay script is evidence of pain, so a flow that has one but no page still
ranks high. Keep the top five. Done when each kept flow has one sentence saying why it
outranks the one below it.

## 3. Build the page

Read `docs/playbook/tone_and_taste.md`, then build the page as
`docs/skills/knowledge/hk-writeup/SKILL.md` steps 3 to 5 say, in
`docs/skills/knowledge/hk-writeup/SHELL.html`. On the page, in order:

1. Kicker "Debug page audit", the repo name as the title, chips for the commit and the count
   of flows scanned.
2. A table of the ranked flows: rank, flow, and one plain line saying what its debug page would let a
   person do.
3. One `<details class="row">` per flow, rank as `.row-name`, flow name as `.row-status`, with
   three parts, each under a bold lead-in:

   **Why it needs a page.** Bullets, each opening with a bold lead-in ("Non-deterministic",
   "Output discarded", "No isolation") and one clause of real pain: the model varies, the
   raw reply is thrown away before anyone sees it, the error recurs in the log, the only way
   to test is to change production config.

   **What the page does.** An inputs table (`<div class="kvwrap"><table class="kv">`): one row per control, using
   this codebase's own nouns. Selecting an existing record loads its data; only a fresh input
   is ever uploaded. Then the flow's knobs (provider, model, threshold) and the config values
   the page lets you override without touching the stored ones. An outputs table: one row per
   block shown (the input sent, the raw reply before parsing, the parsed result, tokens, cost,
   time). One line on compare: the same input across models or configs side by side. Then the
   sentence "Nothing is saved to the database."

   **Build brief.** One paragraph an agent can build from with nothing else open: the same
   selectors, overrides and output blocks in product terms, no file or function names, ending
   by naming an existing debug page in this repo to copy, or saying there is none yet.

   Then one sentence on how much it hurts and how often, and the entry file in `.mono`.

Done when the file exists with one row per ranked flow, up to five. Say in chat where it is
and the flows in order.

## Closing step

Closing step in [../README.md](../README.md#closing-step): one board row per ranked flow, the build brief as the row text; ledger line with `score` empty.

## Example

- If the repo already has a debug page, name it and the route that reaches it, so the audit does not propose a second one.
