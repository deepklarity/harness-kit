---
name: hk-shift-changelog
description: Create and maintain a shift-handover HTML changelog artifact — a page the returning human reads in two minutes to know everything that happened while they were away. Generic across projects. Use when working autonomously for an away user, when asked for a changelog/progress page/catch-up artifact, or at every meaningful transition once one exists. Triggers on - 'changelog artifact', 'progress page', 'what happened while I was away', 'keep me posted in html', or /hk-shift-changelog.
---

# Shift-handover changelog (project-agnostic)

One living HTML page, rewritten (never appended) at every meaningful
transition, redeployed to the SAME artifact URL every time. The reader is
a teammate back from sleep or a weekend: they want the state of the world,
not a log.

## Where it lives

- File: one stable path in the repo (e.g. `docs/<area>/changelog.html`),
  committed, so any future session can find and redeploy it.
- URL: record the artifact URL in the project's durable operator notes
  (state/RESUME file) the first time you publish. Every later publish
  passes that `url` — never mint a second artifact for the same duty.
- Favicon: pick one emoji the first time; never change it (users find the
  tab by icon).

## Structure (top-down by how fast the reader wants out)

1. **Header** — project + "shift handover", a stamp saying what period it
   covers (relative, not raw timestamps).
2. **TL;DR box** — 3-5 sentences: what changed, what's running, whether
   anything is blocked on the reader. If they read nothing else, this
   suffices.
3. **What happened, in order** — timeline entries: a short label (wave
   close / incident / directive / decision), a bold headline, 1-3
   sentences, and a status pill (DONE / RUNNING / VERIFIED / WAITING).
   Every user directive gets its own entry so they can verify they were
   understood.
4. **Scoreboard / metrics** — whatever the project tracks (bucket scores,
   suites, costs), as a table with movement + evidence, never bare deltas.
5. **Waiting on you** — a numbered list of decisions only the human can
   make. This section earns the page its keep; keep it brutally current.
6. **Footer** — "living document, rewritten each transition" + where the
   ground truth lives.

## Design rules

Load the `artifact-design` skill before first authoring. Beyond it:
- Utilitarian treatment: real hierarchy, quiet type, one accent; both
  themes via CSS tokens (`prefers-color-scheme` + `data-theme` overrides).
- Bind background AND text color to the SAME token set on `html, body`
  with `!important`. The artifact shell may force its own body background;
  if your ink tokens go dark while its ground stays light, the page is
  unreadable (seen live). Background and ink must move together or not at
  all.
- Self-contained (CSP): no external fonts/scripts; system font stacks.
- Tables wrapped in `overflow-x:auto`; digits get `tabular-nums`.
- Plain language throughout; no ids or jargon the reader must decode; name
  things by what they mean, cite ids only as secondary `mono` detail.

## Cadence

Rewrite + redeploy at: phase/wave boundaries, incidents and their fixes,
user directives received while away, score/metric movements, and anything
that adds to "Waiting on you". Batch small transitions; never let the page
drift more than ~30 min behind a meaningful one. Keep the file and the
deployed artifact in lockstep — commit the file in the same breath.
