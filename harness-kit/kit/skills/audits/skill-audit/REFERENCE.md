# Skill audit — Reference

## The seven lenses

Read the skill through each, in this order, and write findings as you go. The lens is stored on
each finding as `lens`; the page groups by severity, not by lens, but the lens word tells the
reader which kind of problem it is.

### 1. Purpose

Does the skill say, in its first paragraph, what it does and what it makes? Is that one job, or
several jobs wearing one coat? Does the description (the always-loaded pointer) name the branches
that should trigger it, front-loaded, one trigger per branch, without restating what the body
already says?

Checks: read the first 20 lines; count the distinct things the skill promises to produce.

### 2. Runnability

Could an agent follow this skill, in this repo, today, without asking a question? Every file,
script, command, env var, port and path the skill names must exist as written. Every step must
end where the agent can tell done from not done.

Checks, run them:

```bash
ROOT=$(git rev-parse --show-toplevel)
SKILL=$(ls -d $ROOT/docs/skills/*/<name>)   # skills live under docs/skills/<category>/; .claude/skills/<name> is a stub
grep -oE '`[^`]+`' $SKILL/*.md | sort -u          # every backticked token; test the ones that are paths or commands
for f in <paths named>; do test -e "$ROOT/$f" && echo "ok $f" || echo "MISSING $f"; done
```

A skill that ends by asking the user a yes/no question, prints a status token for another
harness (the harness's own status markers), or writes its report somewhere the repo's rules forbid,
fails this lens.

### 3. Structure

The `matt-writing-for-agents` ladder. Are the steps in order at the top, reference below or
behind a pointer, and nothing the agent needs buried under something it does not? Is any meaning
stated twice? Are there sentences the agent would obey anyway (no-ops: "be thorough", "do not
hallucinate", "analysis only" repeated three times)? Are prohibitions phrased where a positive
target would do? Is the skill longer than its content (sprawl)? Is there a leading word doing
the work of a paragraph, or a paragraph that should be one?

Checks: word count of each file; count of the word "NEVER"/"Do NOT"/"MUST"; list every
sentence that names the same rule as another sentence.

### 4. Voice

`docs/playbook/tone_and_taste.md`, applied to every string a person will read: the description
line, headings in any report it produces, the wording it tells the agent to use, the chat it
tells the agent to send. Grading words, squeezes, epigrams, timestamps on notes, IDs travelling
alone, all-caps shouting, "CRITICAL", "forensic", "senior engineer" role-play.

Checks: grep the skill for the words the tone doc lists and for ALL-CAPS runs of more than two
words.

### 5. Output contract

What the skill makes and where it lands. In this repo a page is built in
`docs/skills/knowledge/hk-writeup/SHELL.html`, under the rules in that skill's `SKILL.md`; a
report-producing skill produces content, not a template of its own; the file lands in
`writeup/` at the repo root (ignored by `/writeup/` in `.gitignore`), never at the root of a
service, never in `docs/` (except `docs/loop_audits/`, which CLAUDE.md names), never in `$TMPDIR`, and as markdown only when the person asked for
markdown. Findings carry the literal evidence line and a `file:line`. Scores and severities
appear only when the person asked for an audit or review, and then as sentences and tables,
not badges or meters. Nothing goes on the page that nobody asked for.

Checks: where does the skill say the output goes; `git check-ignore -v <path>` on that path;
does the skill name its own template or the shared shell; does it end by saying where the file
went.

### 6. Repo fit

Does the skill contradict a standing rule in `CLAUDE.md`? The ones that bite audit and test
skills here: never `git stash` outside a task worktree; never a `MEMORY.md`; explore and read
subagents on haiku, judgment on the strongest model; tests pass does not mean the feature
works, so a fix is verified live; tests are written by an agent with no implementation
context, confirmed failing, before implementation; the diagnostic scripts CLAUDE.md names before reading code, never `curl`; commit or push only
when asked. Does it name the real test command (`scripts/verify.sh`), the real diagnostic
script, the real pattern index (`docs/patterns/`, `docs/breadcrumb_analysis/_INDEX.md`)?

Checks: read `CLAUDE.md`; grep the skill for `stash`, `commit`, `curl`, `pytest`, `haiku`,
`opus`, `sonnet`, `MEMORY`.

### 7. Overlap

Is there another skill in `docs/skills` that already does part of this job, with a trigger
phrase that collides? Two skills answering "audit our tests" is a coin flip at invocation time.
Say which one should own the trigger and what the other should shrink to, or whether one should
be deleted.

Checks:

```bash
grep -l "<trigger words>" $ROOT/docs/skills/*/*/SKILL.md
```

## Severity, 1 to 5 (5 worst)

Judged on what an agent running the skill does because of the line.

**5 — The skill cannot finish or does harm.** A required file or script that does not exist, a
step that launches a paid run without saying so, an output path the repo forbids, an instruction
to modify source in an audit.

**4 — The skill finishes the wrong job for a real case.** A completion criterion so loose the
agent stops early, a source-of-truth pointer to a file outside this repo, a contradiction with a
`CLAUDE.md` standing rule, a page shape the shell does not style.

**3 — Real problem, small blast radius.** A no-op paragraph that buries a live step, a rule
stated twice with different wording, a description that will not trigger on the phrase people
actually use, a trigger collision with another skill.

**2 — Friction.** A grading word, a squeeze, a heading that is a label instead of a sentence, a
role-play opener, an ALL-CAPS instruction.

**1 — Nit.** A typo, a stray blank line, a duplicated heading.

`suspected` (you would have to run the skill to be sure) caps at 3.

## Verdict

| Verdict | When |
|---|---|
| runs as written | nothing at severity 4 or 5, and at most two at severity 3 |
| needs a pass | one or two at severity 4, or three or more at severity 3, nothing at 5 |
| rewrite | any severity 5, or three or more at severity 4 |

The findings set a ceiling; judgment can only lower the verdict, and the `why` says what did.

## Score, 0 to 10

Printed beside the verdict, with the sentence that derives it. Bands: [../README.md](../README.md).

## Word caps

| Field | Cap |
|---|---|
| verdict `why` | 20 |
| score sentence | 30 |
| finding `title` | 10 |
| finding `what` | 20 |
| finding `why` | 45 |
| finding `fix` | 45 |

The `fix` cap is wider than a PR review's because the fix is usually replacement text.

## Finding shape

```json
{ "id": "<file>-<problem>", "sev": 4, "lens": "runnability", "confidence": "traced",
  "title": "10 words, a statement",
  "file": "docs/skills/<category>/<name>/SKILL.md", "line": 42,
  "evidence": "the literal line, byte for byte",
  "what": "One factual sentence, no [Assumption].",
  "why": "What the agent does wrong because of it. Inferred parts carry [Assumption].",
  "fix": "The replacement text, or 'delete the line', or the file to add." }
```

Rank: higher severity first, `traced` before `suspected`, lens order as above, then file and
line ascending.

## Page order

Built in `docs/skills/knowledge/hk-writeup/SHELL.html`, using only what the shell styles:
`.page`, `.kicker`, `h1`, `.chips` and `.chip`, `.nav`, `section` with `h2` and `.count`,
`.lede`, `.note-line`, `details.row` with `summary`, `.row-name`, `.row-status`, `.sn` and
`.dbody`, `.mono`, `.cmd` and `.btn`, `pre`, `.kv`, `.tblwrap` and `.tbl` with `td.num`,
`.foot` and `.said`. The verdict, the score and the lens table fit on the first screen; each
finding is one short row below them.

1. `.kicker` "Skill audit", the skill's name as the `h1`, `.chips` for the run number, the
   commit, file count, word count, the frontmatter flags (`generated`, `imported`), and the model that read
   it. A page describes one version of the skill; a rewrite gets its own page and run number.
2. A `.nav` of anchors: Verdict, Findings.
3. **Verdict.** One `section`: a bold lead-in with the verdict word and the score out of 10,
   then the sentence behind them. Then the lens table as a `.tbl` inside `.tblwrap`: one row
   per lens, one sentence each.
4. **Findings.** One `section` per band, with the count in `.count` on the `h2`: Fix before
   using (severity 5 and 4), Fix soon (3), Friction (2 and 1). Each finding is one
   `details.row`: `.row-name` is the severity in words, `.row-status` the title as a sentence;
   the `.dbody` holds the factual sentence, why it matters, the evidence line in a `pre`, the
   fix, and `file:line` in `.mono`.
5. `.foot` with `.said`: read by hand on the named model at the named commit, how many findings
   were raised, kept and dropped, and the audited skill was not executed.
