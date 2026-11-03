# RESUME — {{initiative}} (machine-facing entry point)

Read order: this file → `board.csv` (task and decision state, the only place it
is written) → `log.md` (one line per row worked, oldest first). `mission-control.html` is
rendered from the board for people; a session re-renders it and does not read
it. Every state change lands in `board.csv` and `python3 render_board.py` runs
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

- `board.csv` columns: `id, kind, title, status, priority, depends_on, closes_when, notes`.
- Statuses: `open`, `blocked`, `done`, `fixed`, `worked around`, `decided`, for suggestions
  `noted`, `taken`, `dropped`, and for learnings `open`, `folded`, `dropped`.
- A suggestion is something a session noticed inside one of the workspace's
  repos that the scaffold must not change — a stale test, a hidden skip
  list, a bad install recipe, a tracked secret, a dead doc. One plain
  sentence saying what and where (the file path in `notes`), never a patch.
- A learning is a row for something this deployment taught harness-kit.
  What it is and how to write one is covered in `initiate-scaffold.md`,
  hard rule 6.
- `log.md` gets one line per row worked, written in the turn the row moves:
  what the row was, what it cost (model, and tokens or turns), and what it
  found. It is not optional and it is not a summary at the end. The board
  carries the state; the log is the only place the reasons behind each
  score and each decision survive, and without it a later session can say
  what changed but not why.
- Render: `python3 render_board.py` from this folder, every time `board.csv` changes.
