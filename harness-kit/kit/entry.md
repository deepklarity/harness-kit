# Finish the harness-kit install

You are an agent at the root of a workspace holding one or more repos.
`npx @deepklarity/harnesskit init` has just run here. Follow this top to
bottom.

Init wrote reference material only: the kit's docs, empty indexes, the board,
and ready-made files under `docs/harnesskit/workflow/` (plus
`docs/harnesskit/MERGE_CLAUDE_MD.md` when a `CLAUDE.md` existed). It changed
no `CLAUDE.md`, `AGENTS.md`, `.claude/` or `.gitignore`; "Workflow files"
below does, with the person's yes. What is left is judgment: what this workspace is, what it lacks, a board that
lists the work, and then building that work: the docs, tools and tests the
board lists are built in this setup, before anything is proven.

## Hard rules

1. **Look before you write.** Until the survey row is done, run only
   read-only commands: listings, file reads, `git status`, `git remote -v`.
   After it, run only what the profile allows and nothing that bills.
2. **Never open a secret.** `.env` and variants, key files, pem files,
   credential JSON: note that they exist, never read or quote them.
3. **Never overwrite what the workspace owns.** An existing CLAUDE.md,
   README, script or doc is blended or left alone; a judgment call that passes the
   ask test under Decisions goes to the person as a decision row.
4. **`writeup/agent-readiness/RESUME.md` is the resume point.** If it
   exists, read it and continue.
5. **Keep the run log.** After each row, append one line to
   `writeup/agent-readiness/log.md`: the row, its cost (turns and tool
   calls as an estimate, tokens if your harness shows them), what it found.
6. **Write learnings down in the same turn, in the right place.** A
   learning about this workspace (a gotcha in its code, a recipe that
   stops on something only the person holds) is a board row: `kind`
   learning, `status` open, a title a person could say out loud. A note
   about these kit instructions (this document surprised you, a copied
   rule proved wrong against the code) is one line in
   `writeup/agent-readiness/kit-notes.md`: the kit file, what happened, what should
   change. That file is local and nothing reads it but the person; do not
   put it on the board or mention it in summaries. Never fix a copied file
   by editing it: it would drift from the manifest.
7. **Name a step by what it does and a row by its title** when you talk to
   the person, never by a number or id.

8. **Open every message to the person with the journey.** The seven
   stages in `docs/harnesskit/journey.md`, marked from the board (`✓`
   done, `▸` in progress with "n of m", `○` to come), then one line: what
   is happening now and what the person needs to do, or "nothing, I'm
   working". A first-time reader always knows where they are.

## The boundary

While this setup runs, the harness is built around the code, never into
it. Once setup is finished, code changes are ordinary work and this
boundary no longer applies. During setup, no session edits a file inside an
inner repo: not a `.gitignore` line, not a stale link in its
CLAUDE.md, not a failing test. Everything you write lands at the workspace
root. The boundary is on writing only: reading, searching and running
anything inside a repo is expected, and the entrypoint must say so.

When the workspace root is itself the only repo (its own source and `.git`
at the root, no inner repos), protect its source folders the same way for
the length of the setup.

What you notice inside a repo (a stale reference, a failing test, a tracked
secret) goes on the board as `kind` suggestion, `status` noted, one plain
sentence, the path in `notes`, never a patch. A decision row is only ever a
choice about the harness itself.

## Ask the person first

Before the survey, ask the questions that are the person's to answer: the
first four in one AskUserQuestion call (it takes at most four), the last two
in a second call, each with the proposed default first and marked
Recommended; without that tool, send one numbered message with the defaults
filled in, so the person can answer "all defaults". If `docs/harnesskit/profile.md` exists, read it and skip
this step: the person is never asked twice.

Use these words, so every person sees the same plain questions. Open with
the journey (stage 2 in progress) and one line: "Six quick questions; the
recommended answer is first, so you can accept them all."

| Key | Question | Answers (first is Recommended) |
|---|---|---|
| `budget_usd` | How much may setup and proof runs spend? | USD 10 · USD 5 · USD 25 |
| `runner` | How should proof runs appear? | In panes you can watch (`herdr`, when `command -v herdr` finds it) · In the background (`headless`) |
| `autonomy` | When should I stop and ask you? | Only before spending money or doing something hard to reverse (`money-or-irreversible`) · Before every change to the board (`every-change`) |
| `repos` | Which folders should the harness cover? | All of them (`all`) · the folders found, one per option |
| `skills` | Add the kit's skills to `.claude/skills/`? | All of them (`all`) · Let me pick categories · None (`none`) |
| `guard` | During setup, block edits to your code until you say so? | Yes, until setup ends (`on`) · No (`off`) |

Without `herdr`, `headless` is first. For `skills`, show the categories in
`docs/skills/skills.csv` with names and counts; then copy each chosen stub
from `docs/harnesskit/workflow/.claude/skills/<name>/` to
`.claude/skills/`, skipping and reporting any name already there, and tell
the person that deleting those folders undoes it. The `guard` step is under
"Workflow files".

The rest have an obvious default, so they are written, not asked; the
summary you show after the answers names them, and the person can change
any by editing the file:

- `model`, `effort`: for proof runs. sonnet, medium.
- `suites`: suites you may run unasked. The unit and mock suites, never
  one that bills.
- `audit_gate`: written only when the full gate takes over two minutes:
  the fast tier the harness audit runs instead (`scripts/verify.sh unit`).
- `scenarios`: how many proof scenarios. 3: one easy, one mid, one complex.

Write the answers to `docs/harnesskit/profile.md` before anything else, a
`# Harness profile` heading and one `key: value` line per key above
(`budget_usd: 10`, `runner: herdr`, `suites: unit,mock`, and so on).

Later steps obey it: `budget_usd` gates every paid run, `autonomy` decides
which questions become decision rows, `suites` what the verify gate may run
unasked.

## Workflow files

Each step has four beats: **show** the change (a diff, or the file list);
**ask** (AskUserQuestion, else a numbered message; the default is no change,
a no is noted on the board, never re-asked); **do** it from
`docs/harnesskit/workflow/`, merging without touching other content; **tell**
in one line what changed and how to undo it. Keep that list for Stop and show.

- **Guard hook**, right after the answers, if `guard` is `on`. Show what it
  blocks and the PreToolUse entry in
  `docs/harnesskit/workflow/.claude/settings.hook.json`. Do: copy `guard.mjs`
  and `protected.txt` from the workflow `.claude/hooks/` into `.claude/hooks/`
  and merge the entry into `.claude/settings.json` (created if absent, other
  settings untouched; invalid JSON: stop and ask). It comes out at Stop and show.
- **CLAUDE.md**, when the entrypoint draft is final. Show a diff of the
  draft against theirs, or the new file; theirs is merged by proposal, never
  replaced.
- **AGENTS.md** and **.gitignore**, at Stop and show. Show
  `workflow/AGENTS.md` and copy it only if missing; show the lines of
  `workflow/gitignore.lines` the `.gitignore` lacks and append those.
  Init already excluded `.harnesskit/` locally.

## The seeded board

Init seeded `writeup/agent-readiness/` with one open row per step (workflow
files, survey, guard list, checklist score, entrypoint, decisions, board), `RESUME.md`,
`log.md` and the rendered page. Work the rows in order; a row's
`closes_when` is its definition of done, and you set it `done` when that
proof is on disk. Every finding, question and decision lands on the page
before it is said in chat, and every `board.csv` change is followed in the
same turn by
`node docs/harnesskit/templates/mission-control/render-board.mjs writeup/agent-readiness`
(the installed renderer; there is no copy in the folder).

What the rows do not say:

- **Guard list.** Write the repo folders you found (or, in a single-repo
  workspace, its source folders), one per line, into
  `.claude/hooks/protected.txt`, so setup cannot edit them. With the guard
  off there is nothing to write. The last step removes the guard.
- **Verdict.** Rewrite the page's one-line verdict each time a score or
  the next step changes; after scoring it never still says nothing was
  assessed.
- **Score.** Each checklist area gets 0 to 10 with the file that justifies
  it. A "not verified, ask the human to run" command becomes a board row;
  the evidence says "not run here".
- **Entrypoint.** Follow `MERGE_CLAUDE_MD.md` if it exists, with a decision
  row where the two files disagree; otherwise fill the slots, in
  `writeup/agent-readiness/CLAUDE.draft.md`, 160 lines at most; a slot with
  nothing to say loses its line. The file routes (task to one of three entry files: `docs/breadcrumb_analysis/_INDEX.md`, `scripts/README.md`, `docs/_INDEX.md`) and stays under 1,500 words. Each entry file lists only areas (folders); each area has its own index; pages and scripts hold the detail. Fill the shell slot from `echo $SHELL`. An inner repo's CLAUDE.md is a source to
  check, not copy. Then audit it: run `node
  docs/harnesskit/templates/scripts/check_claude_md.mjs
  writeup/agent-readiness/CLAUDE.draft.md`, read the draft against
  `docs/adoption/CLAUDE_MD_AUDIT.md` (a subagent, or you, listing the file
  read for each claim about the code), fix every failure, and repeat until
  both pass. Log the before and after pass counts in `log.md`.
- **Decisions.** `kind` decision, `status` open, the question as title,
  `closes_when` "owner answers", and `notes` starting "Proposed: <the
  answer, one sentence>." then why; the page lists it under "What needs
  you" as Decide, shows that sentence as the answer and a reply of "yes"
  takes it (`owner_action` and `owner_reply` override both when the choice
  needs more words). Ask only what spends money, cannot be undone, changes a workflow file, or
  needs something only the person holds (a credential, an intent the code
  cannot show). Everything else is decided, not asked: rebuilding an
  untracked venv, keeping a convention the workspace already follows,
  leaving `writeup/` ignored. Do it, say it in one line, and record a
  `decided` row with the reason so the person can reverse it. Often there
  is nothing to ask; then say so and go on. Otherwise tell the
  person "The page has N decisions for you, each with a proposed answer;
  say yes or change the answer on the row", leave no `{{...}}` slot, and
  stop. When answers come, set each row `decided`, record it in
  `RESUME.md`, apply it, and install the draft as `CLAUDE.md` by the
  CLAUDE.md step above.
- **The board.** Each unchecked checklist item gets the rows its "When
  missing" table names: root rows become tasks, inside rows become
  suggestions. A task row has a title a person could say out loud, a
  `closes_when` that is the proof (checked by you on disk, not a subagent's
  report), a priority, and in `notes` the tenet and the model tier. A row
  that writes a document names its reader, the question it answers, and
  `docs/playbook/tone_and_taste.md`. Set the breadcrumb row's `closes_when`
  from the questions this workspace's testing docs, scripts and ops tasks
  show arriving.
- **Verify gate.** The row runs the gate, only the suites the profile
  lists; each failure becomes a suggestion. Start from
  `docs/harnesskit/templates/scripts/verify.sh`. A recipe that stops on
  something only the person holds becomes a decision row.

## Stop and show

Setup is over, and from here code changes are ordinary work. Remove the
guard hook: its `.claude/settings.json` entry (others stay), `guard.mjs`
and `protected.txt`. Delete any sentence
in `CLAUDE.md` that names `protected.txt` or says agents never touch folders
while setup runs, so no session checks permissions for a rule that ended.
Then the AGENTS.md and .gitignore steps.

Re-score the twelve areas in the page's Readiness table: a moved score
becomes "before → now" in its one Score cell (`2 → 5`). Write the run's cost in
`log.md`, and make the headline what is true and what happens next, in
about twelve words. Commit the workspace root, entrypoint included (a root
that is not a repo is the person's call, so ask). A later session that
edits `CLAUDE.md` commits in the same turn and makes only two kinds of
edit: a fact corrected against the code, or a link to a missing area.

Tell the person every workflow file setup changed and how to undo each, then: both scores, what was blended, what they decided, the
first three open rows, every learning about the workspace by title. Then stop; a fresh
session works the first open row.

## Write the scenarios

Only once the board has been worked: every row that builds a doc, a tool
or an audit is done and the gate is green. The proof measures this harness;
proving a skeleton measures nothing. First run the harness audit
(`docs/adoption/HARNESS_AUDIT.md`): every area must score 7 or more, or the
person accepts the gap; the proof confirmation shows its scores. Close connection and tool gaps first: every uncovered link in the `connections` area, every `index` depth fault, and every database or queue component without an inspect and a logs tool becomes a board row, worked before any scenario is written, so the scenarios can use them. Then write the proof scenarios into
`writeup/scenarios/` from `docs/harnesskit/templates/scenario.md`. Pick as
many archetypes from `docs/harnesskit/templates/scenario-archetypes.md` as
`scenarios` in the profile, spread over its difficulties, and fill each in
with this workspace's own setup, data and commands (a `feature` scenario
follows the last sections of the archetypes file). Name the harness piece
it exercises on the `Exercises:` line. Run each proof command once on the
untouched workspace and see it fail. Proof commands only read and never
call a provider; a scenario that must bill says `Bills: yes` and runs only
if the person names it.

## Prove it

Details are in `docs/proof.md`. Proving is never automatic: nothing paid
runs before a yes.

1. Run `docs/harnesskit/harnesskit prove writeup/scenarios/<file>.md --dry-run`
   for each scenario. Then stop and ask in ONE AskUserQuestion call (a
   numbered message if the tool is absent): list the scenarios (number,
   title, kind, what it exercises) and the total estimate against the
   profile's `budget_usd`. Options: "Run them within $<budget>
   (Recommended)", "Add scenarios first" (they describe them; write them
   from the archetypes, re-estimate, ask again), "Change the budget", "Not
   now" (leave the proof row open on the board as an owner row with
   owner_kind "Decide"). If the estimate exceeds what remains, recommend the
   cheapest subset that fits, named. Write the answer to `log.md` and a
   changed budget to `profile.md`.
2. On a yes, run one scenario at a time without `--dry-run` and report each
   result before the next. A run that would pass the budget is refused.
3. A `herdr` run opens panes and returns; `prove collect <slug>` reads the
   finished transcripts, a judge session follows
   `docs/harnesskit/proof/judge.md`, and `prove render` rebuilds
   `writeup/proof/index.html`.

## Upgrades

Run `npx @deepklarity/harnesskit init` again: it replaces copied files the
workspace has not edited and lists edited ones as drifted (`--check` lists
without writing, exit 1 on drift). Show the drifted list, re-score, and add
rows for anything that regressed.
