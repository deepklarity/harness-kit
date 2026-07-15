# Audit — task-page spec + wave 12 close

**Verdict: on track, with one trust wound reopened.** Nine tasks shipped in
one shift and every claim below is verified in code or by a live run. But
the merge agent silently destroyed sibling work once tonight (caught by the
verify gate, fixed by hand), which is the exact failure wave 12's top item
exists to kill. Overall score moves 5.2 → 5.0 — not because the kit got
worse, but because Autonomy joins the average with an honest first score.

## Verified done (proof named)

| Task | What shipped | Proof |
|---|---|---|
| 345 | Planning is a board conversation (W12.4) | board_planner.py on branch; live round trip in .proof/task-345; the run found and fixed a real wrong-spec linking bug |
| 353 | Memory-share holders visible, stale banners cleared (W12.6) | factory memory_shares block + holder stamps in dag_executor; screenshots in .proof/task-353 |
| 356 | Task liveness/retry breadcrumb | docs/breadcrumb_analysis/task-liveness-retry/ FLOW row says "depends on dispatch path" (verified against both code paths) |
| 359 | Failure banner shows the latest failure + right next step | review_cap class seen live on task 363's failure; failureTags dedupe on branch |
| 360 | System comments post once per real change | comment_dedup.py + suppression at the execution-result endpoint; tests pass |
| 361 | Modal: one history, no empty sidebar fields, lists render | merged first try; component tests on branch |
| 362 | Brief templates read like speech | task_brief_template.md headings are plain questions; loaders aligned |
| 363 | Rework prompts lead with the reviewer's finding | orchestrator.py "Fix this first — the reviewer's finding"; before/after prompts in .proof/task-363 |
| 364 | Saves retry on a locked database | retry_on_locked on task save, comment create, settings write; lock-simulation tests pass |

Wave 12 (spec 110) is merged to the working branch and services run it.
The W12.7 fix is proven live: after the restart, every requeue ran without
the manual log-file rotation that every requeue before it needed.

## Suites (scripts/verify.sh, this checkout)

- odin: 2040 passed
- backend: 1722 passed after the merge repair below
- frontend: 300 passed + 4 known-red SettingsView tests (pre-existing,
  on the record in BACKLOG)

## Findings

1. **The merge agent destroyed sibling work, live.** Task 360's guided
   merge conflicted with task 364's fresh changes in views.py; the agent
   resolves per-file with one uniform action, so the operator's "task side"
   reply took 360's whole file and silently wiped 364's 122 lines. Caught
   only by the verify gate; repaired by hand the same hour (commit on the
   branch). Second live case tonight: the reply parser rejected three
   reasonable answers including its own example phrase ("keep-both" — the
   parser matches "keep both" with a space). Both are the top wave-12 item
   (merge agent v2); the backlog now carries the evidence.
2. **History flattening without re-cutting branches cost a 91-conflict
   merge.** The v0.1 release squashed main while spec branches stayed open.
   Rule encoded in RESUME: a mainline squash isn't done until every open
   spec branch is re-cut from the new tip.
3. **Reviews pass code and fail proofs.** Five of nine tasks burned at
   least one extra round on proof discipline (claims their attachments
   didn't show). The template fix (362) landed late in the shift; watch
   whether proof rounds drop next wave. If not, the proof-shape rule
   belongs in the reviewer prompt too.
4. **Doc-vs-skill divergence (minor):** the audit skill references a
   ladder/exit-criteria section in fable_roadmap.md that no longer exists.
   Skill text should follow the doc.

## Scores (anchors named; table in fable_roadmap.md updated)

- **Trust 7 → 6.** The 5-anchor is "at least one class of silent failure
  remains" — tonight that class fired twice (silent file splice, parser
  rejecting its own example). 6 credits that everything else told the
  truth: honest failure classes, evidence-based outcomes, a gate that
  caught both regressions.
- **Autonomy — first score: 3.** Hand-counted (metrics script still
  missing): zero of nine tasks landed with no operator touch; the
  execute→review→merge loop self-ran, but requeues, escalations, brief
  notes, and merge replies were all human. The 5-anchor ("routine tasks
  land hands-free") is not yet true — though post-fix requeues now run
  clean, which is the path to it.
- **The human's seat 6 → 7.** One reply resumed two parked merges from the
  board; planning now asks its questions on the board (W12.4). Held from
  higher by the reply parser friction.
- All other categories hold: Memory 6, Routing 7, Getting oriented 6,
  Watching 6, Project powers 5, Audits 4 (this audit was asked for, not
  scheduled), Getting started 3, Runs anywhere 2 (the sleep pain proved the
  single-laptop cost again tonight).

**Overall: 5.0** (eleven categories, plain average; the dip from 5.2 is
Autonomy joining at 3, not regression).

## Top 3 risks

1. **Merge agent v2 keeps slipping.** Every week it waits, another silent
   splice is possible; tonight's was caught by luck-shaped diligence (the
   gate). Mitigation: it is the backlog's #1; next wave opens with it.
2. **Proof-round churn eats agent budget.** ~10 extra review rounds this
   shift. Mitigation: 362's template just landed; measure next wave, then
   push the rule into the reviewer prompt if rounds don't drop.
3. **One laptop.** Sleep still kills momentum (the fix removes the killing,
   not the freezing). Mitigation: the Linux-box second instance is a queued
   setup task — schedule it into the next wave.

## Next actions

1. Load the next wave with merge-agent v2 at the top (evidence attached in
   BACKLOG), agy's exec-path fix second.
2. Measure proof-round counts on the first post-template wave.
3. Second instance on the Linux box as a wave task.
