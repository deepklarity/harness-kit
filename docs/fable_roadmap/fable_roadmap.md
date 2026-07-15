# Fable roadmap

Read [`OBJECTIVE.md`](OBJECTIVE.md) first. Short version: harness-kit is a
toolkit for humans and AI to build software together and enjoy it. The kit
builds itself first because the learnings are richest here, then we point it
at standalone apps.

This page is the scoreboard, nothing more. What each category measures and
what the numbers mean is defined once, in [`SCORECARD.md`](SCORECARD.md) —
grade against that, never against feel. Skim the table, open a bucket only
when you need its WHY and its ideas. Each idea grows inside its bucket when
it becomes real work, and every task created from a bucket carries that
bucket's WHY in its description. Scores live in this table and nowhere
else; they move only with evidence, at audits.

**Overall: 5.0/10** (plain average of the eleven scored categories; the
dip from 5.2 is Autonomy joining at its honest first score, not
regression — see audits/2026-07-15-taskpage-wave12-close.md).

| Category | Score | One line |
|---|---|---|
| [Trust](buckets/trust.md) | 6/10 | Honest classes and evidence, but the merge agent spliced files silently once this period |
| Autonomy | 3/10 | Loop self-runs; requeues, escalations and merge replies were all human (hand-counted) |
| [Memory](buckets/memory.md) | 6/10 | Ledger answers repeats; recall still not in briefs |
| [Routing and cost](buckets/routing-and-cost.md) | 7/10 | One policy table, editable; unproven at scale |
| [Getting oriented](buckets/getting-oriented.md) | 6/10 | Projects carry notes; the kit itself still starts cold |
| [Watching](buckets/watching.md) | 6/10 | Failures show up in the product; one health page still missing |
| [The human's seat](buckets/humans-seat.md) | 7/10 | One board reply resumes a parked merge; planning asks on the board; parser friction remains |
| [Project powers](buckets/project-powers.md) | 5/10 | One external product shipped end to end |
| [Audits](buckets/audits.md) | 4/10 | Health checks are hand-rolled, presets sit unused |
| [Getting started](buckets/getting-started-ease.md) | 3/10 | Quickstart shipped and proven once; strangers still unproven at scale |
| [Runs anywhere](buckets/runs-anywhere.md) | 2/10 | It exists in exactly one fragile place |
| [Moonshots](buckets/moonshots.md) | unscored | Idea funnel, not a capability (see SCORECARD) |

Outside ideas (practitioner writing, trending repos) come in through
[`IDEA_BRAINSTORMING.md`](IDEA_BRAINSTORMING.md) via scout waves, and get
promoted into a bucket when they'd move its score.

## How progress is checked

At every audit, three questions with evidence:

1. Did the kit waste the human's time this week, and where? Every yes
   becomes a task.
2. Did any bucket score move, and what proves it? Amplifier tasks must show
   a before and after on real work: tokens, redo rounds, minutes, or
   seconds to understand.
3. Did we learn anything twice? If yes, memory failed, file it.

Standing habits: hand-fixes become tasks the same day, big failures get
structural fixes, every merged task carries a test and proof, numbers come
from scripts.

## Machine facts that still gate us

- One 4 GB sandbox at a time fits comfortably on this host. A Linux machine
  removes the ceiling and enables overnight work. User decision, still open.
- `odin plan` cannot run inside a Claude session, so waves are loaded by
  scripts today. Fine for us, revisit when planning becomes conversation.
