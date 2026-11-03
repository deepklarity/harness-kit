# PR review — Reference

## Which repo

Not pinned to anything. Two facts are all it needs:

| It needs | Where it comes from |
|---|---|
| `owner/name` for `gh` | The PR URL, or `git -C <path> remote get-url origin`; use `origin` unless the PR URL names another remote |
| A local path for `git` | The path you gave it, or `git rev-parse --show-toplevel` |

`<remote>` in the git commands is the one whose URL contains that `owner/name` (`git -C <path>
remote -v`), or `origin` when only one exists.

Always pass `--repo owner/name` on every `gh` call. A fork and its upstream share a checkout but
have unrelated PR numbers, so PR 32 means two different things depending which you ask.

Careful in a repo that contains other repos. `show-toplevel` returns the outer one, and the
clones inside it each have their own `.git`. If the answer looks like a parent directory rather
than the code you meant, use the inner clone and say so.

## Several PRs are one change

When more than one PR is handed over, they are one piece of work split across repos, and the
review covers the set: one intent, one page, one overall score, plus a score for each PR so you
can see which repo is holding it up. If the PRs turn out to be unrelated work, say so and
review them separately rather than forcing one intent over the top.

## Word caps

The page is read in about a minute or it does not get read.

| Field | Cap | Field | Cap |
|---|---|---|---|
| `readiness.why` | 20 | `summary.headline` | 20 |
| dimension `why` | 12 | `summary.body` | 45 |
| PR `readiness.why` | 15 | `out_of_scope` | 30 |
| finding `title` | 10 | `testing.automated` | 30 |
| finding `what` | 20 | `testing.cannot_test` | 25 |
| finding `why` | 45 | step `do` | 20 |
| finding `fix` | 30 | step `expect` | 20 |

A worked review lands near 1,200 words total and under 400 visible before anyone opens a row.
If a `why` will not fit in 45 words, the finding is two findings or you do not understand it yet.

Words that showed up on real runs and had to be cut: "vestigial", "canonical", "mock theatre",
"backdoors", "surfaces", "materialized". Say "left over", "the standard unit", "tests that only
test the mocks", "ways around the check".

## Severity, 1 to 5

Graded against the lines this change touched, not the state of the repo. 5 is worst.

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

Two rules on top:

- Severity is about the changed lines, not the file's history. A 700-line file that grew by
  four lines is a 2. If this change created the file, it counts at full weight.
- If you would have to run the system to be sure, it cannot go above 3. That is the
  `[Assumption]` rule said in numbers.

### The three bands

The band is derived from `sev`, never stored, and is what the page groups findings under.

| Band | Severity | Heading says |
|---|---|---|
| Blockers | 5 and 4 | Fix these before this merges. |
| Major | 3 | Real problems. Fix them, here or in a follow-up you have already agreed. |
| Minor | 2 and 1 | Friction and nits. Merging over them is fine. |

## Kinds

Eight, in the order they are read. The dimension is derived from the kind, never stored.

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

Two kinds have a severity cap, because without them everything drifts to 3. On a real run,
nine of twelve findings came back sev 3, which is a default rather than a judgment.

- `improve` never goes above 2. A suggestion is friction by definition. If it is worse than
  friction it is a `bug` or a `convention` finding, so file it as one.
- `convention` sits at 2 unless the deviation causes real risk, and then it can reach 3. "It
  does not match the other services" is a 2. "It does not match the other services, and the
  thing it skips is where the tenant scoping lives" is a 3.

## Readiness, 1 to 5

Severity says how bad one finding is. Readiness says whether the whole thing can go in. Here 5
is best, because "a sev 5" and "5 out of 5 ready" are both how people already talk. It is the
one score in the skill set out of 5 rather than 10: a merge decision, not a quality audit.

| Score | Means |
|---|---|
| 4.5 and up | Ready |
| 3.5 to 4.4 | Close |
| 2.5 to 3.4 | Needs another pass |
| 1.5 to 2.4 | A lot to fix |
| under 1.5 | Do not merge |

The findings set a ceiling per dimension. Judgment can only lower it, never raise it.

| Findings in that dimension | Ceiling |
|---|---|
| any sev 5 | 1 |
| three or more sev 4 | 2 |
| one or two sev 4 | 3 |
| three or more sev 3 | 3 |
| one or two sev 3 | 4 |
| nothing above sev 2 | 5 |

Score below the ceiling when something real is not captured by any single finding, and say what
in the `why`. "Three tests exist and none touch what this changed" is a reason. "It feels
risky" is not.

A dimension with no findings scores 5. A dimension the change does not touch at all gets
`null` and drops out of the maths. Those are different things: tests that exist and are fine is
a 5, a docs-only change that could not have tests is a `null`.

Overall readiness is the mean of the dimensions that apply, to one decimal, across every PR in
the set. Write `4.0`, not `4`. Two overrides sit on top of the mean:

- Any sev 5 anywhere sets the overall to 1.
- Any sev 4 anywhere caps the overall at 4.4, so it can never read "Ready". A sev 4 means fix
  it in this change, and a change with one open cannot also be finished.

The mean answers "how good is this work". It does not answer "can I merge right now", so the
page also names the weakest dimension: "Fix first: correctness." Each PR also gets its own
score, the same mean over only its own findings. There is no separate verdict field.

## Ranking

Deterministic, so two runs produce the same page:

1. Higher `sev`.
2. `traced` before `suspected`.
3. Kind order: `migration`, `scope`, `bug`, `test`, `convention`, `dead`, `slop`, `improve`.
4. More `callers` first.
5. File path A to Z, then line ascending.

Write the result into `rank` from 1. The page renders in this order.

## Verify before reporting

Nothing a reader hands you goes in unchecked.

- Re-open the cited `file:line`. Does it say what the finding claims? If not, drop it.
- Is the line in the diff? If not it was already there. Set `in_diff: false` and drop the
  severity by one, or cut it. A PR review is not a codebase audit.
- Before calling anything dead, check dynamic imports, reflection, string-keyed registries, DI
  containers, decorator-driven routes, and barrel re-exports. Nest and React both hide callers
  this way. Check the other repos in the set too.
- Misplaced file? List the sibling directories first. Three files living the same way makes it
  the convention, not slop.
- Test fixtures, examples, templates, generated files and lockfiles are never findings.
- Read the PR's comments. If a person already raised it, set `raised_already: true`. It still
  ranks, but the page says someone got there first.
- Merge duplicates on `file:line` plus root cause. Keep the clearest wording, take the higher
  severity.
- Count what you drop into `checked`. It goes in the footer, so a short report reads as
  deliberately short.

A clean PR is a real result. If nothing survives, the page still gets the summary, the test
steps, and a findings section that says so.

### Migrations get read, never skipped

For every migration in the diff:

- Does it run on a table that already has rows? A non-null column with no default, or a
  narrowing type change, fails on real data.
- Is there a down step, and does it actually undo the up step?
- Does anything backfill the rows that already exist, and does the backfill run before the
  constraint is added?
- Does it drop or rewrite data that cannot be recovered?
- Will it lock a big table long enough to matter?

Say on the page that CI migrates an empty database, so every bug in this list passes CI and
fails the first time it meets production data. "The checks are green" says nothing here.

## The smell baseline

The standards reader carries this fixed set of Fowler code smells (_Refactoring_, ch.3) on top
of whatever the repo wrote down. Two rules bind it: a documented repo standard wins where it
endorses something the baseline would flag, and every smell is a labelled judgement call
("possible Feature Envy"), never a hard violation. Anything tooling already enforces is skipped.
Each reads *what it is* → *how to fix*:

- **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no clear name comes, the design is murky.
- **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps**: the same few fields or params keep travelling together. → bundle them into one type, pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

## The `[Assumption]` convention

Most of a review is fact. The useful parts, what will break and who it hurts, are a reading of
the diff. Prefix any inferred sentence with `[Assumption]` so a reader can tell them apart.

- Fact, stays bare: what a changed line does, an enum value, a file location, a call site you
  grepped and read, a config default, a test that exists.
- Inference, gets marked: what will break, who it affects, why the author probably did it, what
  to look at next.

If you would have to run the system to be sure, mark it. A bug you traced through the diff is
a fact. A bug you suspect is not.

## Working shape

Build the review as this JSON before writing the page, so the caps, the ceilings and the
arithmetic can be checked against it. It does not ship; the page is the only output.

```json
{
  "product": "short name for the whole change",
  "reviewed": "How this was made and how much to trust it. Names the merge-base SHA.",

  "readiness": {
    "score": 4.0,
    "why": "One spoken sentence, 20 words.",
    "fix_first": "correctness",
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
    "changes": [ { "area": "…", "repo": "owner/name", "what": "One sentence.", "files": ["…"] } ],
    "out_of_scope": "Optional. What this does not do that someone will assume it does."
  },

  "testing": {
    "setup": [ { "repo": "owner/name", "cmd": "npm run start:dev" } ],
    "steps": [ { "n": 1, "do": "…", "expect": "…", "where": "http://localhost:3000/…",
                 "covers": ["finding-id"] } ],
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
      "fix": "What to do, 30 words.",
      "in_diff": true, "callers": 0, "raised_already": false }
  ],

  "standards": [
    { "rule": "The rule, quoted, and the file it is written in. Say 'possible <smell>' for a baseline smell.",
      "file": "src/…", "line": 42, "evidence": "the literal line, byte for byte",
      "what": "What the diff does instead, one sentence." }
  ],

  "spec": {
    "source": "The spec path, or 'issue #123', or null when none was found.",
    "items": [
      { "state": "missing | extra | wrong",
        "asked": "The spec line, quoted.",
        "what": "What the diff does about it, one sentence.",
        "file": "src/…", "line": 42 }
    ]
  },

  "checked": { "raised": 34, "kept": 10, "dropped": 24 }
}
```

Field notes:

- `file` `line` `lines` `fn` are copied from the diff, not recalled. `fn` is the anchor of
  record; `line` drifts.
- `evidence` is the literal line, byte for byte, never paraphrased. It is what makes the finding
  checkable in ten seconds.
- `what` is factual only. `why` and `fix` carry `[Assumption]` on their inferred parts.
- `confidence`: `traced` means you followed it in the code; `suspected` means you would have to
  run it, and caps `sev` at 3.
- `repo` shows on the page only when the set has more than one.
- `covers` names the finding ids a test step would catch.
- `fix` is doable without a follow-up question. If you cannot write that sentence, you do not
  understand the finding well enough to report it.

## Page order

Built in the `hk-writeup` skill's `SHELL.html`. One column, no rail: there is nothing to pick
from, so a rail would only list the findings twice. Top to bottom:

1. Kicker "PR review", the `product` as the title, chips for each PR (`repo #n`), the head and
   merge-base SHAs, files changed with lines added and removed, and the checks state.
2. An anchor nav: Readiness, Findings, What changed, Standards, Spec, How to test.
3. **Readiness.** One bold lead-in with the score and its word ("Readiness 3.4 out of 5, needs
   another pass."), then `readiness.why`, then "Fix first: <dimension>." Then a plain `.tbl`
   with one row per dimension: label, score or "does not apply", `why`. When the set has more
   than one PR, a second table with one row per PR: repo and number, title, author, its score
   and `why`.
4. **Findings**, grouped under Blockers, Major and Minor with the band's heading sentence under
   each. Each finding is one `<details class="row">`: `.row-name` is the severity in words
   ("severity four"), `.row-status` is the title. The body holds `what`, then `why`, then the
   `evidence` line in a `<pre>`, then a bold "Fix:" lead-in with `fix`, then `file:line` and
   `fn` in `.mono`, and "already raised on the PR" when `raised_already` is true. A band with
   nothing in it says so in one sentence.
5. **What changed.** `headline` in bold, `body`, then one sentence per change with its files in
   `code`, then `out_of_scope` if any.
6. **Standards.** One sentence naming the files the rules came from. Then one `<details
   class="row">` per entry: `.row-name` is the rule or the smell's name, `.row-status` is the
   file and line, the body holds `what` and the `evidence` line in a `<pre>`. Nothing found says
   so in one sentence.
7. **Spec.** One sentence naming `spec.source`, or "No spec was found, so this axis was not
   checked" when it is null. Then the items in three short groups — asked for and missing, in
   the diff and not asked for, implemented but wrong — each quoting the spec line. These two
   sections are reported as they came back: no merged list, no ranking across them, and neither
   moves the readiness score.
8. **How to test.** Setup commands each in a `.cmd` row, then the steps as an ordered list
   ("do, then expect", with the URL and the finding it covers), then `automated` and
   `cannot_test`.
9. Footer: replace the `.said` sentence with one saying this was read by hand at the merge-base
   SHA on the named model, how many findings were raised, kept and dropped, and that nothing was
   run.

Findings are fewer and better checked: six real ones beat twenty nobody reads, and past about a
dozen the low end is padding. At least one test step should try to break the change; the happy
path is not how anyone meets a bug.
