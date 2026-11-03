# Root CLAUDE.md Audit

A root CLAUDE.md is read by every agent on every turn, so its job is to route: send each kind of task to the one doc that owns it, state the few rules that must never be missed, and stop. Depth lives one hop away. Run this audit on the draft before it becomes `CLAUDE.md`, and again whenever the file changes.

Mechanical items are run by one command from the workspace root; the rest are a read. Fix every fail, run again, and record the before and after pass count in the initiative's `log.md`.

```bash
node docs/harnesskit/templates/scripts/check_claude_md.mjs CLAUDE.md   # one line per failure, last line "passed N of M checks"
```

## Mechanical (the script)

| # | Check | Pass | Fix |
|---|---|---|---|
| 1 | No unfilled `{{slot}}` | none left | Fill it from the code, or drop the line. |
| 2 | Length | at most 160 lines and 1,500 words outside comments (the word cap is the token budget: every word is read every session) | Move the longest section into an index or a page and leave a link. |
| 3 | No status phrases ("none yet", "not written yet", "no breadcrumb yet", "TBD", "no debug scripts ship") | none | Drop the line. A file read every session and updated last must not say what is missing; the readiness board says it. |
| 4 | Required sections: Read before you act, Methodical Problem-Solving, Testing, Subagent Strategy, Tool Rules, Stop and Ask | all present | Restore from `templates/CLAUDE.md`. |
| 5 | Read-before-you-act table has at least three rows | yes | Add the rows for the tasks agents get wrong here; each points at the doc that owns the rule. |
| 6 | Breadcrumb-first and diagnostics-first (or a way to reproduce) stated | both found | Restore the Methodical Problem-Solving rules. |
| 7 | A verify command sits in a fenced block under Testing | yes | Put the gate command there, verbatim from CI or the docs. |
| 8 | Every relative path, link and `#anchor` named exists | none missing | Correct the path, or delete the claim. |
| 9 | No setup-time rule: no `protected.txt`, no "while harness setup runs" | none | Delete the sentence; it ended with setup and makes agents check permissions. |
| 10 | Every routing row targets an existing file: its right column names a path or link, and each one exists | none missing | Point the row at its entry file, or drop the row. |
| 12a | The routing table names each of the three entry files that exists: `docs/breadcrumb_analysis/_INDEX.md`, `scripts/README.md`, `docs/_INDEX.md` | all named | Add the row; guides, testing, playbook and patterns route through `docs/_INDEX.md`. |
| 11 | The find-the-row rule is in Tool Rules: "Find the row first. When a row matches, open its page or run its tool before any grep..." | found | Restore it from `templates/CLAUDE.md`. |

## Read (a person or a mid-tier model)

| # | Check | How to verify | Fix |
|---|---|---|---|
| 12 | Routing, not content: no recipe, no function names, no list of what a doc holds | Read each section; ask "would this be wrong within a release?" | Replace with one line and a link. |
| 13 | Hierarchical disclosure: the file routes to three entry files; the breadcrumb and tools entry files list only areas (folders), one line each, 5 to 12 rows, no answers or `path:line`; each area folder has its own index (`_INDEX.md`, `README.md`) with one row per page or script (answer or command, and the page). No detail an index already holds is repeated here | Open each entry file, then one area index, then one page. `check_harness.mjs` scores the same shape as its `index` area. | Move the detail down a level; split an entry file over 12 rows into areas; add the missing area index. |
| 14 | Every table row names a task an agent actually meets, not a topic | Match each row to a real recent task; the fixed rows are all there, including the three entry-file rows (flows, tools, docs). | Rewrite as "About to <action>"; restore a dropped fixed row. |
| 15 | Every command runs | Run the gate and the start command (background if slow); Testing opens with the one-line runner fact (which runner, which command) quoted from the testing doc, not restated. | Fix the command or mark the file for the board; add the runner line. |
| 16 | Hard rules are phrased as rules with a reason: never wait in a foreground call, background anything over a minute, parallel independent calls, a script a later run needs is a tool; search names a command that exists (`grep -rn`/`rg`), not a tool this session may lack; the zsh glob-quoting line is present when `echo $SHELL` says zsh and absent when bash | Read Tool Rules; run `echo $SHELL`. | Rewrite as an instruction plus why; fill or drop the shell line. |
| 17 | Subagent rules name the agent, the model tier and the subagent guard | Read the section. | Restore from the template; correct names to this harness. |
| 18 | Stop and Ask lists real stops: spend over a cap, protected paths, others' uncommitted changes, a fix that failed twice, contradicting docs | Read the section. | Add the stops this workspace has; drop made-up ones. |
| 19 | Shape fits the workspace: a library has no stack or request flow, no "app" wording, no start command it does not have | Read the first screen as a stranger. | Drop the line; never write "none". |
| 20 | The write boundary and costs match the guard list and the billing facts | Compare with `.claude/hooks/protected.txt` and the testing doc. | Make the three agree. |
| 21 | Every claim about the code checked against the code | Spot-check five. | Correct or delete. |

A pass on 1 to 11 with a fail on 12 or 19 is still a fail: a file can be clean and still read as a manual.
