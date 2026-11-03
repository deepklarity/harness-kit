---
name: hk-pr-review
description: "Review a pull request, a branch, or the diff since a ref as one HTML page: a readiness score out of 5, findings in blockers, major and minor, a Standards section and a Spec section. Triggers on: 'review this PR', 'review my branch', 'is this ready to merge', 'review since <ref>', 'check this against the spec', 'standards and spec review', or /hk-pr-review."
argument-hint: "[PR url, number, branch, or a ref to review since] [path to the spec] [path to the repo, if not the current one]"
---

# /hk-pr-review — Review a change

Reads a change and writes it up for a person. The score comes first, because the question is
always "can this go in", and the rest of the page explains the number.

It does not run the code, push commits, or comment on the PR. It judges only the lines the
change touched; a codebase audit is `/hk-code-quality-audit`. If nothing is wrong it says so, and that
is a finished review.

## Context

<review_context> $ARGUMENTS </review_context>

If nothing was given, follow the "Nothing" row of the table in step 1. If the repo is not the
one you are standing in and no path was given, ask where it lives. That is the only question
this skill asks.

Two files drive it:

- `REFERENCE.md` in this skill's base directory — which repo, the word caps, the severity and
  readiness rubrics, the smell baseline, the migration lens, the verify gate, the working shape
  of the data, and the page order. Read it before writing the review data.
- `docs/playbook/tone_and_taste.md` at the repo root — the house voice. It governs every string
  that lands on the page; read it before writing prose.

## Workflow

**1. Work out what to review.** Settle the repo, the `owner/name` for `--repo` and the
`<remote>` for `git` as REFERENCE.md "Which repo" says, and say which you settled on before
reading anything.

| You gave it | What happens |
|---|---|
| A full URL | Owner, repo and number come off the URL. |
| A bare number | `gh pr view <n> --repo <owner/name>` against the repo you settled on. Given several repos, probe each; if the number resolves in more than one, review the one whose head branch is checked out and say so. |
| A branch name | `gh pr list --repo <owner/name> --head BRANCH --state all`. A PR exists, use it. No PR, review the branch against the merge base with `main` and say so on the page. |
| A ref: a SHA, tag, or `HEAD~5` | No PR. The head is `HEAD` and the base is that ref. Confirm it resolves with `git -C <path> rev-parse <ref>` and that `git -C <path> diff --stat <ref>...HEAD` is non-empty before anything else, so a bad ref fails here rather than inside the readers. |
| Several of the above | One change split across repos. One review, one page. |
| Nothing | `git rev-parse --abbrev-ref HEAD`. On a feature branch, treat it as a branch name. On `main`, review the most recent open PR from `gh pr list --repo <owner/name> --limit 1` and say so. |

If the change spans repos, hand over every PR at once and they are reviewed as one thing. Some
findings only exist between the halves: an enum one side declares and the other rejects, a
field the caller reads that only the other PR creates.

**2. Pull each diff. Three dots, always.** Per PR:

```bash
git -C <path> fetch <remote> <base> && git -C <path> fetch <remote> +pull/N/head:pr-N
git -C <path> merge-base <remote>/<base> pr-N
git -C <path> diff --numstat     <remote>/<base>...pr-N
git -C <path> diff --name-status <remote>/<base>...pr-N
git -C <path> log --oneline      <remote>/<base>..pr-N
gh pr view N --repo <owner/name> --json number,title,body,url,author,baseRefName,headRefName,state,isDraft,additions,deletions,changedFiles,files,commits
gh pr view N --repo <owner/name> --json comments,reviews
gh pr checks N --repo <owner/name>
```

The leading `+` resets `pr-N` after a force-push; without it a re-review reads the commit
nobody will merge. `pr-N` is the head for every command that follows, including the readers'
diffs; a branch with no PR uses the branch name, and a ref uses `HEAD`, with the ref in place of
`<remote>/<base>` and the `gh` lines skipped. Names and counts only. The diff bodies are not
pulled here, because whatever lands in this conversation gets sent again inside every reader
prompt. Each reader fetches its own slice in step 5.

Two dots pulls in everything that landed on the base branch since the work started and blames
the author for it. The merge-base SHA goes in `merge_base` so anyone can reproduce the diff, and
each reader gets it so they all read the same commit. Read the comments: if a person already
raised something, do not hand it back as news.

**3. Frame it, cheaply.** Read the file list with line counts, the PR descriptions and
comments, and the small files that set the repo's patterns: `CLAUDE.md`, `README.md`,
`tsconfig.json`, `.eslintrc*`, `pyproject.toml`, whichever exist. Classify every changed file
from its path alone: source, test, config, migration, generated, fixture, or lockfile. Diff
bodies and source files wait for the readers in step 5; reading sibling files for conventions is
the conventions reader's job.

Then settle the two axes the standards and spec readers need:

- **Standards** are the root `CLAUDE.md`, the `CLAUDE.md` nearest each changed file, and the
  workspace's tenets or philosophy document when CLAUDE.md names one. In another repo,
  `CODING_STANDARDS.md` or `CONTRIBUTING.md` where they exist. The smell baseline in REFERENCE.md
  rides along with them.
- **Spec**, first hit wins: a path the user passed; else an issue referenced in the commit
  messages (`#123`, `Closes #45`), fetched with `gh issue view <n> --repo <owner/name>`. If
  neither turns up, the Spec reader does not run and the page says no spec was found.

**4. Set the bound.** This is a spec you hand to the readers.

- Give each reader a file list, never diff text. The reader runs its own
  `git -C <path> diff <merge-base>...<head> -- <its files>`. A real two-repo change runs past
  200 KB and pasting it into every prompt copies it once per reader before anyone has read a line.
- Whole file under 400 lines. Over that, the hunks plus 80 lines either side plus the import
  header. A diff-only read flags things the untouched half of the file already handles.
- One hop out, and only where it pays. Only the bugs and cross-repo readers follow call sites,
  for symbols that are newly exported or whose signature changed, at most fifteen, ordered by
  how many call sites each has. Skip test, generated and lock files.
- No second hop. A finding that would need one still ships, as `suspected`, capped at 3.

More than 60 changed files or 3000 changed lines: review the source and migration files, leave
the rest, and say on the page exactly which files were not read.

**5. Readers in parallel.** One message, one `Agent` call on sonnet per row of the table below;
these are judgment calls, not lookups. Every prompt carries the same small pack (the absolute
repo path from `git rev-parse --show-toplevel`, with every git command run as `git -C <path>`,
the merge-base SHA, the classified file list, the conventions from step 3, the reading bound,
the severity anchors and kinds from `REFERENCE.md`, the word caps, the path to
`docs/playbook/tone_and_taste.md`) and then only the files that reader needs.

| Reader | Gets | Looks for |
|---|---|---|
| **migration** | migration files only | Runs on a table with rows, has a down step, backfills before constraining, drops or rewrites data, locks a big table. Runs only when step 3 classified a file as a migration; when it did not, say so on the page. |
| **scope** | the file list, the line counts, the PR descriptions, and the config, auth and dependency files | Does every changed file trace to the stated intent. A setting nobody mentioned is what this reader exists to find. |
| **bugs** | changed source files, tests excluded. Does the one-hop | A missing `await`, null on an optional field, off-by-one, a dropped auth or tenant check, a changed response shape with a stale caller, a swallowed error, a race, an N+1. |
| **tests** | test files, plus the signatures of changed source | Is there a test for this change, does an existing test cover the new branch, can a person check it by hand. Guards and permission checks with no test are its bread and butter. Writes the `testing` block. |
| **conventions** | changed source, plus two or three siblings per changed directory | Does the new code follow what the repo already does: the provider the other services go through, the directory the other tables live in, the error shape the other handlers throw. |
| **improve** | changed source files | Naming, readability, duplication, a simpler shape, a message that will not help. Tell it plainly that an empty list is a correct answer. |
| **standards** | changed source, the standards files from step 3, and the smell baseline from REFERENCE.md pasted whole | Per file or hunk: where the diff breaks a written rule, citing the file and the rule; and any baseline smell, named, with the hunk quoted. Writes the `standards` block, under 400 words. |
| **spec** | changed source, and the spec's path or fetched text | Requirements the spec asked for that are missing or partial, behaviour in the diff the spec did not ask for, requirements that look implemented but wrong. A spec line quoted per finding. Writes the `spec` block, under 400 words. |

Bugs and improve both answer one extra question: what will this make harder in six months? A
flag that means two things, a type declared twice, a pattern the next person will copy.

The conventions reader reads what the repo does; the standards reader reads what the repo wrote
down. A rule that is only practice belongs to the first, a rule with a line in `CLAUDE.md` to
the second.

When the set has more than one PR, add a **cross-repo** reader. It gets only the shared surface
from both sides (enums, DTOs, exported types, endpoint paths and their request and response
shapes), not the diffs, and looks for the two halves disagreeing.

**6. Verify every finding, a file at a time.** The gate is in `REFERENCE.md`, and the standards
and spec blocks pass it too: open every cited `file:line` and every quoted spec line, and drop
anything whose quote does not match. Group by file and open each file once, checking every
finding that cites it in the same pass. Duplicates collapse here, each finding gets `traced` or
`suspected`, and you count what you dropped.

**7. Rank, then rate.** Rank by the tie-break order in `REFERENCE.md`. Apply the two severity
caps there (`improve` never above 2, `convention` at 2 unless what it skips is load-bearing),
then score the six dimensions: the findings set a ceiling, judgment only lowers it, a dimension
the change does not touch gets `null`. Overall is the mean of the ones that apply, to one
decimal, with the overrides in `REFERENCE.md`. Name the weakest dimension as what to fix first.
Do not decide the number first and pick findings to justify it. The standards and spec blocks
stay out of the bands and out of the score; a change can pass one axis and fail the other, and
merging the lists would hide that.

**8. Read the bands back, then cut to the caps.** The page groups findings into Blockers (sev 5
and 4), Major (sev 3) and Minor (sev 2 and 1). At each heading ask the plain question: would
you merge without fixing everything under Blockers? Then they are not blockers. Would you hold
the merge for anything under Minor? Then it is not minor. Move what sits under the wrong
heading. Then enforce the word caps table in `REFERENCE.md` on every field, and read the page
out loud: any word you would not say to a teammate comes out.

**9. Build the page with the `hk-writeup` skill.** Drop the content into
`docs/skills/knowledge/hk-writeup/SHELL.html` in the order given under "Page order" in
`REFERENCE.md`, replace the footer's `.said` sentence as that order says, and change nothing
else in the shell. The score is a bold lead-in sentence, the dimensions a plain table, each
finding a `<details class="row">` under its band, the test steps a numbered list. Say on the
page that this was read by hand at the merge-base SHA and nothing was run.

Output goes to `writeup/pr-review-<slug>.html` at the root of the repo this skill is installed
in, `$(git rev-parse --show-toplevel)/writeup/`, gitignored; create the directory if it is
missing. `<slug>` is the branch name, the ref when reviewing since one, or the joined repo names
and numbers when the PRs do not share one. A review is a snapshot of one commit, so re-running
replaces the file.

**10. Report back:** where the file went, the readiness score and the one sentence behind it,
how many findings at each severity, one line per axis saying how many findings it raised and the
worst one, and anything you could not follow.

## Writing the findings

The point is that someone who did not write the code knows what breaks and when.

- Say what goes wrong, not what the rule is called. "The list shows an empty cell when quantity
  is zero, so out of stock looks the same as never filled in" beats "falsy check on a numeric
  field".
- Name the real file, the real field, the real endpoint, the real number.
- The code is not a character. It does not forget, try, or decide. Say what the line does and
  who calls it.
- On a sev 5 or 4, say what state it leaves behind. That is what someone will be staring at
  when they ask what went wrong.
- The readiness `why` is a sentence you would say across a desk: "The migration will not apply
  on any database that already has rows."
