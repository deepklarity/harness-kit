---
name: hk-writeup
description: "Put what the user asked for, and nothing else, onto one self-contained HTML page in the house voice. Triggers on: 'write this up', 'turn this into a page', 'make an HTML of this', 'turn this into a report', 'I want to show someone this', or /hk-writeup."
---

# Writeup

Makes one self-contained HTML page. Two files carry it:

- `docs/playbook/tone_and_taste.md` (path from the repo root) — how every sentence is written.
  This skill keeps no copy, so there is nothing here to drift.
- `SHELL.html` in this skill's directory — the default look. Drop the content in. If the user
  says anything about the look, their instruction wins (see "The look is yours to change").

## The rule

**Put on the page what you were asked for. Nothing you were not asked for.**

Asked for the three options and what each costs? The page has three options and what each
costs. It does not also have a recommendation, a comparison table, or a verdict. A short page
is a finished page. If you think something is missing, say so in the chat afterwards.

## How

1. Read `docs/playbook/tone_and_taste.md`.
2. Write what you were asked for. If you were not told what goes on the page, ask that one
   question first.
3. Read `SHELL.html`, replace the `<!-- CONTENT -->` line with the content and
   `<title>Writeup</title>` with the real title. The footer's `.said` sentence says how the page
   was made; replace it with one plain sentence about this page, or leave the default.
4. Write it to `writeup/<slug>.html` at the root of the repo this skill is installed in —
   `$(git rev-parse --show-toplevel)/writeup/`. Create the directory if it is not there.
   `writeup/` is gitignored, so the page stays on disk and out of commits. Self-contained — no
   fonts or scripts from anywhere else, so it opens from a `file://` URL and survives being
   emailed.
5. Say where it went, in one line. That HTML file is the only thing this skill makes: no JSON
   beside it, no markdown copy, no commit.

## Pages other skills build here

Other skills borrow `SHELL.html` and this voice; their own contract says what goes on their
page. Three things hold across all of them:

- A page overwrites its slug on every run. A skill that needs history says why in one
  sentence and appends `-<run>`; nothing appends a date.
- Scores and severities are content when a person asked for an audit or a review, written as
  sentences and plain tables, never as badges, meters or pills. Severity is one scale, 1 to 5
  with 5 worst, and appears in words in a `.row-name` ("severity four"). A score is out of 10
  and sits beside a band table saying which findings set which band, the shape
  `docs/skills/audits/hk-test-case-audit/REFERENCE.md` uses.
- The footer's default `.said` sentence stays unless the skill says what replaces it.

## The look is yours to change

`SHELL.html` is what you get when the user says nothing about the look. Say anything about it
and that instruction wins, without discussion: a different layout, different colours, no theme
button, built for printing, or start from a page they like and ignore this one. If a change
costs something worth knowing, say it in one sentence and then build exactly what was asked.

## What the shell styles

Plain HTML first: `h1` `h3` `p` `ul` `ol` `a` `strong` `code` `pre` `details`. An `h2` is
styled only inside a `section`; a bare `table` is not styled at all, so a table is `.tbl`
inside `.tblwrap` (below). A page that uses nothing but those tags comes out right.

The classes, for when the content calls for them:

| Class | Use it for |
|---|---|
| `.page` | The single column everything sits in. Already in the shell. |
| `.kicker` | The small uppercase line above `h1` ("Skill audit"). |
| `.chips` `.chip` | A row of plain facts under the title: run number, file count, commit, model. Facts, never status badges. |
| `.nav` | Anchor links to the sections, under the title. |
| `section` with `h2` | One heading and its content. The `h2` gets the small uppercase rule; `<span class="count">` inside it for a count. |
| `.lede` | The one line under the title. |
| `.note-line` | An aside. |
| `details.row` with `summary` | A folded entry: a finding, a test, a step. Inside the summary, `.row-name` is the short label (severity in words, a file), `.row-status` the title as a sentence, `.sn` a small mono number. |
| `.dbody` | The body of a folded entry. `.mono` inside it for a `file:line`. |
| `.cmd` | A command someone runs: `<div class="cmd"><code>…</code><button class="btn">Copy</button></div>`. |
| `pre` | A whole line or block of code, an evidence line. |
| `.kv` in `.kvwrap` | A two-column key/value table that scrolls sideways on a phone. |
| `.tbl` in `.tblwrap` | A plain table with a header row: lens tables, before/after, inventories. `td.num` for a numeric cell. |
| `.foot` `.said` | The footer and its one sentence about how the page was made. Already in the shell. |
