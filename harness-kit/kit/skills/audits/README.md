# audits

Read-only audits that produce a page (`hk-autonomy-audit` a markdown file). Each skill's tier is a row in [`../skills.csv`](../skills.csv); the full list of categories is [`../README.md`](../README.md).

| Skill | What it does |
|---|---|
| [audit-agent-efficiency](audit-agent-efficiency/SKILL.md) | Read a finished agent's transcript, find the turns it wasted, and put a fix on disk for each one (a CLAUDE.md line, a script, a breadcrumb, a pattern) so the next run is fast. |
| [debuggability-audit](debuggability-audit/SKILL.md) | Find where a failure leaves no usable evidence, in the logs or on the screen: swallowed exceptions, context-free logs, silent fallbacks, errors caught and only logged, error fields no component reads; scored out of 10, guard mode checks the diff. |
| [hk-arch-audit](hk-arch-audit/SKILL.md) | Audit a codebase against eight agent-native principles, one sub-agent each, and write one page in writeup/ with findings ranked by severity out of 5 and a score out of 10. |
| [hk-autonomy-audit](hk-autonomy-audit/SKILL.md) | Judge whether an agent could take one area from symptom to verified fix with no person in the loop, and write up the gaps as one committed markdown page in docs/loop_audits/ with a score out of 10; the one audit that writes no HTML. |
| [hk-code-quality-audit](hk-code-quality-audit/SKILL.md) | Find what nobody would miss and what reads as generated, and write it up as one HTML page scored out of 10: dead code, pasted templates, wrappers with one caller, scratch files, hardcoded hosts, unused packages. |
| [hk-chart-validation](hk-chart-validation/SKILL.md) | Check that the charts in a codebase tell the truth: chart type, axis baselines, aggregation, units, labels, tooltips and how they render in Chrome at each width; writes the findings as one HTML page with a score out of 10. |
| [hk-debug-readiness-audit](hk-debug-readiness-audit/SKILL.md) | Rank the flows in this codebase that most need a debug page, up to five, with a build brief for each, as one page in writeup/. |
| [hk-mobile-app-ui-validation](hk-mobile-app-ui-validation/SKILL.md) | Drive a native iOS or Android app on a device or simulator through the mobile MCP, screenshot every screen, measure touch targets, and write up mis-taps, clipped text and screens that trap the user as one HTML page with a score out of 10. |
| [hk-read-path-audit](hk-read-path-audit/SKILL.md) | Audit the documents an agent reads for a job, as one HTML page scored out of 10: their token cost, what they state twice, where they contradict each other or the code, what is read and never used, each with the exact edit; applies the edits when asked. |
| [hk-security-audit](hk-security-audit/SKILL.md) | Run this skill's security_audit.py over the repo, verify each hit by reading the code, and write up to ten findings as one HTML page scored out of 10: leaked secrets, endpoints without auth, credential mounts, OAuth tokens in sandboxes, egress defaulting to allow, shell=True. |
| [hk-surface-design](hk-surface-design/SKILL.md) | Score one surface a person reads or answers (board, decision page, report, markdown, chat summary) out of 10 against the visual clarity rules, with up to ten exact edits in the template or renderer that made it; applies them when asked. |
| [hk-test-case-audit](hk-test-case-audit/SKILL.md) | Write up this repo's test gaps as one HTML page scored per suite: what has no test, which tests pass with the code broken, which bill a provider, what to write first. Reads a directory or PR; runs the four suites when asked to score them. |
| [hk-web-app-ui-validation](hk-web-app-ui-validation/SKILL.md) | Walks a running web app in Chrome at three widths and reads its fetching components, then writes up layout defects, dead ends, broken links, forms, accessibility failures and missing loading, empty or error states as one page scored out of 10. |
| [skill-audit](skill-audit/SKILL.md) | Audit a skill under docs/skills and write up, as one HTML page, whether an agent can run it as written: a verdict, a score out of 10, and ranked findings each with a severity out of 5 and the fix to apply. Applies the rewrite when asked. |

## Score, 0 to 10

Every audit that scores uses this table and the severity words 1 to 5 (5 worst); each skill keeps only its own severity anchors. The findings set the band, judgment picks the number inside it, and one sentence beside the score says what moved it. When a set matches two rows, the lower band applies.

| Findings | Band |
|---|---|
| none | 10 |
| nothing above severity 2, or one severity 3 | 8 to 9 |
| two or three severity 3, or one severity 4 | 6 to 7 |
| two severity 4, or four or more severity 3 | 4 to 5 |
| three or more severity 4 | 2 to 3 |
| any severity 5 | 0 to 1 |

## Closing step

Every audit ends with these three, after its page or chat report:

1. Each finding of severity 4 or 5 becomes a row on the active mission-control board (`kind` bug or suggestion), the proposed fix as the row text. Render the board in the same turn.
2. A cause that recurs across findings or across runs becomes a pattern, written to `docs/patterns/` following the pattern file format (append to the matching file, never fork).
3. Append one line to `writeup/audits/ledger.csv` (create it with the header if missing): `date,skill,target,score,top_finding`.

`hk-debug-readiness-audit`, `audit-agent-efficiency` and `skill-audit`'s apply mode rank or edit rather than score; they still take step 1 and the ledger line, with `score` left empty. The exception is the re-audit page that `skill-audit`'s apply mode writes: it carries its own score, and the ledger line takes it.

## Model

Audits judge, so they run on the session's strongest model.

## Screenshots

UI audits write screenshots to `writeup/shots/<skill>/` and delete only that folder, so two audits in one session never touch each other's files.
