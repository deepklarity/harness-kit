---
name: hk-merge-pr
description: "Squash-merge a GitHub PR with a commit message written from its commits and the repo's attribution trailer. Triggers on: 'merge this PR', 'merge PR', 'squash merge', or /hk-merge-pr."
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
---

# Merge PR

Squash-merge one GitHub PR. Takes a PR URL or a number; `gh` accepts either, so pass whatever the user gave as `<pr>` in every command below. A URL names its own repo, a bare number means the PR in the current checkout's repo. With neither, ask for one.

## 1. Read the PR

```bash
gh pr view <pr> --json title,body,author,state,baseRefName,headRefName,commits
gh pr view <pr> --json commits --jq '.commits[] | "\(.oid[:7]) \(.authors[0].name) <\(.authors[0].email)> - \(.messageHeadline)"'
```

Print two or three sentences on what the PR does and how many commits it has.

## 2. Check it can merge

```bash
gh pr view <pr> --json mergeable,mergeStateStatus,reviewDecision
gh pr checks <pr>
```

If the PR has conflicts, say so and offer `/hk-merge-resolve` on the PR branch. If `gh pr checks` lists a failing check, name it and stop.

## 3. Draft the commit message

- Subject: one line on the user-facing outcome, not the branch name.
- Body: bullets on the key changes, then the attribution trailer the harness gave this session (the Co-Authored-By line, plus the Claude-Session line when there is one).

If the commits have more than one author, list them under the draft and credit the PR author unless the user names another. Show the draft and get a go-ahead before merging; a squash on main cannot be undone quietly.

## 4. Squash merge

```bash
gh pr merge <pr> --squash --subject "<subject>" --body "<body>"
```

GitHub credits the PR author by default. Only when the user named another author add `--author-email <their email>`; it must be an email verified on GitHub.

## 5. Confirm

```bash
gh pr view <pr> --json state,mergeCommit
```

Done when the state is `MERGED`. Print the merge commit SHA.
