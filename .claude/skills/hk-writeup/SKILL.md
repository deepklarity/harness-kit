---
name: hk-writeup
description: Put what the user asked for onto one self-contained HTML page, written in the house voice. It adds nothing of its own — no summary, no score, no severities, no next steps, no sections nobody asked for. The look is a default you can change or throw out. One HTML file is the only thing it makes. Triggers on - 'write this up', 'turn this into a page', 'make an HTML of this', 'I want to show someone this', or /hk-writeup.
---

# Writeup

Shows what you asked to be shown. That is the whole job.

Two files, one in this skill's base directory and one at the repo root:

- `docs/fable_roadmap/TONE.md` — how it is written. The repo's own voice file, the one
  `AGENTS.md` points at. Read it before writing a word. This skill keeps no copy of it, so
  there is nothing here to drift.
- `SHELL.html` — an empty page with the colours, the type and the spacing. Drop the content in
  and change nothing else.

## The rule

**Put on the page what you were asked for. Nothing you were not asked for.**

Anything you add on your own is slop, however good it looks:

- No summary, headline or overview nobody asked for.
- No score, rating, readiness number or confidence level.
- No severity ratings, no colour-coded badges, no Critical / Major / Minor grouping.
- No "next steps", "recommendations", "how to test", "what changed", "risks", "open questions".
- No expandable rows holding detail nobody asked to see. If it fits on the page, put it on the
  page. If it does not belong on the page, leave it out — do not fold it away.
- No filler section to make the page look complete. A short page is a finished page.

Asked for the three options and what each costs? The page has three options and what each
costs. It does not also have a recommendation, a comparison table, or a verdict.

If you think something is missing, say it in the chat afterwards. Do not put it on the page.

## The only thing it makes is one HTML file

No JSON alongside it, no markdown copy, no notes file, no commit.

## You decide how the page looks

`SHELL.html` is a **default, not a contract.** It is what you get when you say nothing about the
look. Say anything about it and your instruction wins, without discussion: a different layout,
different colours, no theme button, built for printing, or start from a page you like and ignore
this one. Change the CSS, replace it, or throw it out.

Do not argue for the default, do not offer it as a compromise, and do not quietly keep half of
it. If a change costs something worth knowing, say it in one sentence and then build exactly
what was asked.

## How

1. Read `docs/fable_roadmap/TONE.md` at the repo root.
2. Write what you were asked for. Nothing else.
3. Read `SHELL.html`, replace the `<!-- CONTENT -->` line with the content and
   `<title>Writeup</title>` with the real title. Replace `HOW-THIS-WAS-MADE` in the footer with
   one plain sentence saying where this came from, or delete that line.
4. Write it to `writeup/<slug>.html` at the root of the repo this skill is installed in —
   `$(git rev-parse --show-toplevel)/writeup/`. Create the directory if it is not there, and
   never write to a hardcoded absolute path. Self-contained — no fonts or scripts from anywhere
   else, so it opens from a `file://` URL and survives being emailed.
5. Say where it went, in one line.

If you were not told what goes on the page, ask. Do not guess and do not fill it in.

## What the default shell styles

Plain HTML. There is nothing to learn and no class you have to use.

`h1` `h2` `h3` `p` `ul` `ol` `a` `strong` `code` `pre` `table` `details`

Four classes on top, for when the content actually calls for them:

| | |
|---|---|
| `<p class="lede">` | The line under the title |
| `<section>` | Wraps a heading and its content. `h2` inside gets the small caps rule |
| `<div class="cmd"><code>…</code><button class="btn">Copy</button></div>` | A command someone runs |
| `<div class="kvwrap"><table class="kv">` | A table that can scroll sideways on a phone |
| `<div class="note-line">` | An aside |

## Writing it

`docs/fable_roadmap/TONE.md` governs every sentence. Three things from it that get missed most
on a page like this:

- **Real numbers, real file names.** "This broke twice" beats "this is a known risk area".
- **Every line stands alone.** Someone who was not there should understand it from the sentence
  itself. No "see above", no bare reference to a label the reader would have to look up.
- **Big type does not change the writing rules.** A title or a lede is a plain spoken sentence,
  not a headline. "Gate green, two tracks live" is as much a costume as drama is.
