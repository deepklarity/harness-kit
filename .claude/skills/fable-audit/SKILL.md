---
name: fable-audit
description: Audit the fable roadmap (docs/fable_roadmap/) — verify claimed progress with proof, write a short report, update RESUME state + BACKLOG rank. Use at wave close or whenever the user asks how roadmap development is going. Triggers on - 'fable audit', 'roadmap audit', 'how is the roadmap going', 'audit progress', or /fable-audit.
argument-hint: [--dispatch]
allowed-tools: Bash, Read, Edit, Write, Glob, Grep, Task, Artifact
---

# Fable roadmap auditor

Trust nothing; verify everything. Lean by user directive: an audit is one short
report plus two file updates — not a documentation pass.

1. **Verify claims with proof.** For each task DONE since the last audit, run its
   verify step or inspect the merged artifact. Delegate command runs and file
   checks to parallel haiku subagents; keep judgment in the main conversation.
   The board is truth (`board_overview.py` / `spec_trace.py` /
   `task_inspect.py --brief` from `taskit/taskit-backend/`); doc-vs-board
   divergence is a finding. Downgrade anything unprovable.
2. **Recompute what has data**: run the suites; count autonomy/unsticks from
   board history (hand-counted until the metrics script lands — say so).
   Report gaps as gaps.
3. **Grade against `SCORECARD.md`** — every score move names the anchor
   (0/5/10) its evidence supports; recompute the overall (plain average).
   Scores live only in the `fable_roadmap.md` table — a score anywhere
   else is a finding. Then judge the ladder against `fable_roadmap.md`
   exit criteria; check the ratchet rules there. Violations are findings.
4. **Write `audits/YYYY-MM-DD*.md`**: verdict line (on track / drifting /
   blocked + why), verified-done table with proof, demotions, metrics snapshot,
   top-3 risks with mitigations, ladder call. Readable in 30 seconds from the top.
5. **Update the living docs**: RESUME "Current state" + BACKLOG re-rank (the top
   IS the next wave draft) + the ladder Status column if a level changed.
6. **Artifact only if the user asks** — redeploy to the URL in RESUME's durable
   facts; never mint a new one.
7. **`--dispatch` only**: convert the backlog top into a wave spec under
   `bootstrap/` and load it (pre-dispatch checklist in the loader templates).

End with: verdict line, ladder level, top 3 next actions — plain prose.
