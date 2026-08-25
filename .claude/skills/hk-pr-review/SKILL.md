---
name: hk-pr-review
description: "Review a pull request and write it up as one HTML page a person reads in a minute. Gives a readiness score out of 5 for whether it can merge, findings grouped into blockers, major and minor, what the change does, and how to test it by hand. Checks migrations, changes that do not belong in the PR, whether the repo's own patterns were followed, and what will break later. Give it a PR link or number and, if the repo is not the one you are in, where that repo lives. Takes several PRs at once and reviews them as one change. Triggers on: 'review this PR', 'review my branch', 'is this ready to merge', 'what does this PR do', or /hk-pr-review."
argument-hint: "<PR url or number> [path to the repo, if not the current one]"
allowed-tools: Bash, Read, Edit, Write, Task, Grep, Glob
---

# /hk-pr-review — Review a pull request

Reads a change and writes it up for a person. The score comes first, because the question is
always "can this go in", and the rest of the page explains the number.

## Context

<review_context> $ARGUMENTS </review_context>

If the context above is empty or unclear, ask which PR to review. If the repo is not the one you
are standing in, ask where it lives.

Two files in this skill's base directory drive it:

- `TONE.md` — the house writing voice. Read it before writing any prose. It governs every string
  that lands on the page.
- `REFERENCE.md` — how to settle which repo, the word caps, the severity and readiness rubrics, the
  migration lens, the verify gate, and the schema. Read it before writing the review data.

Load the `artifact-design` skill before first authoring the page. Beyond it, two specifics: bind
background and text colour to the same token set on `html, body` with `!important` (a host shell
can force its own background and leave the page unreadable), and keep the page self-contained,
no CDN fonts or scripts.

## What this is not

Not a linter and not a rubber stamp. It does not run the code, push commits, or comment on the
PR. It is not a codebase audit either — `/hk-slop-audit` does that. This only judges the lines the
change touched. If nothing is wrong it says so, and that is a finished review.

## Workflow

**1. Work out what to review.** Settle the repo first. If a path was given, use it. If not, the
repo is wherever you are: `git rev-parse --show-toplevel`. Then `git -C <path> remote -v` for the
`owner/name` that every `gh` call needs pinned with `--repo`. `REFERENCE.md` covers the edge
cases. Say which repo you settled on before reading anything.

| You gave it | What happens |
|---|---|
| A full URL | Owner, repo and number come off the URL. |
| A bare number | `gh pr view <n> --repo owner/name` against the repo you settled on. Given several repos, probe each and ask if the number resolves in more than one. |
| A branch name | `gh pr list --repo R --head BRANCH --state all`. A PR exists, use it. No PR, review against the merge base and say so on the page. |
| Several of the above | One change split across repos. One review, one page. |
| Nothing | `git rev-parse --abbrev-ref HEAD`. On a feature branch that is the answer. On `main`, list recent PRs and ask. |

If the change spans repos, hand over every PR at once and they are reviewed as one thing. Some
findings only exist between the halves — an enum one side declares and the other rejects, a field
the caller reads that only the other PR creates — and no single-repo review can see them.

**2. Pull each diff. Three dots, always.** Per PR:

```bash
git -C R fetch <remote> <baseRef>
git -C R merge-base <remote>/<base> <head>
git -C R diff --numstat     <remote>/<base>...<head>
git -C R diff --name-status <remote>/<base>...<head>
git -C R log --oneline      <remote>/<base>..<head>
gh pr view N --repo X --json number,title,body,url,author,baseRefName,headRefName,state,isDraft,additions,deletions,changedFiles,files,commits
gh pr view N --repo X --json comments,reviews
gh pr checks N --repo X
```

Names and counts only. The diff **bodies** are not pulled here, because whatever lands in this
conversation gets sent again inside every reader prompt. Each reader fetches the slice it needs
in step 5.

Two dots pulls in everything that landed on the base branch since the work started and blames
the author for it. The merge-base SHA goes in `merge_base` so anyone can reproduce the diff, and
each reader is given it so they all read the same commit. Read the comments — if a person already
raised something, do not hand it back as news.

**3. Frame it, cheaply.** Read the file list with line counts (`--numstat`), the PR descriptions
and comments, and the small files that set the repo's patterns: `CLAUDE.md`, `README.md`,
`tsconfig.json`, `.eslintrc*`, `pyproject.toml`, `nest-cli.json`. Classify every changed file
from its path alone: source, test, config, migration, generated, fixture, or lockfile.

**Do not read diff bodies or source files here.** The readers do that in step 5, and anything you
read now gets read a second time there. Learning the conventions properly matters, but reading
sibling files is the conventions reader's job, not yours.

**4. Set the bound.** This is a spec you hand to the readers, not a pass you run yourself.

- **Give each reader a file list, never diff text.** The reader runs its own
  `git -C R diff <merge-base>...<head> -- <its files>`. Pasting the diff into every prompt copies
  it once per reader before anyone has read a line, and a real two-repo change runs past 200 KB.
- Whole file under 400 lines. Over that, the hunks plus 80 lines either side plus the import
  header. A diff-only review flags things the untouched half of the file already handles.
- **One hop out, and only where it pays.** Only the bugs and cross-repo readers follow call
  sites. They take symbols that are newly exported or whose signature changed, **at most fifteen**,
  ordered by how many call sites each has. Not every symbol in the diff: a 3,500-line change
  declares around three hundred, and grepping all of them is most of the wall clock and finds
  almost nothing. Skip test, generated and lock files.
- Never follow a second hop. A finding that would need one still ships, as `suspected`, capped
  at 3.

More than 60 changed files or 3000 changed lines, stop and ask whether to scope down. Reviewing
half a change and not saying so is worse than not reviewing it.

**5. Six readers in parallel.** One message, six `Task` calls. Every prompt carries the same
small pack — the repo path and merge-base SHA, the classified file list, the conventions from
step 3, the reading bound, the rubrics, `TONE.md`, the word caps — and then **only the files
that reader needs**. Six agents each opening all 26 changed files is six times the work for one
review.

| Reader | Gets | Looks for |
|---|---|---|
| **migration** | migration files only | Runs on a table with rows, has a down step, backfills before constraining, drops or rewrites data, locks a big table. Say plainly that CI migrates an empty database, so green checks prove nothing here. Returns empty at once when the change has no migration. |
| **scope** | the file list, the line counts, the PR descriptions, and the diff of config, auth and dependency files | Does every changed file trace to the stated intent. A setting nobody mentioned is what this reader exists to find. It does not need source bodies to answer that, so do not send them. |
| **bugs** | changed source files, tests excluded. Does the one-hop | A missing `await`, null on an optional field, off-by-one, a dropped auth or tenant check, a changed response shape with a stale caller, a swallowed error, a race, an N+1. |
| **tests** | test files, plus the signatures of changed source | Is there a test for this change, does an existing test cover the new branch, can a person check it by hand. Guards and permission checks with no test are its bread and butter, because a later refactor deletes them silently. Writes the `testing` block. |
| **conventions** | changed source, plus two or three siblings per changed directory | Does the new code follow what the repo already does. The provider the other services go through, the directory the other tables live in, the error shape the other handlers throw. |
| **improve** | changed source files | Naming, readability, duplication, a simpler shape, a message that will not help. Most likely to invent work to look useful, so tell it plainly that an empty list is correct. |

**Bugs and improve both answer one extra question**: what will this make harder in six months?
A flag that means two things, a type declared twice, a pattern the next person will copy. Say it
as a finding or say nothing, but ask it.

**When the set has more than one PR, add a seventh: cross-repo.** It gets only the shared surface
from both sides — enums, DTOs, exported types, endpoint paths and their request and response
shapes — not the diffs. Enums that disagree, a field one side reads and the other never creates,
a response shape one side changed. These findings are invisible to every other reader and are
usually the most valuable thing on the page, and the surface is a small fraction of the diff.

Use sonnet. These are judgment calls, not lookups.

**6. Verify every finding, a file at a time.** The gate is in `REFERENCE.md` and nothing skips
it. Group the findings by file first and open each file once, checking every finding that cites
it in the same pass. Going finding by finding reopens a busy file four or five times. This is
also where duplicates collapse, each finding gets `traced` or `suspected`, and you count what
you dropped.

**7. Rank, then rate.** Rank by the tie-break order. Before scoring, check the two severity caps
in `REFERENCE.md`: an `improve` finding cannot exceed 2, and a `convention` finding sits at 2
unless what it skips is load-bearing. Without those, everything drifts to 3 and the ceilings all
collapse together.

Then score the six dimensions. The findings set a ceiling and judgment can only lower it. A
dimension the change does not touch gets `null`, not a low number. Overall is the **mean** of
the ones that apply, to one decimal, and a sev 5 anywhere forces it to 1. Name the weakest
dimension as what to fix first — the mean says how good the work is, the weakest says where to
start. Do not decide the number first and pick findings to justify it.

**8. Read the bands back, then cut it down.** The page groups findings into Blockers (sev 5 and
4), Major (sev 3) and Minor (sev 2 and 1). Go down that grouping and ask the plain question at
each heading: would you honestly merge this without fixing the things under Blockers? If yes,
they are not blockers, so move them. Would you hold the merge for anything under Minor? Then it
is not minor. This catches a mis-set severity faster than re-reading the anchors, because the
heading states the consequence out loud.

Then every field has a word cap in `REFERENCE.md`. Go back over what you wrote and enforce them. A `why` that will not fit in 45 words is two findings or is not understood yet.
Read the page out loud. Any word you would not say to a teammate comes out.

**9. Build the page:**
1. Read `TEMPLATE.html` from this skill's base directory.
2. Replace `<title>PR Review</title>` with `<title>[repo] #[n] + [repo] #[n] — PR Review</title>`.
3. Replace **only** the `<script type="application/json" id="review-data">…</script>` block with
   the review data.
4. Write the result. Change nothing else in the template.

One file. There is no JSON sidecar — the data lives in the page's own island, and a PR review is
a snapshot pinned to a commit rather than something anyone hand-edits.

Output goes to `docs/pr_reviews/<slug>.html`. `<slug>` is the shared branch name when the PRs
share one, otherwise the joined repo names and numbers. A review is a snapshot of one commit, so
re-running replaces the file rather than adding to it.

**10. Report back:** where the file went, the readiness score and the one sentence behind it, how
many findings at each severity, and — plainly — anything you could not follow.

## Cadence

The review is tied to a commit, not to a PR forever.

- Re-run when a PR gets new commits. `reviewed` names the head SHA, so a stale page is obvious.
- Finding `id` values are permanent. They key saved notes, so renaming one orphans someone's
  work. Add findings freely, never rename ids.
- Delete findings that got fixed rather than marking them done. An old finding sitting in a
  report is how people stop trusting the report.
- Say plainly, on the page, that this was read by hand and nothing was run.

## Writing the findings

The point is that someone who did not write the code knows what breaks and when.

- Say what goes wrong, not what the rule is called. "The list shows an empty cell when quantity
  is zero, so out of stock looks the same as never filled in" beats "falsy check on a numeric
  field".
- Name the real file, the real field, the real endpoint, the real number.
- Do not make the code a character. It does not forget, try, or decide. Say what the line does
  and who calls it.
- On a sev 5 or 4, say what state it leaves behind. That is what someone will be staring at when
  they ask what went wrong.
- The `fix` has to be doable without asking you a follow-up question. If you cannot write that
  sentence, you do not understand the finding well enough to report it.
- The readiness `why` is a sentence you would say across a desk. Not a headline, not clipped
  radio-speak. "The migration will not apply on any database that already has rows" is the
  register.
