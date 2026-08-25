# PR review — Reference

## Which repo

Not pinned to anything. You say which PR and, when it is not the repo you are standing in, where
that repo lives on disk. Two facts are all it needs:

| It needs | Where it comes from |
|---|---|
| `owner/name` for `gh` | The PR URL, or `git -C <path> remote get-url origin` |
| A local path for `git` | The path you gave it, or `git rev-parse --show-toplevel` |

```bash
git -C <path> rev-parse --show-toplevel      # confirm the path is a repo
git -C <path> remote -v                      # get owner/name, and spot a second remote
```

**Always pass `--repo owner/name` on every `gh` call.** A fork and its upstream share a checkout
but have unrelated PR numbers, so PR 32 means two different things depending which you ask. With
`--repo` pinned it cannot pick the wrong one.

Without a path, the repo is wherever you are (`git rev-parse --show-toplevel`). Say plainly which
repo you settled on before reading anything, so a wrong guess is caught in one line rather than
after a full review.

Careful in a repo that contains other repos. `show-toplevel` returns the outer one, and the
clones inside it each have their own `.git`. If the answer looks like a parent directory rather
than the code you meant, ask.

## Several PRs are one change

When more than one PR is handed over, they are one piece of work split across repos, and the
review covers the set. One intent, one page, one overall score, plus a score for each PR so you
can see which repo is holding it up.

This is the only way some findings are visible at all. An enum the web app declares and the API
rejects, a field the frontend reads that only the backend PR creates, a response shape one side
changed and the other still expects. Reviewing each PR alone cannot see any of them.

If the PRs turn out to be unrelated work, say so and review them separately rather than forcing
one intent over the top.

## Keep it short

The page is read in about a minute or it does not get read. Every field has a word cap:

| Field | Cap | Field | Cap |
|---|---|---|---|
| `readiness.why` | 20 | `summary.headline` | 20 |
| dimension `why` | 12 | `summary.body` | 45 |
| PR `readiness.why` | 15 | `out_of_scope` | 30 |
| finding `title` | 10 | `testing.automated` | 30 |
| finding `what` | 20 | `testing.cannot_test` | 25 |
| finding `why` | 45 | step `do` | 20 |
| finding `fix` | 30 | step `expect` | 20 |

A worked review lands near 1,200 words total and under 400 visible before anyone clicks
anything. If a `why` will not fit in 45 words, the finding is two findings or you do not
understand it yet.

**Plain words.** `TONE.md` governs, and these are the ones that actually showed up and had to
be cut: "vestigial", "canonical", "mock theatre", "backdoors", "surfaces", "materialized". If a
word would make you pause reading the sentence aloud to a teammate, it is the wrong word. Say
"left over", "the standard unit", "tests that only test the mocks", "ways around the check".

## Severity, 1 to 5

Graded against the lines this change touched, not the state of the repo. **5 is worst.**

**5 — Ships broken or unsafe.** A migration that cannot run or loses rows, a dropped auth or
tenant check, a secret in the diff, an endpoint returning another user's data, a crash on the
main path. Someone gets paged.

**4 — Wrong for a real case this change is meant to handle.** A migration with no way back, a
security setting changed with no reason given, an unawaited promise so the error vanishes, a
swallowed error, a caller not updated after a signature change. Works in the demo, fails in the
week.

**3 — Real problem, small blast radius.** A bug on a rare path, no test on new branching logic,
a new guard nothing covers, dead code this change added, a pattern break that skips something
load-bearing like tenant scoping.

**2 — Friction, not a defect.** Naming that misleads, a long file that got longer, a comment
that no longer matches the code under it, a branch nothing can reach.

**1 — Nit.** A leftover `console.log`, a stray import, a typo in a string nobody sees.

### The three bands

Severity says how bad one finding is. The band says what it means for merging, which is the
question people open the page with. **Derived from `sev`, never stored**, so the two cannot drift
apart. The page groups the findings under these headings.

| Band | Severity | Heading says |
|---|---|---|
| Blockers | 5 and 4 | Fix these before this merges. |
| Major | 3 | Real problems. Fix them, here or in a follow-up you have already agreed. |
| Minor | 2 and 1 | Friction and nits. Merging over them is fine. |

The bands are not new judgment, they are the anchors read back. A sev 4 already says "fix in this
change" and a sev 2 already says "ship it, clean it later". Grouping them means a reader does not
have to know the scale to act.

This is also the sharpest check on whether a severity is right. If a finding sits in Blockers and
you would honestly merge without fixing it, it is not a 4. If something is in Minor and you would
hold the merge for it, it is not a 2. Read the bands back before publishing and move anything
that sits under the wrong heading.

Two rules that keep the numbers honest:

- Severity is about the changed lines, not the file's history. A 700-line file that grew by
  four lines is a 2. If this change created the file, it counts at full weight.
- **If you would have to run the system to be sure, it cannot go above 3.** Certainty is part of
  the score. That is the `[Assumption]` rule said in numbers.

## Kinds

Eight, in the order they are read. The dimension is **derived from the kind, never stored**, so
the two cannot drift apart:

| Kind | Dimension | It means |
|---|---|---|
| `migration` | Migrations | A schema change that will not apply, cannot be undone, or loses data |
| `scope` | Scope | A change that does not belong to the stated intent |
| `bug` | Correctness | It does the wrong thing, now or soon |
| `test` | Tests | Nothing would catch it if this broke |
| `convention` | Conventions | It ignores the pattern the rest of the repo follows |
| `dead` | Clarity | Code nothing can reach |
| `slop` | Clarity | Left behind: prints, temp files, stale comments, committed secrets |
| `improve` | Clarity | Naming, shape, a message that will not help |

**Two kinds have a severity cap, because without them everything drifts to 3.** On a real run,
nine of twelve findings came back sev 3, which is a default rather than a judgment.

- **`improve` never goes above 2.** A suggestion is friction by definition. If it is worse than
  friction it is not an improvement, it is a `bug` or a `convention` finding, so file it as one.
- **`convention` sits at 2 unless the deviation causes real risk**, and then it can reach 3. "It
  does not match the other services" is a 2. "It does not match the other services, and the
  thing it skips is where the tenant scoping lives" is a 3.

## Readiness, 1 to 5

Severity says how bad one finding is. Readiness says whether the whole thing can go in.
**Here 5 is best.** The two scales point opposite ways because "a sev 5" and "5 out of 5 ready"
are both how people already talk. The colour is what stays constant: red is bad on both.

| Score | Means |
|---|---|
| 4.5 and up | Ready |
| 3.5 to 4.4 | Close |
| 2.5 to 3.4 | Needs another pass |
| 1.5 to 2.4 | A lot to fix |
| under 1.5 | Do not merge |

**The findings set a ceiling. Judgment can only lower it, never raise it.**

| Findings in that dimension | Ceiling |
|---|---|
| any sev 5 | 1 |
| three or more sev 4 | 2 |
| one or two sev 4 | 3 |
| three or more sev 3 | 3 |
| one or two sev 3 | 4 |
| nothing above sev 2 | 5 |

That table is deliberately gentler than it looks like it should be. An earlier version capped a
dimension at 2 for a single sev 4, and the result was that a 3,500-line feature with a thousand
lines of new tests scored a 2. Every real change of any size has a sev 4 somewhere, so that rule
pushed everything into the bottom half and 4 and 5 became unreachable. A scale nothing can score
well on is not measuring anything.

Score below the ceiling when something real is not captured by any single finding, and say what
in the `why`. "Three tests exist and none touch what this changed" is a reason. "It feels risky"
is not. Never score above the ceiling, because that is how a review gets talked into being
kinder than its own evidence.

**A dimension that does not apply gets `score: null`** and drops out of the maths. A docs-only
change is not held back for having no tests, and a change with no schema work is not held back
on Migrations.

A dimension with **no findings** scores 5. A dimension the change **does not touch at all** gets
`null`. Those are different things: tests that exist and are fine is a 5, a docs-only change
that could not have tests is a `null`.

**Overall readiness is the mean of the dimensions that apply**, to one decimal, across every PR
in the set. Write `4.0`, not `4`. It is the average of the six meters printed right under it, so
anyone can check the headline against them by eye and argue with it.

Two overrides sit on top of the mean, because averaging six dimensions dilutes a problem that
lives in one of them:

- **Any sev 5 anywhere sets the overall to 1.** Something that ships broken or unsafe is not
  averaged away by five good dimensions.
- **Any sev 4 anywhere caps the overall at 4.4**, so it can never read "Ready". A sev 4 means
  fix it in this change, and a change with one open cannot also be finished. Without this a
  single sev-4 bug in an otherwise clean change averages to 4.6 and calls itself ready.

The mean answers "how good is this work". It deliberately does not answer "can I merge right
now", because one weak dimension no longer drags the headline down. So **name the weakest
dimension separately** — the page prints "Fix first: correctness" under the meters. That is
where the blocking information lives now.

**Each PR also gets its own score**, the same mean over only its own findings.

There is no separate verdict field. The score, its word, and the fix-first line say everything a
verdict enum used to, without a second thing to keep in step.

## Ranking

Deterministic, so two runs produce the same page:

1. Higher `sev`.
2. `traced` before `suspected`.
3. Kind order: `migration`, `scope`, `bug`, `test`, `convention`, `dead`, `slop`, `improve`.
4. More `callers` first.
5. File path A to Z, then line ascending.

Write the result into `rank` from 1. **The page renders in array order and never re-sorts**, so
`findings` must arrive sorted.

## Verify before reporting

Nothing a reader hands you goes in unchecked.

- Group the findings by file and open each file **once**, checking every finding that cites it in
  the same pass. A busy file draws four or five findings, and going one at a time reopens it
  every time.
- Re-open the cited `file:line`. Does it say what the finding claims? If not, drop it.
- Is the line in the diff? If not it was already there. Set `in_diff: false` and drop the
  severity by one, or cut it. A PR review is not a codebase audit.
- Before calling anything dead, check dynamic imports, reflection, string-keyed registries, DI
  containers, decorator-driven routes, and barrel re-exports. Nest and React both hide callers
  this way. Check the other repos in the set too.
- Misplaced file? List the sibling directories first. Three files living the same way makes it
  the convention, not slop.
- Never flag test fixtures, examples, templates, generated files, or lockfiles.
- Read the PR's comments. If a person already raised it, set `raised_already: true`. It still
  ranks, but the page says someone got there first.
- Merge duplicates on `file:line` plus root cause. Keep the clearest wording, take the higher
  severity.
- Count what you drop into `checked`. It renders, so a short report reads as deliberately short.

A clean PR is a real result. If nothing survives, the page still gets the summary, the test
steps, and a findings section that says so.

### Migrations get read, never skipped

Migrations are the highest blast radius file in any change, so they are the one thing that never
gets waved through. For every migration in the diff:

- **Does it run on a table that already has rows?** A non-null column with no default, or a
  narrowing type change, fails on real data.
- **Is there a down step**, and does it actually undo the up step?
- **Does anything backfill** the rows that already exist, and does the backfill run before the
  constraint is added?
- **Does it drop or rewrite data** that cannot be recovered?
- **Will it lock a big table** long enough to matter?

The trap worth naming on the page: **CI migrates an empty database.** Every bug in this list
passes CI and fails the first time it meets production data, which is why "the checks are
green" says nothing here.

## The `[Assumption]` convention

Most of a review is fact. The useful parts, what will break and who it hurts, are a reading of
the diff. A reader cannot tell them apart unless you say so.

Prefix any inferred sentence with `[Assumption]`. The page turns the marker into a small tag so
the sentence still reads, and the literal string stays in the JSON so it stays greppable.

- **Fact, stays bare:** what a changed line does, an enum value, a file location, a call site
  you grepped and read, a config default, a test that exists.
- **Inference, gets marked:** what will break, who it affects, why the author probably did it,
  what to look at next.

Rule of thumb: if you would have to run the system to be sure, mark it. A bug you traced through
the diff is a fact. A bug you suspect is not.

## Schema

```json
{
  "product": "short name for the whole change",
  "reviewed": "How this was made and how much to trust it.",

  "readiness": {
    "score": 4.0,
    "why": "One spoken sentence, 20 words.",
    "dimensions": [
      { "id": "correctness", "label": "Correctness", "score": 2, "why": "12 words naming what set it." },
      { "id": "scope",       "label": "Scope",       "score": 2, "why": "..." },
      { "id": "migrations",  "label": "Migrations",  "score": null, "why": "No migration in this change." },
      { "id": "conventions", "label": "Conventions", "score": 3, "why": "..." },
      { "id": "tests",       "label": "Tests",       "score": 2, "why": "..." },
      { "id": "clarity",     "label": "Clarity",     "score": 4, "why": "..." }
    ]
  },

  "prs": [
    { "number": 39, "title": "…", "url": "…", "repo": "owner/name", "author": "…",
      "base": "dev", "head": "feat/…", "merge_base": "520c369",
      "state": "open", "draft": false,
      "files_changed": 7, "added": 1337, "removed": 90,
      "checks": "passing | failing | none",
      "readiness": { "score": 1, "why": "15 words." } }
  ],

  "summary": {
    "headline": "One sentence. A non-author reads it and knows what changed.",
    "body": "Up to 45 words, including the part that will surprise someone.",
    "changes": [ { "id": "…", "area": "…", "repo": "owner/name", "what": "One sentence.",
                   "files": ["…"] } ],
    "out_of_scope": "Optional. What this does not do that someone will assume it does."
  },

  "testing": {
    "setup": [ { "repo": "owner/name", "cmd": "npm run start:dev" } ],
    "steps": [ { "id": "…", "n": 1, "do": "…", "expect": "…",
                 "where": "http://localhost:6090/…", "covers": ["finding-id"] } ],
    "automated": "What the tests do and do not cover, 30 words.",
    "cannot_test": "Optional. What you cannot check by hand, and why."
  },

  "findings": [
    { "id": "migration-non-null-no-default", "rank": 1, "sev": 5,
      "kind": "migration", "confidence": "traced", "repo": "owner/name",
      "title": "10 words. A statement, not a label.",
      "file": "src/migrations/…", "line": 11, "lines": "9-14", "fn": "up",
      "evidence": "the literal line, byte for byte",
      "what": "One spoken sentence, factual only, never carries [Assumption].",
      "why": "Up to 45 words. Inferred sentences carry [Assumption].",
      "fix": "What to do, 30 words, actionable without a follow-up question.",
      "in_diff": true, "callers": 0, "raised_already": false }
  ],

  "checked": { "raised": 34, "kept": 10, "dropped": 24 }
}
```

## Field rules

| Field | Marked? | Notes |
|---|---|---|
| `id` | — | **Permanent.** Keys the reader's saved notes. File basename plus the problem. Never rename after publishing. |
| `rank` | — | Integer from 1. The page renders in this order. |
| `sev` | — | 1 to 5, 5 worst. `suspected` caps it at 3. |
| `kind` | — | One of the eight. The dimension follows from it, so there is no `dimension` field. |
| `repo` | — | Which repo the finding is in. Shown only when the set has more than one. |
| `confidence` | — | `traced` means you followed it in the code. `suspected` means you would have to run it. |
| `title` | — | A statement. If it needs a comma it is too long. |
| `file` `line` `lines` `fn` | — | **Copied, not recalled.** `fn` is the anchor of record, `line` drifts. |
| `evidence` | — | **The literal line, byte for byte.** Never paraphrase. This is what makes the finding checkable in ten seconds. |
| `what` | never | One sentence. **Factual only.** This is the line the drawer leads with. |
| `why` | yes | Consequences, intent, advice. All marked. |
| `fix` | yes | Usually part inference. Mark the inferred half. |
| `in_diff` | — | `false` means it was already there. Drops severity by one. |
| `callers` | — | From the one-hop grep. Breaks ranking ties. |
| `raised_already` | — | Someone already said it on the PR. Ranks normally, shows in the facts table. |
| `readiness.score` | — | 1 to 5, **5 best**, one decimal. Mean of the dimensions that apply. A sev 5 anywhere forces 1. |
| `prs[].readiness` | — | Same mean over that PR's own findings. |
| `covers` | — | Finding ids a test step would catch. Renders as a link into that finding. |
| `checked` | — | Honesty counters. Renders in the footer. |

## Page anatomy

One column, no rail. The two sibling skills have a rail because you pick a flow from it; here
there is nothing to pick, so a rail would only list the findings twice.

Top to bottom: the score and its one line, a card per PR with its own score, the six dimension
meters, then what is wrong, then what changed, then how to test, folded.

Clicking a finding opens the drawer, in this order, so a reader can stop anywhere and still have
got something:

1. **n of m**, the pill in the severity colour
2. **The title** as a headline
3. **The short version** — `what`, one factual sentence
4. **Why it matters** — `why`, with assumptions tagged
5. **The code** — the line itself, so the claim is checkable without leaving the page
6. **What to do** — `fix`, always in the accent colour, never the severity colour, so the action
   never blends into the problem
7. **The facts** — severity, kind, repo, confidence, in-diff, function, file, lines, callers
8. The copyable `file:line`
9. **Your notes**
10. Previous and Next

Notes are stored under `pr-review:<repo>#<n>+<repo>#<n>` as `{findingId: note}`, keyed by the
whole set so two reviews never share notes, and by the stable finding id so inserting a finding
never orphans one.

## Structural guidance

- **Fewer findings, better checked.** Six real ones beat twenty nobody reads. Past about a
  dozen, the low end is padding.
- **Every finding needs `evidence`.** No line quoted means it did not get verified.
- **At least one test step should try to break it.** The happy path is not how anyone meets a
  bug.
- **A step with `covers` is worth more than one without**, because it ties the two halves of the
  page together.
- **Name real things.** Real files, real endpoints, real env vars.

## Checking your own output

Before building the page, read what you wrote back against three things:

- **The bands.** Would you merge without fixing everything under Blockers? Would you hold the
  merge for anything under Minor? Either answer means a severity is wrong.
- **The caps.** Every field has a word limit in the table above. A `why` that will not fit in 45
  words is two findings, or one you do not understand yet.
- **The arithmetic.** The overall is the mean of the dimensions that apply, and those dimensions
  sit at or under the ceiling their findings allow. If the headline does not match the meters
  under it, the headline is wrong.

`TEMPLATE.html` carries a small placeholder review in its JSON island, so the page renders on its
own before you have generated anything. Open it to see the shape the data takes.
