---
name: hk-autonomy-audit
description: "Judge whether an agent could take one area from symptom to verified fix with no person in the loop, and write up the gaps as one committed markdown page in docs/loop_audits/ with a score out of 10. Triggers on: 'loop audit', 'agent readiness', 'could an agent fix this alone', 'can an agent fix this on its own', or /hk-autonomy-audit."
argument-hint: "[area or flow to audit, e.g. 'spec execution' or 'reflection reports']"
---

# Autonomy audit

An agent closing the loop on a problem goes through six stages: discover, diagnose,
hypothesize, fix, verify, document. This audit checks, for one area, whether the docs, tools,
commands and logs let an agent with only the repo's `CLAUDE.md` files and no tribal knowledge
get through each stage. Nothing else changes: a gap you could fix in a minute is still a gap
on the page.

The output is one file, `docs/loop_audits/<area-slug>.md`, plain markdown and committed
(CLAUDE.md names that folder as the permanent home for loop audit reports). It is the one audit
that writes no HTML page. Whether a failure leaves
evidence in the logs or on the screen is `debuggability-audit`; which flows need a debug page is
`hk-debug-readiness-audit`; this skill asks whether an agent could close the loop.

Target: `$ARGUMENTS`. When it is empty or names the whole repo, pick the area with the most
recent failures: grep the app log named in CLAUDE.md for `ERROR` or a traceback, and check the
newest orchestration run logs. Say which area you picked and why in one line, and audit that
one deeply. When it is a doc path, start there and trace outward to the commands, tools and
flows the doc names.

## 1. Scope

Read the area's `CLAUDE.md`, `AGENTS.md` and the docs they point at; check
`docs/breadcrumb_analysis/_INDEX.md` for a breadcrumb that covers it, and
the diagnostic-scripts folder named in CLAUDE.md for a script that covers it. Scan headings;
read the bodies of sections that name failures. Done when you can list the area's failure modes, its diagnostic scripts and
commands, its log files, and its docs.

## 2. The rating scale

Each stage gets one of three ratings, in plain words: **can** (tools exist, are documented and findable), **can with
waste** (partial tooling or docs; the agent gets there slowly or misses things), **gets stuck**
(a missing tool or doc, or knowledge that is only in someone's head). Rate the weakest
realistic case in the area: a diagnostic script that covers parse failures and nothing for
timeouts makes diagnose "can with waste". The overall rating is the weakest stage.

The ratings and the score are separate. The rating words give the overall rating. The score out
of 10 comes from giving each gap a severity 1 to 5 and reading the band table in
[../README.md](../README.md):

- 5: the agent is stuck on the area's main job.
- 4: a gap that blocks a common job in the area.
- 3: the agent wastes turns.
- 2: it finds the answer but the docs are stale.
- 1: a nit.

## 3. Walk the six stages

Launch one subagent per stage in a single message with `model: sonnet`, each with the scope list from
step 1, the three ratings and the weakest-case rule from step 2, and that stage's questions.
Each returns its answers with the file or command that backs each one, and its rating; the main conversation re-judges every rating.

**Discover.** Do error messages say what failed, or just "error"? Where are the logs, in what
format, can an agent tail them? Is there a health or status command?

**Diagnose.** Are there diagnostic scripts (the folder named in CLAUDE.md), and do
they explain what they find rather than dump data? Can the agent follow data from input to
symptom? Are common failures documented with their signatures?

**Hypothesize.** Do docs give the reason behind design decisions? Are edge cases and gotchas
written down? Are past incidents findable by symptom (`docs/patterns/`)? Is there enough
architecture to reason about side effects?

**Fix.** Are the commands that change state documented, not only the read-only ones? Are there
tests, linters and type checks that catch a regression? Can the agent tell which files to
touch? Are there past fixes to follow?

**Verify.** Is the test command written down and runnable (the verify gate, CLAUDE.md's Testing section)? Is
there a live check beyond unit tests? Is "working" defined? Can the agent confirm it without a
person looking at a screen?

**Document.** Is there a place and a shape for what was learned (a pattern appended to the
matching file in `docs/patterns/`), and would an agent find it?

Done when every stage has a rating with a backing file or command. The main conversation re-rates on the strongest model,
and changes a rating only where the subagent's evidence contradicts it.

## 4. List the gaps

For every stage rated below "can", each gap is one of: something the agent needs and cannot
find; something that exists but is not in any doc; a judgment call that could be written down;
something that works for one case in the area and not the others. Rank by how often it would
block an agent. Each gap carries a fix concrete enough to act on with no follow-up question:
the doc line to add, the script to write, the command to document, and a size (small, medium,
large).

## 5. Write the file

Read `docs/playbook/tone_and_taste.md`, then write
`$(git rev-parse --show-toplevel)/docs/loop_audits/<area-slug>.md` (`mkdir -p` the folder).
Ratings are the content, so they go in as words in a plain table. In order:

1. Title "Loop audit: <area>", the commit, and what prompted the audit if known.
2. One bold sentence with the overall rating, the score out of 10 and the stage that set it. Then a table, one row
   per stage: the rating and the one-line gap, or "none".
3. Gaps, ranked, each a heading with the stage, then what exists, what is missing, the fix, and the size, each
   with a bold lead-in.
4. What to do first: the top three to five fixes as an ordered list.
5. What already works, in two or three sentences, only if another area should copy it.

An area with no gaps gets rows that all say "can" and one line saying so; that is a finished
file.

Done when the file exists and every stage has a rating with a backing file or command. Then
post in chat: the rating table, the score, the top three gaps with their fixes, and the path.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- Recent failures: grep your app log for `ERROR|Traceback` and list the newest files in your agent-log folder.
- The diagnostic-scripts folder is the testing-tools folder named in CLAUDE.md.
- The verify gate is the command CLAUDE.md names for the suite.
