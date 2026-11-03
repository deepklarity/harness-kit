---
name: build-debug-tool
description: "Turn a repeated hunt through logs, JSON dumps or database rows into a permanent diagnostic tool, at the moment you notice the pain. Triggers on: 'build a debug tool', 'make this dump readable', 'I keep grepping for this', 'I keep typing this python -c', or /build-debug-tool. Test commands and test data belong to hk-testing-shortcuts."
---

# build-debug-tool

Build the tool the moment you are reading more than the answer needs, retyping the same extraction, or chaining grep and python over JSON to answer a question you have asked before. A permanent, named tool; raw logs and ad-hoc scripts are the fallback for when the tool itself looks wrong, never the routine. Before this workspace's diagnostic scripts existed, one session spent an hour grepping escaped log lines and retyping the same ten-line python three times; the scripts then answered every question in seconds.

## The tells

Any one of these means stop and build:

- You typed nearly the same extraction twice (a grep chain, a `python -c` over JSON, a jq pipe).
- You are reading a wall of output to find one fact (a giant debug dump, an escaped multi-line error, a 300-line JSON).
- A check failed and the reason lives somewhere you cannot read with one command.
- You wrote a "temporary" script in the scratchpad that you would want again tomorrow.

## Where the tool goes

Look first at what exists: the workspace's diagnostic-scripts folder, its run/log command's help, and its repo-wide scripts folder (CLAUDE.md names each). Extend one of those when it already answers part of the question; a new mode or `--sections` entry on an existing script beats a new script.

1. **A service's own data** (its entities, records, jobs): a script in that service's diagnostic-scripts folder (CLAUDE.md), following the shared-helper pattern its neighbours use so it gets `--brief`, `--json`, `--full` and `--sections`. It reads the data directly (through the app's own data layer, or a direct query) rather than over the network when the network path needs auth the script can bypass. Add a row to the "when to reach for which script" table in the root `CLAUDE.md`.
2. **Orchestration runs and logs**: a flag on the workspace's run/log command or CLI. Log extraction is the right answer only when the fact cannot be made to land somewhere a script can read it (an error inside a rolled-back transaction never reaches the run's own record).
3. **Repo-wide checks and comparisons**: a script in the repo-wide scripts folder, the shape of the verify gate: one command, a one-line verdict, evidence attached on failure.
4. **Test commands, test IDs, how-do-I-test knowledge**: `hk-testing-shortcuts` owns it; call the Skill tool with `hk-testing-shortcuts` and stop here.

## The bar

- One command, one question answered, one screen of output.
- Works from the ID forms a person actually has (a task id or its board key, a spec id or its slug).
- Prints the denominator with the fact ("comments: (none)" beats empty output).
- A time budget where hanging is possible; on failure, print the evidence, not just the failure.
- Stays in the same change as the work that needed it, so it is committed together when the user commits. An uncommitted tool is a snippet.

Done when the tool answers the question that caused the pain in one command on the real case, and the chat reply gives that command line.

## Example

- Existing tools: list your testing-tools folder, your CLI's `--help`, and `scripts/`.
- A data script (records, boards, reports) lives in the diagnostic-scripts folder and reads through the app's own data layer; CLAUDE.md rules out curl and raw API calls.
- A CLI log tool is a flag on the existing log command.
- A repo-wide script has the shape of `scripts/verify.sh`.
