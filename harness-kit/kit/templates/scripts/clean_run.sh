#!/bin/sh
# scripts/clean_run.sh '<command>': run a command on a clean checkout of HEAD, without touching this one.
# Use it to show a failure exists without your change. Never `git stash` for this: stash is shared state.
# Usage: scripts/clean_run.sh 'pytest tests/test_x.py'     (run from anywhere inside the repo)
set -u
[ $# -ge 1 ] || { echo "usage: clean_run.sh '<command>'" >&2; exit 2; }
ROOT=$(git rev-parse --show-toplevel) || exit 2
WT=$(mktemp -d "${TMPDIR:-/tmp}/clean-run.XXXXXX") || exit 2
trap 'git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1; rm -rf "$WT"' EXIT INT TERM
git -C "$ROOT" worktree add --detach "$WT" HEAD >/dev/null 2>&1 || { echo "clean_run.sh: git worktree add failed" >&2; exit 2; }
echo "clean_run.sh: running at $(git -C "$WT" rev-parse --short HEAD) in $WT" >&2
cd "$WT" && sh -c "$*"
