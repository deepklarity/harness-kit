---
name: hk-pdf-export
description: Turn an HTML page, or content that has to become one first, into a PDF in the house design. Never restyles the original, unfolds whatever was collapsed or tabbed behind a click, and checks that nothing was lost. Triggers on - 'make a PDF of this', 'export this to PDF', 'can I print this', or /hk-pdf-export.
argument-hint: "<path to an HTML page, or the content to turn into one>"
allowed-tools: Bash, Read, Write, Edit, Glob, Skill
---

# PDF export

Takes a page and produces a PDF a person can hand to someone. The house look comes from one
stylesheet, the conversion from one script, and neither is optional — a PDF that lost half its
content looks exactly like one that did not, so the checking is the job.

Two files in this skill's base directory:

- `PRINT.css` — the house look, light only, with Manrope and JetBrains Mono embedded as base64.
  Self-contained on purpose. It needs nothing from the network and nothing from another repo.
- `scripts/html_to_pdf.py` — builds the print copy, renders it, and prints the verification
  report. Run it, read what it says.

## What it needs installed

Headless Chrome does the rendering. The script looks for `google-chrome`,
`google-chrome-stable`, `chromium` and `chromium-browser` on `PATH`, and stops with a clear
message if none is there.

Install `poppler-utils` too — `sudo apt install poppler-utils` gives you `pdffonts`, `pdfinfo`
and `pdftoppm`. The script runs without them and says so, but you lose the page-1 image, which
is the strongest check of the four. Treat it as a real install step, not an optional one.

## What goes in

| You gave it | What happens |
|---|---|
| A path to an HTML file | Converted directly. This is the normal case. |
| Markdown, notes, pasted content, a thing you were just discussing | It has to be a page first. Call the `hk-writeup` skill to build it, then convert what that wrote. Do not hand-write an HTML page here — `hk-writeup` is that job and it already knows the voice. |
| Nothing | Ask what to convert. Do not guess. |

## The rules

**Never restyle the original in place.** The script writes `<name>-print.html` beside the source
and leaves the source alone. Two files, one of them generated every run, so they cannot drift by
hand. A page that is interactive on screen stays interactive on screen.

**Never rewrite the author's prose to fit a tone.** If the writing conflicts with
`docs/fable_roadmap/TONE.md`, say so once and leave it. Rewriting someone's copy is their call, not this skill's. The one
exception is below and it is narrow.

**Unfold everything interactive.** This is where a PDF silently loses content and still looks
finished. The script does it, but know what it is doing, because a page can hide content in a way
it does not recognise:

- Every `<details>` gets `open`. A collapsed one prints as its summary line and nothing else.
- A `.tabs` picker becomes stacked sections, each tab's label promoted to a heading. Four panels
  out of five are `display:none` on screen and would print as nothing at all.
- `<nav>`, `<script>` and every `<button>` come out. Jump rails, theme toggles and Copy buttons
  are all screen.

**Reword screen-only instructions, in the print copy only.** "Click any of them to see the
detail" is false on paper, where the detail is already open. So are "pick", "hover", "scroll",
"expand". The script finds them and lists them. You supply the replacement with `--reword`, which
writes it into the print copy and never touches the source. This is the narrow exception to the
rule above: those sentences are not a matter of taste, they became untrue.

## Workflow

**1. Get a page.** An HTML file, or `hk-writeup` builds one from what you were given.

**2. Convert it.**

```bash
python3 .claude/skills/hk-pdf-export/scripts/html_to_pdf.py <page>.html
```

Writes `<page>-print.html`, `<page>.pdf` and `<page>-page1-01.png` beside the source.
`--out-dir` puts them somewhere else. Output belongs next to the page it came from, which for a
writeup means `writeup/` at the repo root.

**3. Read the verification report. All four parts.**

| Check | What you are looking for |
|---|---|
| `text kept` | The percentage, then every line of the diff under it. Each one has to be a transform you expected — a tab label that moved into a heading, a button that went. Anything else is content the PDF lost. Under 95% the script warns, but a 99% that dropped the wrong sentence is worse than a 96% that dropped a toolbar. |
| `screen-only wording` | Each hit is a sentence that may have stopped being true. Read it in context. Reword it or leave it deliberately. |
| `fonts embedded` | Manrope and JetBrains Mono both present. Missing means the page fell back to a system font and the PDF is not house style. The script exits 1 on this. |
| page count and page 1 | `Read` the PNG. Actually look at it. |

**4. Look at where the breaks landed.** Page 1 is not enough on a long document. Render the rest
and look, especially at anything wide:

```bash
pdftoppm -png -r 70 <page>.pdf /tmp/pg && ls /tmp/pg*.png
```

A table that runs off the right edge is the failure to watch for. On screen `overflow-x:auto`
scrolls it, on paper it is clipped and nothing tells you.

**5. Re-run with the fixes.** Add `--reword "was::now"` for each sentence, repeatable. The script
fails if a `was` string is not found, so a typo cannot silently do nothing.

**6. Report back:** where the PDF went, page count, the percentage, and anything you chose to
leave. Say plainly if you left a screen-only sentence in.

## When the page must keep its own look

`--keep-css` keeps the source page's styling and adds only the paper rules. Use it when the
layout carries meaning that the house sheet does not know about — a data-driven template like
`.claude/skills/hk-pr-review/TEMPLATE.html`, for instance. It forces `data-theme="light"` on the root, which works
on pages that support that attribute. A page with a dark mode and no light override will print
dark, so check page 1 before trusting it.

Default is the house sheet. Reach for `--keep-css` when you have a reason.

## The CSS rules, and why

Each of these came from a real page that got them wrong. If you edit `PRINT.css`, keep them.

- **`tr{break-inside:avoid}`, never `table`.** Holding a whole table together pushes a long one
  to the next page and buys a half-empty page before it. Rows are the right granularity. Same for
  long prose boxes: small fixed blocks get `avoid`, long things are allowed to split.
- **Mono table cells get `overflow-wrap:anywhere`.** A 41-character code holds its column open,
  pushes the last column off the page, and print clips it instead of scrolling.
- **Short code columns get `white-space:nowrap`.** The other half of the same problem, and the
  non-obvious one. A hyphen is a normal wrap opportunity, so once a column is tight Chrome's auto
  table layout splits `LH-001` after the dash and prints a code that does not exist.
- **`*{print-color-adjust:exact}`** or Chrome drops every background and the chips, code blocks
  and coloured state all come out white.
- **`@page{margin:14mm}`, light only, no `prefers-color-scheme` block.** A dark page prints as a
  dark PDF.
- **Collapse breakpoints are `@media screen`.** The printable width is 710px at Letter with 14mm
  margins, 688px at A4, and 665px at Letter with 20mm. All of those sit at or under a typical
  mobile breakpoint, so without `screen` a two-column grid quietly becomes one column on paper
  and reads as a deliberate layout rather than a bug. Do not talk yourself into a bare
  breakpoint because it clears one paper size — changing the margin or the paper moves the
  number, and this is the failure you cannot see by reading the CSS.

## What it does not do

Not a layout engine for arbitrary pages. A page built by `hk-writeup`, or plain HTML, converts
cleanly because the house sheet styles ordinary tags. A page with its own bespoke class names
keeps its structure but loses its styling unless you pass `--keep-css` or add the classes to
`PRINT.css`.

It does not commit, push, or email anything. It writes three files and tells you where they are.

## Notes

A `LiberationSans` or `DejaVuSansMono` subset in `pdffonts` output is normal. Some glyph — an
arrow, an ellipsis, a Greek letter — fell outside both embedded fonts and Chrome substituted for
that character only. Harmless. Both house families still being listed is what matters.

`pdffonts` reports every Manrope subset as `Manrope-ExtraLight`. That is the variable font's
default name record, not the weight that rendered. Nothing to fix.
