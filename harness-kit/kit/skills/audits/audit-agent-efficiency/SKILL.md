---
name: audit-agent-efficiency
description: "Read a finished agent's transcript, find the turns it wasted, and put a fix on disk for each one (a CLAUDE.md line, a script, a breadcrumb, a pattern) so the next run is fast. Triggers on: 'audit that agent', 'why was that slow', 'review the agent's run', or /audit-agent-efficiency."
argument-hint: "[transcript path or task id, and what the task was]"
---

# Audit an agent's efficiency

Read one finished run, list every stretch where the agent spent turns it did not need to, and
fix the thing in the repo that made it spend them. The output is edits on disk plus a short
chat summary. No page, no report file.

Context for this run: $ARGUMENTS

## 1. Get the transcript

Three places a run's transcript lives here. Pick the one that matches what you were given.

| You were given | Where the transcript is |
|---|---|
| A Claude Code session, or nothing | `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`, where the slug is the working directory with every `/` replaced by `-`. `ls -t` that directory; the newest file is this session, so take the one below it unless the user named a session id. |
| An orchestrator task id | the task runner's own log location, named in CLAUDE.md. |
| A task id from the project's task board | the board's own diagnostic script, named in CLAUDE.md (its comments carry the agent's turns and proof of work). |

A session file over about 2,000 lines is too long to read whole. Dispatch one haiku subagent
to condense it into a numbered list, one line per turn: tool, target, and whether it succeeded.
Judge the waste yourself on that list; open the raw transcript only for the stretches you
flag.

## 2. Mark the waste

Walk the turns in order and mark every stretch that a better-prepared agent would have skipped.
The shapes that recur in this repo:

- **Hunting for a fact the repo already states.** Searching for the test command, a log path, a
  script name, a port, when a `CLAUDE.md`, a project's own docs, or `docs/breadcrumb_analysis/`
  file has it.
- **Tracing a flow by hand** through frontend, API, worker and CLI that
  `docs/breadcrumb_analysis/_INDEX.md` already maps.
- **Answering a data question from code** instead of running the diagnostic script named in
  CLAUDE.md for that entity, or a direct read-only query when none exists.
- **The same command failing twice** for the same reason (wrong directory, missing venv, missing
  env var) before the agent found the fix.
- **Reading whole files for one value**, or reading five files in sequence in the main context
  when one haiku subagent could have returned the answer.
- **Independent subagents launched one after another**, each waiting on the last.
- **Running the full suite** to check one change when the scoped verify gate named in CLAUDE.md
  would do.
- **Asking the user** something the repo answers.
- **Fixing a symptom, then the next one, then the next**: a cascade that means the first diagnosis
  was wrong.

For each stretch write down: turns where it starts and ends, how many turns it cost, and the
one thing that would have made it unnecessary. Wasted turns that came from the task itself
being hard are not waste; leave them out.

## 3. Put a fix on disk for each one

Group the stretches by cause, then fix each cause where it holds:

| The cause | The fix | Where |
|---|---|---|
| A command, path or convention the agent could not find | Add the one line that states it | The nearest `CLAUDE.md`: root for repo-wide facts, a project's own `CLAUDE.md` for one project |
| A fact a doc states wrongly or stale | Correct the doc in place | The doc that misled the agent |
| A multi-command hunt for a project's own data | A script with `--brief` and `--json` modes, like its neighbours | That project's own diagnostic-scripts folder, named in CLAUDE.md |
| A multi-command hunt that is not project data | A script | `scripts/` |
| A flow traced by hand that no breadcrumb covers | A breadcrumb, with `/hk-breadcrumb-creator` | `docs/breadcrumb_analysis/` |
| A principle that would have prevented the cascade | A pattern, appended to the matching file | `docs/patterns/` |

One fix per cause, in the one place it belongs. A rule the agent already follows by default
is not a fix; a line that only helps this exact task is not one either. When the cause is a
missing tool that would take more than an hour to build, write the request as one paragraph in
the chat summary instead of building it.

## 4. Check and report

Done when every stretch you marked has a file edit on disk or one sentence in the summary
saying why it does not. Then post in chat, in this order:

1. Total turns, turns wasted, and the share.
2. One line per stretch: turns it cost, the cause, the fix, and the file you changed.
3. Anything you could not fix and why.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step) (no severities here): board rows for the stretches you could not fix, a pattern for a recurring cause, one ledger line with `score` empty.

## Example

- Agent transcript: the log or output file your orchestrator writes per task, or its log command.
- Diagnostic script for your backend's data: the script in your testing-tools folder that dumps one record.
- Scoped verify: the verify gate in CLAUDE.md, for the suite the change touched.
