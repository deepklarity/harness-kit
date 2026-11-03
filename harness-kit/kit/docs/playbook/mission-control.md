# Mission control: the shared picture of one initiative

For anything long-running, on the page and in chat: when a run or a
subagent starts, say what is running, the clock time it should finish
and what happens after, and say it again if the estimate changes.

## Why it exists

The owner (the person who owns the initiative) is thinking about ten things at once; an agent session starts
remembering nothing. Mission control is the one page holding the shared
picture of one initiative, so either can arrive cold, read for three
minutes, and act.

## How it talks

What the words on the page must do:

- **A one-line verdict at the top** that says the situation, including
  the bad part. A reader who stops there has the truth.
- **Every claim carries its own evidence**: the number, the file path,
  the command, the count. A claim without its evidence is an opinion.
- **Worst first**, and **it reads without its author**: no sentence
  needs a person in chat to explain what it meant.
- **A stat appears once**, and each grade is a sentence with its own
  evidence, never a bare word.
- **Status pills carry the word**, never colour alone.
- **Heavy secondary material folds** behind a closed toggle whose
  summary keeps the one-sentence outcome visible.

Voice rules are `tone_and_taste.md` in this folder: plain sentences,
bold as the focus map, no epigrams. That file wins on wording.

## How it looks

The template (`templates/mission-control/mission-control.html`) carries
the CSS and the structure; reuse it rather than restyling.

- **Always light.** The page pins `data-theme="light"` and ships no dark
  palette.
- **A soft page ground with one white card per section.** Each card opens
  with a small mono uppercase label and a short blue dash. The cards do
  the sectioning: the eye finds "What needs you" by its card, not by
  scanning headings in a wall of text.
- **Two system font stacks, nothing fetched**: a sans for text, a mono
  for ids, labels and times. One offline file.
- **Status pills in tables** (open, agent working, blocked, done, fixed,
  decided, dropped) coloured and always carrying the word. Done items
  stay readable, never struck through: their proof is information.
- **The container follows the content.** Three or more things of the
  same shape are a table, one row each, short cells. Text a reader needs
  in place to act on a row (the full ask, the reply that closes it)
  opens in the row on a click, never in a modal or wrapped into the
  cell. A group the reader mostly skips (closed rows) is one collapsed
  `<details>` line with its count. More than seven rows, or two kinds,
  get filter chips ("all N" plus one per kind with its count); past about
  fifteen rows, a filter box. A card per item is a table that lost its
  columns.
- **A tracked section carries its own totals**, generated with it, so
  the shape of the work shows without reading every row. Decoration that
  replaces the sentence (a bare count, an unexplained badge) is out.

## What goes on the page

Four screens in reading order, one table shape throughout. Each answer
lives in one place; a fact stated twice will eventually drift. Under the
title come the verdict line and a muted line, "Rendered <time> by
render-board.mjs from board.csv", so a reader knows how fresh the page is.

1. **Where are we?** Written by the agent, to the owner, in its own
   words, never generated from the board: four labelled paragraphs.
   **Done**: what now works, with the proof. **Waiting on you**: each
   thing, numbered, and why. **Next for me**: what the agent does next,
   in order. **Broken**: anything known broken, or "nothing new". Plain
   words, no task ids. It sits outside the generated markers, so the
   renderer keeps it; the template ships it with unfilled slots. Rewrite
   it from scratch at the end of every session that changed state; if
   the owner has to ask "where are we?", the section failed.
2. **What needs you, the owner.** One numbered table with one row per
   board row at status `owner` or `human-review` (and every open
   decision), whatever its kind. A task that waits on the owner to read,
   approve or run something is as blocking as a decision; a page that
   files it under the work says "nothing needs you" while the owner is
   the blocker. Columns: `#`, a coloured kind word, the row id,
   priority, and "What you do". Filter chips read "all 11", then one per
   kind with its count. Each row expands in place to the full ask and
   "To close it:". The kind is one of four: *Decide* (a choice only the
   owner can make; these come first), *Review proof* (read what the
   agent attached), *Try it in the app* (click through and look), *Run
   it yourself* (a command and what its output should show).

   **What you do** is the first sentence of the ask, in the imperative,
   naming what to open or run: a URL, a file, a command. Never "please
   review T-24". A *Decide* row is a question answerable without opening
   another file: the question first, the problem today with a concrete
   example, **Yes:** what changes and how big, **No:** what stays and
   what that costs, then "I recommend ..." with one reason. Never
   "approve or strike this proposed task" or a bare file pointer.
   **To close it** is the reply that settles the row, choices spelled out
   ("Reply \"lift the rule\" (recommended) or \"keep it\""). The section
   is either unmissable or says "nothing right now"; nothing else may
   block on a human. A person is asked for a judgment that is theirs, or
   permission before real money or an undoable action; anything an agent
   can do or check itself, it does. A change to code, tests or config
   inside a workspace repo is a suggestion (what and where, no patch),
   never a decision or a task.

   **Discussion notes** sit right under it when `discussion.csv` exists
   (the section is omitted otherwise): questions the owner wants to settle
   with other stakeholders, not things that block the work today.
   Columns: `id` (Q-n), `raised`, `status` (open, decided), `question`,
   `today`, `options` (`option :: what it means :: cost or risk`, joined
   by `||`), `recommend`, `code`. One row per question, chips open /
   decided / all. Written for people who did not build the system: the
   question in business terms; **What happens today** in a sentence or
   two with a real example and no internal names; **Options** as a small
   table; **My recommendation** naming the option, then one sentence of
   why; code references last, in one muted line. A settled note becomes
   a `decided` board row and the note's status says so.
3. **The work.** Only what is with the agent (open, doing, blocked) and,
   folded, what is closed. One table from the board for tasks and bugs
   alike: task id, priority, kind, what, closed-when (with "after <ids>"
   while a dependency is open), status pill, started, done, and a detail
   toggle that opens the row in place. Chips: all, open, done, decided,
   dropped, with counts. A totals line reads "N with the agent, M waiting
   on you (listed above, not here), K closed". A row waiting on the owner
   is never listed here: a "human review" pill in this table would hide
   that the owner is the blocker. Open rows come by priority, then
   dependency order, so the first is what happens next; closed rows fold
   under "K closed rows, with their proof". There is no separate
   next-steps list, roadmap or bug table: a bug a task closes lives in
   that task's detail. Suggestions and learnings render as a plain list
   in a folded card after the work, "Noticed along the way": one sentence
   each, its file in small mono, no patch.
4. **Instruments and references.** One short table: a link and a
   one-line description each, covering the board, RESUME, the log, run
   reports, guides and audit pages. Live numbers live in the
   instruments, not here. This card is the agent's, outside the markers.

**One board.** `board.csv` holds tasks, bugs, decisions, suggestions and
learnings, one row shape: `id, kind, title, status, priority, depends_on,
closes_when, how_proven, notes, started_at, done_at, owner_kind,
owner_action, owner_reply`. A board with the first eight columns renders
too. Statuses: open, doing, blocked, owner, human-review, done, fixed,
decided, dropped; suggestions use noted and taken, learnings open and
folded. `owner` means the row waits on the owner. `human-review` means
the agent finished and attached proof; only the owner moves it to done.
`started_at` is set when a row first goes to doing, `done_at` each time
it reaches human-review or done (`YYYY-MM-DD HH:MM`, local time).
`closes_when` says what must be true; `how_proven` says how anyone checks
it, written before the work starts: the agent part (tests by name, a
script) and the human part (UI clicks, or a script for the owner to run),
each with what a pass looks like. A task with an empty `how_proven`
cannot go to human-review. `owner_kind` (decide, review, click, run; a
decision defaults to decide), `owner_action` (falls back to
`closes_when`) and `owner_reply` are filled whenever a row moves to
`owner` or `human-review`. Priority is P0, P1, P2 and orders every table.
The installed `render-board.mjs` (in `docs/harnesskit/templates/mission-control/`,
run with the initiative folder as its argument) writes sections 2 and 3
and the noticed card; every row's notes open in the row itself, so a
cell never holds a story. Learning rows: `docs/harnesskit/entry.md`,
hard rule 6.

Settled decisions are decision rows with status decided, and they also
live in RESUME.md for agents, who are the ones tempted to re-open them.

## What stays off the page

- **History** lives in `log.md` next to the page: append-only, one
  entry per session, same voice, order set once in `RESUME.md`. It
  exists so a session can audit how things got here; the owner is not
  expected to read it, and no current fact may live only there.
- **Design and reference** live in their canonical doc for the
  workspace; the page links to them, never carries a second copy.
- **Live data** (counts, durations, per-test results) belongs to the
  instrument; the page links to it and never mirrors its numbers,
  because mirrored numbers are stale numbers.

## Mechanics

- The initiative folder is `writeup/<initiative>/`: `board.csv`,
  `mission-control.html`, `log.md`, `RESUME.md`, and `discussion.csv`
  when there are notes. A session reads the board and RESUME,
  re-renders the page, and does not read it; where RESUME and the board
  disagree, the board wins and RESUME gets fixed.
- Day to day, open the file locally; a published copy is a snapshot,
  republished at milestones to the same URL.
- `board.csv` is the one place task, bug and decision state is
  written; `render-board.mjs` generates the owner, discussion, work and
  noticed sections and the Rendered line from it and rewrites the page
  head from the template on every render, so a template upgrade reaches
  existing pages; never hand-edit those. Prose outside the BOARD markers
  (the title, verdict, "Where are we?", the references) is yours and is
  kept. Update the board and re-render in the same turn as the change.
- Sections 1 to 3 fit in about two screens and read in three minutes;
  past that, cut rows or move them to the log (`visual_clarity.md`).

## Rules learned the hard way

Keep this list growing.

1. **When a milestone lands, sweep every place that states state in one
   pass** — verdict, grades, waiting-on-owner, next steps, bugs — or the
   page ends up saying, simultaneously, that something passed, was
   blocked, and was still to be written.
2. **Never refer to anything by number or position** ("checkpoint 3");
   it breaks silently when the list reorders. Requirements live inside
   the item they apply to; references use names.
3. **A phase is done when its checks pass, everything they found is
   fixed or has a written decision, and the suite itself has been
   reviewed.** First green is one third of done.
4. **Freshness is part of the contract.** A session that changed
   reality and did not update the page did not finish.
5. **One initiative, one page.** A second roadmap gets its own folder;
   the parent keeps one paragraph and a link.
6. **No dates on notes.** A checked box carries proof, not a date — the
   log and git carry time.
7. **Voice drifts by imitation, so sweep it like state.** Reread
   `tone_and_taste.md` before editing; when it gains a rule, sweep this
   page against it in the same pass.
8. **A number lives in one place, and a grade is a sentence.** "Area:
   word" headings are banned; they train later sessions to write
   fragments.
9. **The verdict and grades name things by what they do, not by their
    identifiers.** A grade built from tool flags, service names and
    commit hashes reads as jargon to the owner; those belong in the log
    or the linked page, not the grade, unless the reader must type one.
10. **One status vocabulary for the whole page.** Pills and grades speak
    the same small set; a new word joins only when no existing one is
    honest, and then everywhere.
11. **Finished work never sits under a next-actions heading, even
    relabeled** — a heading's meaning is what belongs under it, not the
    words inside each item.

## Starting a new initiative

1. Make `writeup/<initiative>/` with the four files: `board.csv`,
   `mission-control.html`, `log.md`, `RESUME.md`.
2. Reuse the page template's CSS (cards, tables, pills, chips), light
   only, no build step.
3. First log entry: what was agreed, confidence, known risks.
