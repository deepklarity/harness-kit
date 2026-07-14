# Getting oriented

**Why.** Watch any trace and the pattern is obvious: agents spend most of
their tokens finding things out, not building. One small task burned 5
million tokens, mostly on reading. Orientation is the biggest single waste
in the kit, and everything that fixes it pays on every task forever.

**Where it stands.** Breadcrumbs (maps of how each flow works) exist with a
staleness checker, but agents don't reliably start from them and nothing
keeps the maps current.

**Ideas.**
- Warm starts. The dispatcher packs every brief automatically: the right
  map, the closest twin's notes, the matching pattern. No agent starts cold
  again.
- Ask-the-repo. A small always-on helper that answers "where is X handled?"
  in seconds. Agents ask it mid-task instead of grepping for ten minutes.
- Maps as part of done. Touch a flow, update its map, and review checks that
  you did. Maps stop rotting because keeping them fresh is part of the work.
- The profiler. Measure where tokens actually go per task: finding versus
  doing versus checking. Stop guessing which fix pays most, know it.

When an idea here becomes real work, it gets its own section below this
line (or its own file) with the design, the tasks, and the before and after
numbers. The WHY above travels into every task description.

## Warm starts + twins — measured, not assumed

**Design.** Warm starts (W6.7) inject up to three doc paths into a task's
brief when its title/description clears a 0.05 cosine-similarity floor
against `docs/breadcrumb_analysis/_INDEX.md` and `docs/patterns/*.md`; the
suggestions are logged to `task.metadata.warm_start_docs` so behavior can be
checked later. Twins (wave 5) post a "closest finished twins" comment at
dispatch, adding a warning line when a twin's most recent outcome was a
recorded mistake. Both are pure suggestion — nothing gates on them.

**Method.** Pulled every wave-6/7 task's metadata, comments, and raw
execution trace from the board; reused the token profiler's (W6.10) pure
find/do/check/other classifier against each trace's tool-call *inputs*
(never the output text, which would false-positive on a doc merely being
*mentioned* inside `_INDEX.md`) to get an honest read/not-read signal and a
finding-share number per task.

**Read rate — do agents open what's suggested?** This is the number that
decides warm starts' fate, and it's small: board-wide, only 6 of 131 tasks
have ever cleared the match floor since the feature shipped — coverage, not
read behavior, is the bottleneck. Of the 3 that have finished, 2/3 opened at
least one suggested doc via a genuine Read call; the third opened none of
its three. At the individual-doc level, 4 of 9 suggested paths (44%) were
actually read.

**Finding-share delta (read vs. not), N=3.** Tasks that read a suggested
doc: 56.5% and 65.7% finding-share (mean 61.1%, N=2). The one that ignored
its suggestions: 56.6% (N=1). The no-suggestion population for the same two
waves (N=13 finished tasks): 45.5%–70.3%, mean 59.5%. All three numbers sit
inside the spread of the no-suggestion group itself — no effect is
detectable at this N, and none should be claimed either way.

**Redo-round delta (twin warning vs. clean), N=16 finished tasks with a
twins comment.** The codebase's own redo metric (`metadata.rework_count`,
bumped only when a reflection sends a task back for code-quality rework) is
0 for all 16 tasks, warned or not — the mechanism never fired once in wave
6/7, so there is zero variance to compare. A looser proxy, at least one
execution-level crash/retry, fired for 1 of 4 twin-warned tasks (25%) and 3
of 12 clean tasks (25%) — identical rate, and both N are too small to mean
anything regardless.

**Recommendation.** Keep warm starts running as-is — cost is near-zero and
the read signal is real (verified reads, not string coincidence) — but
don't spend more effort tuning the matcher against finding-share or redo
rounds yet: the binding constraint is that only 6 of 131 tasks ever clear
the match threshold, so there isn't enough signal to judge the effect.
Re-measure once broader coverage (lower floor or richer corpus) produces a
usable N.
