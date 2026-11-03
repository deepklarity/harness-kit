# RESUME — {{initiative}} (machine-facing entry point)

Read order: this file → `board.csv` (task and decision state, the only place it
is written) → `log.md` (one line per row worked, oldest first). `mission-control.html` is
rendered from the board for people; a session re-renders it and does not read
it. Every state change lands in `board.csv` and `node docs/harnesskit/templates/mission-control/render-board.mjs writeup/<initiative>` runs
in the same turn.

## Before you write anything the owner reads

Before writing a page or a message the owner reads (not on every session
start), reread `docs/playbook/tone_and_taste.md` and check your last
paragraph against it before sending. Write the way you would say it to
the person next to you.

## What this initiative is

{{A few sentences on what this initiative is for and what done looks like,
the way you'd explain it to someone who just walked in.}}

## ⏭️ Next action

{{The one thing to do next, plainly, and why it doesn't need a decision
first. When the work waits on a person, say what waits and where.}}

## Settled decisions (do not re-open)

{{The calls that are made and why, one line each, so nobody re-argues them.}}

## Mechanics

- Scores per readiness area live only in the Readiness table inside
  `mission-control.html`'s "Where are we?" card; read them there, never copy them here.
- `board.csv` columns: `id, kind, title, status, priority, depends_on, closes_when,
  how_proven, notes, started_at, done_at, owner_kind, owner_action, owner_reply`.
  The last six may be empty or absent.
- Statuses: `open`, `doing`, `blocked`, `owner` (waits on the owner), `human-review` (the
  agent finished and attached proof; only the owner closes it), `done`, `fixed`,
  `decided`, `dropped`; for suggestions `noted`, `taken`, and for learnings `open`, `folded`.
  A row at `owner` or `human-review` shows under "What needs you", never under "The work";
  `owner_kind` is `decide`, `review`, `click` or `run`, `owner_action` what the owner does,
  `owner_reply` the words that close it. An open decision row waits on the owner too.
- A suggestion is something a session noticed inside one of the workspace's
  repos that the scaffold must not change — a stale test, a hidden skip
  list, a bad install recipe, a tracked secret, a dead doc. One plain
  sentence saying what and where (the file path in `notes`), never a patch.
- A learning is a row for something this deployment taught harness-kit.
  What it is and how to write one is covered in `docs/harnesskit/entry.md`,
  hard rule 6.
- `log.md` gets one line per row worked, written in the turn the row moves:
  what the row was, what it cost (model, and tokens or turns), and what it
  found. It is not optional and it is not a summary at the end. The board
  carries the state; the log is the only place the reasons behind each
  score and each decision survive, and without it a later session can say
  what changed but not why.
- Render: `node docs/harnesskit/templates/mission-control/render-board.mjs writeup/<initiative>`, every time `board.csv` changes. The renderer is the installed one, not a copy here, so `harnesskit init` upgrades reach this board on its next render.
