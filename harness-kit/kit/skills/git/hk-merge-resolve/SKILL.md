---
name: hk-merge-resolve
description: "Resolve the conflicts of an in-progress merge, rebase or cherry-pick, then run the affected suite with the verify gate the entrypoint names. Triggers on: 'merge conflict', 'resolve conflicts', 'conflict markers', 'rebase conflict', or /hk-merge-resolve."
allowed-tools: Bash, Read, Edit, Grep, Glob, AskUserQuestion
---

# Resolve merge conflicts

Resolve every conflict in the current checkout, verify the result with the repo's test gate, and finish the git operation.

## 1. Diagnose

```bash
git status
git rev-parse HEAD
git rev-parse MERGE_HEAD 2>/dev/null        # merge in progress
git rev-parse CHERRY_PICK_HEAD 2>/dev/null  # cherry-pick in progress
test -d "$(git rev-parse --git-dir)/rebase-merge" || test -d "$(git rev-parse --git-dir)/rebase-apply"   # rebase in progress (works in worktrees too)
```

Print "Safety checkpoint: HEAD is at `<sha>`" and the abort command for the operation, so the user has an escape hatch:

| Operation | Abort |
|---|---|
| merge | `git merge --abort` |
| rebase | `git rebase --abort` |
| cherry-pick | `git cherry-pick --abort` |

If tracked files outside the conflict are modified, stop and ask the user to put them on a temporary commit first. Follow CLAUDE.md's git safety rules for this checkout (for example, a shared checkout may rule out `git stash`).

## 2. Build context

Which side is which:

- Merge: ours is the current branch, theirs is the branch being merged in.
- Rebase: ours is the branch being rebased onto, theirs is the commit being replayed.
- Cherry-pick: ours is the current branch, theirs is the picked commit.

List every conflict with `git grep -n '<<<<<<<'`. Group them before editing: lock files, generated files and whitespace resolve mechanically; both sides touching different things in one region combine; both sides changing the same logic need the intent of each side, read from `git log` on both branches.

## 3. Resolve each file

Read the conflicted region with its surroundings, then write the final state: ours, theirs, or both intents combined into code that works, with imports, names and types consistent across the file. Remove every `<<<<<<<`, `=======` and `>>>>>>>` marker, then `git add <file>`.

When both sides made substantial changes to the same logic and the right result is not clear, show the user both chunks, what each side intended, and the resolution you propose, and wait for the answer before editing.

## 4. Verify

```bash
git diff --check
git grep -nE '^(<<<<<<< |=======$|>>>>>>> )' -- $(git diff --name-only --cached)
```

Both must print nothing; the one allowed hit is a doc that quotes the markers as an example, which you confirm by reading the line. Then list the files the operation touches and run the suite each maps to, per the verify gate's path-to-suite mapping (CLAUDE.md's Testing section):

```bash
git diff --name-only HEAD; git diff --name-only --cached
```

Run only the suites that match. A failure caused by the resolution (missing import, type mismatch, duplicated declaration) gets fixed and the suite re-run; a failure in a file the operation did not touch is reported as "not confirmed caused by this resolution", with the failing test named, and the user decides whether it blocks.

Report one line per check: markers, then each suite with its result.

## 5. Finish

Only after every check passes, or the user has accepted an unconfirmed failure:

- Merge: `git commit`, ending the message with the attribution trailer the harness gave this session (the Co-Authored-By line, plus the Claude-Session line when there is one).
- Rebase: `git rebase --continue`.
- Cherry-pick: `git cherry-pick --continue`.

Done when `git status` shows no unmerged paths and no operation in progress. Print the new HEAD sha.

## Example

- Git safety rule: `git stash` is off the table in the main checkout (CLAUDE.md).
- The path-to-suite mapping:

  | Path prefix | Suite |
  |---|---|
  | `frontend/` | the frontend suite |
  | `backend/` | the backend suite |
  | `cli/` | the CLI suite |
  | `tests/e2e_snapshots/` | the snapshot suite |
