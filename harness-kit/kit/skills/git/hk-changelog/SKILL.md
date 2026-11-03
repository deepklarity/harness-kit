---
name: hk-changelog
description: "Write a CHANGELOG.md entry from the branch's diff against main, then commit it on a changelog branch and open a PR. Triggers on: 'update the changelog', 'changelog entry', 'add to the changelog', or /hk-changelog."
argument-hint: "[--dry-run | --commit]"
allowed-tools: Bash, Read, Edit, Write, Agent
---

# Changelog

Write one entry at the top of `CHANGELOG.md` at the repo root from what this branch changed.

```
/hk-changelog              write + commit on a changelog branch + PR
/hk-changelog --commit     write + commit, no PR
/hk-changelog --dry-run    print the entry, change nothing
```

Arguments arrive in `$ARGUMENTS`.

## 1. Gather the git data

```bash
git rev-parse --abbrev-ref HEAD
git log main..HEAD --oneline --no-merges
git diff --stat main...HEAD
git diff --name-status main...HEAD
git status --short
```

`main...HEAD` diffs from the merge-base, so it shows only this branch's work. Set `RANGE=main...HEAD`. On `main` itself, or with no commits past main, set `RANGE=HEAD~1`; the entry then covers the last commit plus any uncommitted work.

If `CHANGELOG.md` is missing at the repo root, create it with the line `# Changelog` and a blank line, and say so in the report. Read it either way so the entry matches the existing shape.

## 2. Write the entry (sonnet subagent)

Dispatch one `Agent` with `model: "sonnet"`. Give it the output of step 1, the current `CHANGELOG.md`, `RANGE`, and the block below verbatim.

---

Read the diffs by area; file names alone are not enough to describe a change. Group by the top-level projects this workspace's CLAUDE.md lists, running `git diff $RANGE -- <path>` per area. Uncommitted work shows up with `git diff HEAD -- <path>` added to each group.

Read new untracked files in full. For a group over 500 lines, read the first 300 and that group's `--stat`.

Write the entry in this shape:

```markdown
## YYYY-MM-DD — [short title]
**`<short-hash>`** — [one sentence on the theme]

### Added
- [what is new, as a capability]

### Changed
- [what works differently now, for the user or developer]

### Deleted
- [what is gone]

---
```

Rules:

- Use today's date and the latest commit's short hash, or `unstaged` for uncommitted work.
- Include only the sections that have entries.
- Every change someone would notice gets a bullet; a reader should learn what shipped without opening the diff. One to five files: 2 to 4 bullets. Five to twenty: 5 to 10. More: 10 to 20, grouped by area.
- Say what changed and why it matters in the user's terms: "Added executing-time tracking to task cards", not the field and file it lives in. A file path appears only when the file is the feature (a new migration, a new config module).
- Distinct changes stay distinct bullets: a new component, a new comment type and a CLI restructure are three bullets. Frontend changes (new components, changed controls, new form fields, visual changes) and backend changes (model fields, API parameters, parsing, migrations) each get their own.
- Match the tone of the existing entries.

Return the markdown entry from `## ` to `---` and nothing else.

---

## 3. Apply

With `--dry-run`, print the entry and stop.

Otherwise insert the entry after the `# Changelog` line and its blank line, with `Edit`.

## 4. Commit on a changelog branch

Whatever branch you are on, the commit goes on a new branch cut from the current HEAD, so the source branch (`main` or a feature branch) is never touched and never pushed by this skill. Each Bash call starts a fresh shell, so write the source branch name down in step 1 and set `SOURCE=<that name>` at the top of every block that reads it. A failure here stops the run with the error shown.

```bash
git checkout -b changelog/$(git rev-parse --short HEAD)
git add CHANGELOG.md
git commit -m "$(printf 'docs: update CHANGELOG.md\n\n<the attribution trailer the harness gave this session>')"
```

## 5. Open the PR (no flags only)

The PR targets `SOURCE`, so it carries only the changelog commit: on `main` it merges into main, on a feature branch it merges into that branch and rides the feature's own PR from there. Finish with `git checkout "$SOURCE"` so the user is back where they started.

```bash
SOURCE=<source branch from step 1>
git push -u origin HEAD
gh pr create --base "$SOURCE" --title "docs: changelog entry for $SOURCE" --body "$(cat <<'EOF'
## Summary
- Changelog entry for the changes on `<SOURCE>`

## Content
<the entry from step 2>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<the session URL the harness gave this session, when there is one>
EOF
)"
```

## 6. Report

One or two sentences: whether `CHANGELOG.md` was created, the changelog branch name and which branch it was cut from, the commit, and the PR URL and its base when one was opened. For `--dry-run`, the entry itself.

## Example

```bash
git diff $RANGE -- backend/
git diff $RANGE -- frontend/src/
git diff $RANGE -- cli/
git diff $RANGE -- docs/ CLAUDE.md scripts/
```
