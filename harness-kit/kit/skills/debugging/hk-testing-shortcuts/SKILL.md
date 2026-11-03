---
name: hk-testing-shortcuts
description: "Answer a testing question (a command, a path, how to get a test id) from the repo's own testing docs, and when the answer was not written down yet, save it as a guide in docs/testing_process/ or a script in the workspace's testing tools folder. Triggers on: 'how do I test', 'get me the ID', 'what's the command for', 'save this as a shortcut', or /hk-testing-shortcuts."
argument-hint: "[what you're trying to test or look up]"
---

# /hk-testing-shortcuts — Testing shortcuts

<context_hint> $ARGUMENTS </context_hint>

## Step 1: Read what the repo already says

Before deriving anything, check these in order; most questions end here:

1. `CLAUDE.md`, its Testing and Debugging sections: the suite table, the diagnostic scripts and their output modes.
2. The verify gate CLAUDE.md names: the one command for whether the repo is green.
3. The testing docs folder CLAUDE.md names, for guides on running things, live runs and finding ids.
4. The diagnostic-scripts folder CLAUDE.md names, for a script that already does the lookup.

If one of them answers the question, use it, post the Step 4 block with the answer line only, and stop.

## Step 2: Work it out

Do what was asked: find the id, run the command, set up the environment. Keep note of what you had to discover on the way: commands and what they are for, paths that matter (venv, config, fixtures), how to reach specific data, services or env vars that must be present, and the step that was not obvious.

## Step 3: Save the shortcut, when there is one

Only when Step 2 needed something Step 1 did not say. Otherwise the chat answer is the whole output.

**A guide** goes in the testing docs folder: extend the file that covers the topic, and open a new `<topic>.md` only when none does. Shape, keeping only the sections that apply:

```markdown
# <Topic>

## Quick reference
<the commands you'd want on a sticky note>

## Setup
<venv, env vars, services that must be running>

## How to
<the actual commands, in order>

## Useful paths

## Gotchas
```

**A script** goes in the diagnostic-scripts folder when the lookup took several steps and will be needed again. Follow the shared setup and output-mode pattern the scripts beside it use, put a usage line at the top, and say what is wrong when the env or a service is missing. Name it after what it answers.

## Step 4: Report

```
Answer: <the command or value>
Guide: <testing docs file> [extended | created | nothing new to record]
Script: <diagnostic-scripts file> [created | not needed]
```

Done when the answer is in chat and anything new is on disk in one of the two homes above.

## Example

- The verify gate: the script CLAUDE.md names, with a suite argument.
- The testing docs folder: `docs/testing_process/`, with one page per workflow (snapshots, live runs, finding ids).
- The diagnostic-scripts folder: your testing-tools folder, each script with `--brief` and `--json`.
