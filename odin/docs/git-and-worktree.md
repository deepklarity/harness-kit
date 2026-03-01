# Git and Worktrees

## The problem

An agent finishes a task. Where did the code go?

Right now, agents write to whatever directory Odin points them at. The output exists on disk — maybe committed, maybe not, maybe mixed in with another agent's half-finished work. If two agents run in parallel, they stomp on each other's files. If a task fails and gets retried, the failed attempt's changes are just… gone. Overwritten. No trace.

That's not version control. That's a shared scratch pad.

When humans work on a codebase together, they don't all edit the same checkout simultaneously. They use branches. They commit checkpoints. They open pull requests. When someone's work is bad, you revert. When someone takes over mid-task, the git log shows the handoff.

Agents should work the same way.

## Why git

Code lives in git. Agents write code. So Odin needs to understand git.

Not every task involves git — an API call or a prose generation might not. But when the work is code, git is how it gets isolated, checkpointed, reviewed, and rolled back. A commit hash is proof that specific changes existed at a specific time. A branch diff shows exactly what a task produced. A PR links the work to a reviewable surface.

Odin already has proof-of-work. Git makes that proof concrete — diffs instead of descriptions, hashes instead of status flags.

## Layers of control

A solo agent writing a poem doesn't need worktrees. A five-agent parallel codebase rewrite does. Odin's git management is opt-in and layered.

### Level 0: Just commit

The default. Odin doesn't manage branches. Agents work on whatever branch is currently checked out. Commits happen there.

Fine for simple specs, single-agent work, or non-code tasks. No configuration needed.

### Level 1: Branch per spec

Odin creates a branch when a spec starts: `odin/<spec_id>`. All tasks commit there. When the spec is done, the branch is ready for review and merge.

The human sees the spec's work as one branch diff. `gh pr create` opens a PR from it. Merging to main is the moment the work becomes official.

This is the natural level for most real work. Isolation from main, a clean diff, an obvious merge point.

### Level 2: Branch per task, worktrees for parallelism

When agents run in parallel — wave 2 of a DAG with three concurrent tasks — they need physical isolation. Two agents can't safely write to the same directory at the same time.

Git worktrees solve this. Each parallel task gets its own worktree: a separate directory with its own checked-out branch, backed by the same repository. The agent works in its worktree, commits to its task branch, and when done, the branch merges back to the spec branch. The worktree is cleaned up.

Task branches follow a convention: `odin/<spec_id>/<task_id>`. Each task's diff is inspectable in isolation. Conflicts surface at merge time, not during execution.

Worktrees are created on demand under `.odin/worktrees/` and destroyed after merge. The executor handles the lifecycle. The agent just sees a working directory.

## Scenarios

### The happy path

A spec has a scaffold task, three parallel writing tasks, and an assembly task.

Odin creates `odin/sp_a1b2` from HEAD. Scaffold runs, commits. The three writing tasks run in parallel — Odin creates worktrees, each on a task branch off the spec branch. Agents work in isolation. Each commits. Each merges back. Assembly runs on the spec branch, commits the final result.

`gh pr create`. Review. Merge.

Git log on the spec branch: scaffold commit, three merge commits, assembly commit. Clean and readable.

### Reassignment mid-task

Gemini starts a task, makes two commits. The user reassigns to claude.

Claude picks up where gemini left off. Claude's commits follow gemini's on the same branch. The git log shows both contributors — commit authors tell you who did what.

No reset, no cleanup. The work continues.

### Failed task, retry

A task fails. Bad output, crash, tests didn't pass.

The failed agent's commits stay on the branch — they're evidence. Odin tags the failed HEAD: `attempt/<task_id>/1`. The branch resets to its starting point. Retry begins clean.

Now the proof shows two attempts. Attempt 1: tagged, inspectable, failed. Attempt 2: the current branch, succeeded. A reviewer can diff the two to see what went wrong and what fixed it.

If the retry uses a different agent, the tag captures the first agent's work, the second agent starts fresh. TaskIt records both assignments. Git tags record both attempts. Nothing is lost.

### Human takes over

An agent is struggling. The human takes the task: `odin assign hero human`.

The human works on the task branch directly. They commit, run tests, mark it done. If they hand it back to an agent, the agent picks up from the human's last commit.

The branch history reads: agent commits → human commits → agent commits. The full collaboration, readable in `git log`.

### Rollback after completion

The spec is done. The human reviews and realizes one task's work is wrong.

That task's merge commit to the spec branch is identifiable. Revert it. The spec branch returns to the state before that task was integrated. The task goes back to "assigned." Re-run. New commits, new merge.

The revert commit is proof of the rollback. The original merge commit is proof of what was rolled back. Nothing is hidden.

## Git and proof-of-work

Git doesn't replace proof-of-work. Proof-of-work lives in TaskIt — the result field, comments, screenshots, verification steps, timeline. That's where a reviewer goes to understand what happened.

Git provides the links. A task's proof includes a commit hash — click through to the diff. A spec's proof includes a PR URL — see the full change set and review comments.

It works both ways. TaskIt points to git: commit hashes, branch names, PR URLs on the task card. Git points to TaskIt: commit messages reference task IDs, PR descriptions link to the spec. A reviewer can follow links in either direction and get the full story.

## GitHub CLI as the glue

`gh` is how Odin talks to GitHub.

`gh pr create` for the spec branch. `gh pr comment` to attach per-task proof. `gh pr checks` for CI status. `gh pr merge` when everything is green.

Odin doesn't reimplement GitHub's review workflow. The spec branch becomes a PR. The PR becomes the review surface. GitHub's existing infrastructure — reviewers, CI, merge rules — applies as-is.

## What Odin manages vs. what it doesn't

**Odin manages:**
- Creating spec branches (Level 1+)
- Creating task branches and worktrees (Level 2)
- Committing agent output after task execution
- Merging task branches back to spec branches
- Tagging failed attempts before retry
- Cleaning up worktrees after tasks complete
- Opening PRs via `gh` when a spec completes

**Odin does not manage:**
- The main branch. Merges to main are human decisions.
- Conflict resolution. If a merge conflicts, the task surfaces it and waits.
- Force pushes. History is not rewritten.
- Branches outside `odin/*`. Everything else is untouched.

## The trust chain, extended

Proof-of-work describes a chain: agent → Odin → TaskIt → human. Git adds an artifact at every link:

```
Agent does work
    → commits to task branch
        → Odin merges to spec branch
            → TaskIt records result + links to commits/PR
                → PR surfaces the diff for review
                    → Human reviews, merges to main
```

The commit proves the work. The merge proves integration. The PR proves review. Main proves acceptance. And if any link breaks, git provides the mechanism to undo, retry, and move forward.
