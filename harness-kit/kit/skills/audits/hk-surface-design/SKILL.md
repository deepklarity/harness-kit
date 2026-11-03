---
name: hk-surface-design
description: "Design or redesign a surface a person reads or answers (a board, decision page, report, markdown file, chat summary) from the reader's questions: a six-step written brief first, then the build in its generator, then a scored check out of 10. Triggers on: 'design this page', 'redesign this board', 'does this page read well', or /hk-surface-design."
argument-hint: "<path, or what the surface is for> [design|redesign]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, mcp__chrome-devtools__*
---

# Surface design

Designs a surface where a person and an agent work together, starting from the reader's questions rather than from a layout. The method below decides what goes on the page; the rules it applies live in `docs/playbook/visual_clarity.md` (layout) and `docs/playbook/tone_and_taste.md` (wording). Read both once and cite them; do not restate them here. A running app with routes and forms is `hk-web-app-ui-validation`, and chart truth is `hk-chart-validation`. Inside the harness-kit repo those paths sit under `kit/` (`kit/docs/playbook/...`, `kit/skills/...`).

## Context

<skill_context> $ARGUMENTS </skill_context>

**Modes.** **Design** builds a new surface. **Redesign** rebuilds an existing one. In redesign mode, read the old surface only to extract three things: who reads it, the data behind it, and the questions it answers. Never start from its layout, and never patch it item by item. With no mode given, an existing path means redesign.

## Steps

### 1. Find the generator

A rendered page (a `mission-control.html`, a page in `writeup/`) is produced by something: a template, a renderer script, a skill's shell. Grep for it (`render-board.mjs`, `SHELL.html`, the template the file came from). Every change lands in the generator so that the next render keeps it; the instance is only evidence. Also find realistic data: the real board, report or file. Never design on an empty template.

Done when the generator files and a real-size data sample are named.

### 2. Write the brief

Write six short answers before any HTML. Save them as `<surface>-brief.md` beside the surface's audit or in its `writeup/` folder; for a chat summary, put them in chat. Each answer is a few lines, not an essay.

1. **The problem.** Who arrives, from where, and in what state. What they must leave knowing or having done. Write one job sentence, then the three or four questions the reader asks, in the order they ask them.
2. **The interface per question.** For each question, pick the plainest primitive that answers it: a sentence, a short table, a list, an inline accordion (`<details>`), or a copyable reply line. Detail expands in place; there are no modals or dialogs. Use tabs only for jobs that are genuinely separate.
3. **Screen real estate.** The first screen holds the answers to the first two questions and nothing else. No wide empty cards and no columns that hold one value in every row. Set a target page height for the real content size, with folds closed.
4. **Cut slop.** List what goes. Every element must answer a reader question: repeats, labels that restate what is next to them, single-value columns, decoration and boilerplate captions are cut. In redesign mode, name each thing cut from the old surface.
5. **Understood in isolation.** A cold reader gets it with no jargon and no bare ids: an id always appears with words. No other file is needed to read it. Name the clicks a reader needs to answer, and the scrolling.
6. **The system.** Typography, colour and spacing as a few tokens that serve answers 1 to 5. Status colour always comes with a word. Light and dark. Lay out for a narrow screen only when the surface will be read on a phone.

Done when all six answers are written down and each of the reader's questions has exactly one primitive.

### 3. Build from the brief

Build in the generator, in the brief's order: the first two answers first, then the rest. When the generator is a renderer, write the failing tests for the new behaviour first (what is shown, what is folded, what is cut), then change it. Render the real data.

Done when the real data renders and the renderer's tests pass.

### 4. Verify

Run the static pass:

```bash
node docs/skills/audits/hk-surface-design/scripts/check_surface.mjs <file>
```

It prints `<item> <file>:<line> <message>` for the mechanical checks: themes, viewport, colour-only status, uppercase overuse, unfilled `{{tokens}}`, freshness, and wide tables. A `13-narrow` line counts only when the brief says phone. Run it on the template too. On a template, ignore its `13-filled` and `2-fresh` lines, because a template's tokens and its missing stamp are expected.

Then open the real render in Chrome with `mcp__chrome-devtools__new_page`. Read `document.documentElement.scrollHeight` against the brief's target, and screenshot light and dark at 1440 (plus 390 when the brief says phone). DevTools writes screenshots to its tool-results folder. Copy them to `writeup/shots/hk-surface-design/`, then close the pages you opened.

Score the result with the checklist below. Done when the static pass is clean apart from the expected template lines, and the height and screenshots are in hand.

### 5. Report and close

In chat, report:

- the job sentence
- the score and the item that capped it
- the page height against its target
- the files changed
- up to ten remaining findings, worst first, each with the file and line of the generator edit

The checklist score is the score. Severity words (as `docs/skills/audits/README.md` defines them) only rank the findings. Then run the closing step in `docs/skills/audits/README.md`. The active board is the `writeup/<initiative>/` folder the work belongs to; when there is none, the severity 4 and 5 findings go in chat. Either way, add one line to `writeup/audits/ledger.csv`.

## The checklist (verify only)

Ten yes/no items, scored as round(passed / 10 × 10). A failed gate (1 or 2) caps the score at 6. The rules behind each item are in `visual_clarity.md` under the heading named.

| # | Item | Yes when | Heading |
|---|---|---|---|
| 1 | **Job** (gate) | The brief's job sentence holds, and the first screen answers the first two questions. | The rule |
| 2 | **What needs me** (gate) | First screen: each waiting item has its question, its proposed answer and the exact reply or command, ready to copy; or the page says "nothing needs you". | What needs me |
| 3 | Freshness | It says when it was produced and from what; the regenerate command is on it or in its source. | Freshness |
| 4 | Status | One vocabulary; the word is always present; an item shows the same word everywhere. | What a screen holds |
| 5 | Order | Priority orders every list, and any other sort is stated. | What a screen holds |
| 6 | No slop | No repeated text, no single-value column, no bare id, and no cell past two lines. | What is not on the screen |
| 7 | In place | Detail expands in place; closed items fold; lists filter past 15 rows and page past about 50; no modal. | What waits behind a click |
| 8 | Height | Page height is within the brief's target, with folds closed. | The rule |
| 9 | System | A few tokens for type, colour and spacing, labels in sentence case, and no decoration. | The system |
| 10 | Themes | Light and dark follow the system, contrast holds, status is never colour alone, focus is visible, and nothing is left as an unfilled token. | The system |

A markdown file or chat summary skips 7, 9 and 10 and is scored over the rest.

## Done when

The brief is saved with its six answers. The build is in the generator, with tests for any renderer change. The static pass is clean. The height and screenshots are reported. The chat report carries the job, the score and what capped it, and the ledger line is written.
