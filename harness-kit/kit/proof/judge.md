# Judge packet: {{TITLE}}

One judge session, on the strongest model available, blind on the text. It reads both
answers shuffled as A and B (the mapping is sealed away from you), the
two run records for the process call, and the scenario file for the
prompt, the hints, and (for an outcome scenario) the proof command's output.

Primary question, per answer, is one: **did it achieve what was asked, yes
or no.** For a question, that means every part of the question is answered
correctly. For an outcome, it means the proof command passed and the answer
reports truthfully what it did. There is no "partly": an answer that leaves
a part unanswered or wrong is a no, and say which part.

Then check every concrete claim against the code (and the database, logs or
output folder for an outcome scenario), read both transcripts, and return
the verdict in the exact json shape below.

## The question

{{PROMPT}}

## Things a correct answer is expected to cover (hints for you only, never shown to either agent)

{{MUST_MENTION}}

{{PROOF_OUTPUT}}

## Answer A

{{ANSWER_A}}

## Answer B

{{ANSWER_B}}

## Run record for A

```json
{{VERIFY_A}}
```

## Run record for B

```json
{{VERIFY_B}}
```

## Feature scenarios: the quality object

When the scenario is `Kind: feature`, each answer's packet section lists
three facts: hidden tests, suite and lint, each passed, failed or not
configured, and shows `git diff --stat` and the diff (cut at 400 lines;
the worktree holds the rest). The facts are not yours to reinterpret: cite
them in `achieved_why`. Hidden tests failing means `achieved` is "no". A
failed suite is a regression: name it. Read the diff, not only the agent's
message, then add a `quality` object to each answer:

```json
"quality": {
  "correctness": 1, "fits_codebase": 1, "tests": 1, "scope": 1, "verification": 1,
  "overall": 1,
  "quality_why": "one sentence naming the strongest and weakest of the five"
}
```

Integers 1 to 10 each. Judge the five independently; `overall` is your
judgement of the whole, not their mean.

- **correctness**: 2 = the main case fails or crashes; 5 = the main case works, edge cases in the request are wrong or missing; 8 = every case the request names works, one small gap; 10 = all of it works, including cases the request implies.
- **fits_codebase** (conventions, structure, naming): 2 = ignores how the repo is laid out, new style throughout; 5 = in the right place but names, error handling or structure differ from neighbours; 8 = reads like the neighbours, one small deviation; 10 = a maintainer could not tell it from their own code.
- **tests** (did it add or update tests that matter): 2 = none, or tests that assert nothing; 5 = tests for the happy path only; 8 = covers the main behaviour and a failure case, fails when the feature is broken; 10 = also pins the edge cases the request implies and updates affected existing tests.
- **scope**: 2 = rewrites or reformats unrelated code; 5 = the feature plus a few unrequested changes; 8 = only the feature and what it needs, one stray edit; 10 = exactly what was asked, nothing more.
- **verification** (evidence it ran what it built): 2 = claims it works, ran nothing; 5 = ran something, but not the feature end to end or not the repo's suite; 8 = ran its tests and the feature, said what it saw; 10 = ran suite, lint and the feature itself, and reports the output truthfully.

## How to pick the preferred answer

There is one preference, overall. Decide it in this order, and stop at the first rule that separates the two:

1. **Achieved beats not achieved.** An answer that did not achieve what was asked, or carries a blocking wrong claim (a developer following it hits a wall or goes the wrong way), loses.
2. **Between two that achieved with no blocking error, the more efficient one wins.** Efficiency is the cost, wall time and tokens read in the run records, and whether the answer used what the workspace already ships (a script, a doc, a command) instead of reinventing it. Minor and cosmetic claims (a line number off, an aside that is wrong but that nobody would act on, a slightly less complete list) do not outweigh a clear efficiency gap; a good harness gets a correct-enough answer fast and cheap, not a perfect answer slowly.
3. **Only when both achieved and are close in efficiency** does completeness and precision of the text decide.
An answer that cites a file it did not open because a breadcrumb or guide it did open named it is the harness working as intended, not a fault; if that doc was wrong, the error shows up as a wrong claim and is judged as one. An answer whose run opened nothing at all ("Files this run opened: none" and no tool calls) is marked down however correct it looks, even when it cites real files: it came from the model's memory, nothing in this workspace checked it, and on code the model has not seen it would be wrong. Between such an answer and a correct one that checked the code, prefer the one that checked.

Say in `why` which rule decided it.

## What to return

Return exactly this json shape, one object per answer under "A" and "B",
plus the three closing fields. No prose outside the json. In every prose
field (`achieved_why`, `why`, `preferred_why`, `one_sentence`, `what_the_harness_bought`, the `claim` and `why` of a wrong
claim) refer to the answers only as "Answer A" and "Answer B", never a bare
"A" or "B" and never "before" or "after": the page replaces "Answer A" and
"Answer B" with the run each one was, so a bare letter is left unreadable.

```json
{
  "A": {
    "answers_the_question": "yes|no",
    "achieved": "yes|no",
    "achieved_why": "one paragraph: which parts landed, which did not, checked against the code/db/output",
    "wrong_claims": [
      {"claim": "...", "severity": "blocking|minor|cosmetic", "why": "...", "checked": "how you checked it"}
    ],
    "missing_essentials": ["..."],
    "process": {
      "injected": "what was injected at session start, from the run record",
      "files_opened": 0,
      "harness_used": ["every doc, script, skill or hook this answer actually touched, by path"],
      "cited_not_opened": ["..."],
      "leaky": false,
      "cheating": "none, or: claiming a check it did not make / inventing a file / reporting a tool it never ran",
      "what_the_harness_bought": "one sentence, or empty for the before half; use the 'Harness files opened' line under the answer, and when it says none, say what injected context (CLAUDE.md) could have contributed"
    },
    "quality": "feature scenarios only: the object above",
    "one_sentence": "one sentence summing up this answer for a reader"
  },
  "B": { "...": "same shape as A" },
  "preferred": "A|B|tie",
  "preferred_why": "one sentence, under 25 words, a reader can take in on the row: what the preferred answer did that the other did not",
  "why": "one paragraph: which answer a developer should trust overall, and why, in the terms of the criteria above"
}
```
