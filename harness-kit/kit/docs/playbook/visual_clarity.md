# Visual clarity: what a screen shows, and what waits behind a click

This applies to every surface a person reads or answers: mission
control, a decision page, the test report, a review page, an audit
write-up, a markdown board, a chat summary. `tone_and_taste.md` says how a
sentence sounds; this says what a screen holds. When they meet, the tone
doc wins on wording and this doc wins on layout.

## The rule

A screen is a fixed amount of attention. Everything on it competes for
the same eyes, so each thing shown must earn its place against the
things it pushes down. The test: cover the page with your hand and reveal
one screen at a time. If the reader cannot act on what a screen shows
without scrolling for the rest, the screen is holding the wrong things.

Every surface has one job, sayable in one sentence ("the owner sees
what is blocked on them and answers it"). A surface that cannot name
its job is two surfaces, or none.

## What needs me

The first screen says what waits on the reader, before any evidence.
Each item carries the question, the proposed answer, what waiting
costs, and the exact way to answer: the reply to type (with a copy
button on a page), the command to run, the field to change. "Say yes"
with no place to say it is not an answer path. Nothing waiting is said
too, in one line ("nothing needs you").

## Freshness

Every surface says when it was produced and from what ("rendered
14:02 from board.csv"), and how to regenerate it. A number whose age is
unknown reads as current; a stale page that looks fresh is worse than
no page.

## What a screen holds

- **The answer, then its evidence.** The first screen carries the
  verdict sentence and one table of status words with a sentence each.
  A reader who stops there has the truth.
- **Tables, not paragraphs, for anything that repeats.** Three things of
  the same shape are a table with short cells. A paragraph per item was
  how mission control's grades became eight blocks nobody compared.
- **One row per thing, one status word per row.** A row holds what it
  is, one status word, and one sentence that says when it is closed.
  Nothing else. The story behind a row is one click away.
- **A short cell.** A cell holds a name, a number, a status word or one
  sentence. A cell that wraps to three lines is a paragraph hiding in a
  table; move it behind the row's detail.
- **Numbers live once, in the instrument that measured them.** A page
  that tracks work links to the report; it does not copy the report's
  counts, because copied numbers are stale numbers.
- **One status vocabulary per page**, and the word is always present.
  Colour marks the word; it never replaces it.
- **Priority orders every table.** P0 rows first. A reader who reads
  only the first row still read the right one. If the sort is anything
  else (ready before blocked), the caption says the real rule.
- **A number has a comparison**: a target, the previous value or the
  total it is part of ("7 of 23 closed", not "7"). A bare number cannot
  be judged good or bad.
- **No bare ids.** An id always sits next to words ("T03 Say where the
  logs are"), so a cold reader needs no other file.
- **Labels in sentence case.** Uppercase or letter-spaced mono is for
  a few labels of one or two words; a page where every header, pill and
  button shouts has no emphasis left.

## What waits behind a click

- **Detail expands in place.** Notes, options, evidence, the commit that
  changed a contract: the row is a `<details>` whose summary is the
  row and whose body is the detail. The list stays short and the
  detail stays whole; nothing is rewritten into a summary to make it
  fit. No modals or dialogs: they hide the page the reader was reading
  and cannot be compared side by side. Tabs only for separate jobs.
- **Closed items fold.** Done tasks, fixed bugs, decided decisions, past
  runs: one `<details>` line with the count ("13 closed"), collapsed by
  default. Their proof is still there for whoever opens it.
- **Long lists filter and page.** Above about fifteen rows a list gets a
  filter box; above about fifty, the rest sit behind a "show N more"
  fold.
- **Deep material links out.** A design, a strategy, a per-file coverage
  page, a log: the page carries one link, never a second copy.

## Affordances

- Anything the page offers can be done from it: a control that exists,
  looks like a control (primary solid, secondary outlined, tertiary a
  link) and has a visible focus ring. The row's title opens its detail,
  not a far-right button.
- Destructive actions confirm or undo.
- A view worth sending has a link or command that reproduces it:
  filters and open panels in the URL, file paths as relative links.

## The system

Pick these once (the writeup shell already does) and use nothing else:

- **Type**: one text face and one mono face; a scale of about five
  sizes; body line at most about 75 characters; `tabular-nums` on
  number columns.
- **Spacing**: 4, 8, 12, 16, 24, 32, 48; more space between groups
  than inside them.
- **Colour**: tokens on `:root` (background, surface, text, muted
  text, border, accent, and good / warn / bad); tinted neutrals, no
  pure black; status colours only on status.
- **Both themes**: the tokens are redefined under
  `@media (prefers-color-scheme: dark)` guarded by
  `:root:not([data-theme="light"])`, and again under
  `:root[data-theme="dark"]`; never a hardcoded `data-theme="light"`.
  Check both renders.
- **Accessibility**: contrast 4.5:1 for body text, 3:1 for large;
  status is a word, never colour alone; honour
  `prefers-reduced-motion`; a viewport meta on every page.
- **Phones, when the surface is read on one**: reflow at 390 px with
  no sideways scroll, wide tables as stacked rows below 640 px, touch
  targets of 44 px. A desktop-only surface skips this.

## Markdown and chat

The same rules, in plain text. The first line is the answer; the
second says what needs the reader and how to reply. Repeating things
are a table of short cells, at most five columns so it survives a
terminal. Status is a word. Paths are written whole so they can be
clicked or copied. No headings for a reply under a screen; no bold
except the one thing to act on.

## What is not on the screen at all

- A section that restates another section in different words. Mission
  control once had next steps, a roadmap and a bug table that were the
  same rows three times; the fix was one table with a kind column, not
  better wording.
- A comparison or snapshot that will be false next week ("against
  oss", "as of commit X"). It goes in the log with its date.
- A count with no sentence, a badge with no word, a meter with no
  number. Decoration that replaces the sentence is cut.
- **Decoration** that does not carry reading: shadows, gradients,
  icons standing in for words, a card around every block, a tile row
  that repeats the table under it. A card or tile earns its place only
  when it groups one thing and its sentence sits inside it (a decision
  with its question and reply); `tone_and_taste.md` defers to this
  line on layout.

## Shape of a report page (test report, audit, dashboard)

In reading order, one screen each where possible:

1. Provenance and verdict: when, against what, one sentence that says
   the situation including the bad part.
2. What is broken, worst first: a table of name, one-line message,
   whether it also failed last time, with the rerun command and the full
   message behind the row's detail.
3. The instruments: per-suite counts, durations and coverage as one
   table; each number links to the thing that produced it (the JUnit
   file, the per-file coverage HTML).
4. Deltas and history: newly failing, newly fixed, then the last runs
   folded.
5. Inventory: every test, folded, filterable.

## Shape of a tracking page (mission control)

`mission-control.md` in this folder carries the screens: what needs
you, the work, where we are, the instruments. Its board rules are
the worked example of this doc.

## Checking a page against this

Open it at laptop width. For each screen ask: what is the one thing this
screen is for, is every row a short row, is every long thing behind a
click, does any number here also live somewhere else. Fix in that order.
The `hk-surface-design` skill designs and redesigns a surface from
the reader's questions and verifies it against these rules.
The `scripts/guides/AUDIT_RUBRIC.md` lines for page length, long lists
and plain words are the same checks scored.
