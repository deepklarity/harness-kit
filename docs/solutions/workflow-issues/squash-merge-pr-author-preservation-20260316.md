---
title: "Squash-merging PRs with author preservation and descriptive commit messages"
date: 2026-03-16
category: workflow
tags:
  - git
  - github
  - squash-merge
  - pr-workflow
  - commit-messages
component: cross-cutting
severity: low
status: documented
---

# Squash-merging PRs with author preservation and descriptive commit messages

## Problem

When squash-merging a PR via `gh pr merge --squash`, two things can go wrong:

1. The `--author-email` flag fails with `GraphQL: Invalid email address` if the email isn't verified on GitHub — even when `gh api users/<login>` returns that exact email. The error is misleading; it's a GitHub-side verification constraint, not a format issue.

2. Without `--subject` and `--body`, the squash commit defaults to the PR title (often a branch name like "Feature/task image upload") and a body listing every individual commit message — neither of which communicates what the PR actually did.

## Insight

**GitHub automatically attributes squash commits to the PR author when they're the sole contributor — no `--author-email` flag needed.** The flag only matters when you want to override the default attribution (e.g., crediting a different person). For single-author PRs, skip it entirely.

The real value-add is in `--subject` and `--body`: write a commit message that summarizes what changed and why, not just the branch name.

## Solution

Standard squash merge for a single-author PR:

```bash
# Step 1: Review commits to understand what changed
gh pr view 39 --json commits --jq '.commits[] | "\(.oid[:7]) \(.authors[0].name) <\(.authors[0].email)> - \(.messageHeadline)"'

# Step 2: Squash merge with a descriptive message
gh pr merge 39 --squash \
  --subject "Add reference image upload and lightbox preview for tasks" \
  --body "- Add image upload with drag-and-drop support in task creation
- Add lightbox component for previewing uploaded images
- Add image input support in model configurations"
```

If you need to override the author (multi-author PR, or attributing to someone other than the PR creator), `--author-email` requires the email to be a verified GitHub email for that user. Check with:

```bash
gh api users/<username> --jq '.email'
```

If that returns `null` or the email isn't verified, `--author-email` will fail. In that case, merge locally:

```bash
git fetch origin main <branch>
git checkout main
git merge --squash origin/<branch>
git commit --author="Name <email>" -m "Subject" -m "Body"
git push origin main
```

## Prevention / Reuse

When squash-merging any PR:

1. Read the commit list first (`gh pr view <n> --json commits`) to understand the full scope of changes
2. Write a `--subject` that describes the user-facing outcome, not the branch name
3. Use `--body` with bullet points summarizing the key changes
4. Skip `--author-email` for single-author PRs — GitHub handles attribution automatically
5. Only reach for `--author-email` when overriding attribution, and verify the email is GitHub-verified first
