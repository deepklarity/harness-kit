# The guard hook

`guard.mjs` runs before every `Edit`, `Write`, `MultiEdit`, `NotebookEdit` and
`Bash` call and enforces the write boundary the entrypoint states. Init puts
ready-made copies under `docs/harnesskit/workflow/.claude/`; setup installs
them into the workspace's `.claude/` after the person agrees (merging
`settings.hook.json` into `.claude/settings.json`, never replacing it) and lists the repo folders the scaffold found in
`protected.txt` beside the guard, one per line. The guard itself stays
unedited, so `harnesskit init` can upgrade it.

## What it blocks

It blocks a write into a protected folder: an editing tool pointed at a
file inside one, or a Bash command that copies, moves, removes, touches,
`tee`s, `sed -i`s or redirects output into one. It also blocks three git
operations wherever they point: `git stash`, which in a shared checkout
silently swallows other agents' work; `git reset --hard`; and a force
push.

Nothing stops reading. Opening, searching, and running anything inside a
protected repo is expected; the boundary is on writing only. An agent that
stops exploring a repo because it may not edit it answers from the entrypoint
alone, and answers worse.

A blocked call exits 2 with one plain sentence on stderr saying what was
blocked and what to do instead, which is to put a suggestion on the readiness
board for that repo's owners.

## What it actually catches in Bash

The guard does not scan a command for merely mentioning a protected
folder — a heredoc, a comment, or a string that names one goes through.
It catches an output redirect (`>` or `>>`) whose target resolves into a
protected folder, and a `cp`, `mv`, `rm`, `touch`, `tee` or `sed -i`
followed, in the same simple command, by a path there; a repo named
before the verb, in another command of the same line, or inside a heredoc
body is text, and so is a blocked git command quoted in a heredoc. Everything else — reading,
searching, running, `python3 -c` writing a file, `npm install` with a
working directory inside the folder — is not caught by the hook; that
part of the write boundary is the rule the entrypoint states, not code
the guard enforces. And because paths are matched on whitespace, a
quoted path that contains a space can slip past both checks.

## It fails open

The wiring in `settings.json` checks that `guard.mjs` is there and lets the call
through with a message on stderr when it is not. A hook that blocks everything
when its own script goes missing locks the session out of every file tool,
including the one that would put the script back.
