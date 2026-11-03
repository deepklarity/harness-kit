---
name: hk-pdf-export
description: "Turn an HTML page, or content that has to become one first, into a PDF in the house design and check nothing was lost. Triggers on: 'make a PDF of this', 'export this to PDF', 'can I print this', or /hk-pdf-export."
argument-hint: "<path to an HTML page, or the content to turn into one>"
allowed-tools: Bash, Read, Write, Edit, Glob, Skill
---

# PDF export

Takes a page and produces a PDF a person can hand to someone. The house look comes from one
stylesheet, the conversion from one script, and a PDF that lost half its content looks exactly
like one that did not, so the checking is the job.

Two files in this skill's base directory:

- `PRINT.css` — the house look, light only, with Manrope and JetBrains Mono embedded as base64.
  It needs nothing from the network and nothing from another repo. The reason behind each
  print rule is a comment beside it; keep those when editing.
- `scripts/html_to_pdf.py` — builds the print copy, renders it, and prints the verification
  report. Run it, read what it says.

## Workflow

**1. Get a page.**

| You gave it | What happens |
|---|---|
| A path to an HTML file | Converted directly. This is the normal case. |
| Markdown, notes, pasted content, a thing you were just discussing | It has to be a page first. Call the `hk-writeup` skill to build it, then convert what that wrote. `hk-writeup` owns the page and already knows the voice. |
| Nothing | Ask what to convert. |

**2. Convert it.**

```bash
python3 docs/skills/knowledge/hk-pdf-export/scripts/html_to_pdf.py <page>.html
```

Writes `<page>-print.html`, `<page>.pdf` and `<page>-page1-1.png` (`-01.png` past nine pages)
beside the source. `--out-dir` puts them somewhere else. Output belongs next to the page it
came from, which for a writeup means `writeup/` at the repo root.

**3. Read the verification report. All four parts.**

| Check | What you are looking for |
|---|---|
| `text kept` | The percentage, then every line of the diff under it. Each one has to be a transform you expected — a tab label that moved into a heading, a button that went. Anything else is content the PDF lost. Under 95% the script warns, but a 99% that dropped the wrong sentence is worse than a 96% that dropped a toolbar. |
| `screen-only wording` | Each hit is a sentence that may have stopped being true. Read it in context. Reword it or leave it deliberately. |
| `fonts embedded` | Manrope and JetBrains Mono both present. Missing means the page fell back to a system font and the PDF is not house style. The script exits 1 on this. |
| page count and page 1 | `Read` the PNG and say what page 1 shows. |

**4. Look at where the breaks landed.** Page 1 is not enough on a long document. Render the
rest beside the page and look, especially at anything wide:

```bash
pdftoppm -png -r 70 <page>.pdf <page>-pg && ls <page>-pg*.png
```

A table that runs off the right edge is the failure to watch for. On screen `overflow-x:auto`
scrolls it, on paper it is clipped and nothing tells you.

**5. Re-run with the fixes.** Add `--reword "was::now"` for each sentence, repeatable. The script
fails if a `was` string is not found, so a typo cannot silently do nothing.

**6. Report back:** where the PDF went, page count, the percentage, and anything you chose to
leave. Say plainly if you left a screen-only sentence in.

## What it needs installed

Headless Chrome does the rendering. The script takes `CHROME=/path/to/binary` from the
environment first, then looks for `google-chrome`, `google-chrome-stable`, `chromium` and
`chromium-browser` on `PATH`, then for `Google Chrome.app` or `Chromium.app` under
`/Applications` on macOS, and stops with a message naming all of that if none is there.

Install poppler too (`brew install poppler` on macOS, `sudo apt install poppler-utils` on
Debian) for `pdffonts`, `pdfinfo` and `pdftoppm`. The script runs without them and says so,
but you lose the page-1 image, which is the strongest check of the four. Install it before
the first run.

## The rules

**The source stays as it is.** The script writes `<name>-print.html` beside it and every run
regenerates the copy, so the two cannot drift by hand. A page that is interactive on screen
stays interactive on screen.

**The author's prose stays as written.** If the writing conflicts with
`docs/playbook/tone_and_taste.md`, say so once and leave it; rewriting someone's copy is their
call. The one exception is the screen-only rewording below.

**Everything interactive is unfolded.** The script does it, but know what it is doing, because
a page can hide content in a way it does not recognise:

- Every `<details>` gets `open`. A collapsed one prints as its summary line and nothing else.
- A `.tabs` picker becomes stacked sections, each tab's label promoted to a heading. Four panels
  out of five are `display:none` on screen and would print as nothing at all.
- `<nav>`, `<div class="nav">`, `<script>` and every `<button>` come out. Jump rails, theme
  toggles and Copy buttons are all screen.

**Screen-only instructions are reworded in the print copy only.** "Click any of them to see
the detail" is false on paper, where the detail is already open. So are "pick", "hover",
"scroll", "expand". The script finds them and lists them. You supply the replacement with
`--reword`, which writes it into the print copy and leaves the source alone. Those sentences
stopped being true on paper, which is why rewording them is allowed.

## When the page must keep its own look

`--keep-css` keeps the source page's styling and adds only the paper rules. Use it when the
layout carries meaning that the house sheet does not know about — a page whose class names
encode structure the reader depends on. It forces `data-theme="light"` on the root, which works
on pages that support that attribute. A page with a dark mode and no light override will print
dark, so check page 1 before trusting it.

Default is the house sheet. Reach for `--keep-css` when you have a reason.

## What it does not do

A page built by `hk-writeup`, or plain HTML, converts cleanly: `PRINT.css` styles ordinary
tags and every class the writeup shell defines (`.kicker`, `.chips`, `.nav`, `section` `h2`,
`.lede`, `.note-line`, `details.row` with `.row-name` and `.row-status`, `.dbody`, `.mono`,
`.cmd`, `.kv`, `.tbl`). A page with its own bespoke class names keeps its structure but loses
its styling unless you pass `--keep-css` or add the classes to `PRINT.css`.

It writes three files and tells you where they are. It does not commit, push, or email anything.

## Notes

A `LiberationSans` or `DejaVuSansMono` subset in `pdffonts` output is normal. Some glyph — an
arrow, an ellipsis, a Greek letter — fell outside both embedded fonts and Chrome substituted for
that character only. Both house families still being listed is what matters.

`pdffonts` reports every Manrope subset as `Manrope-ExtraLight`. That is the variable font's
default name record, not the weight that rendered. Nothing to fix.
