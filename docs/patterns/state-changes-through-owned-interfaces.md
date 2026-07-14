tags: lifecycle, orm, worktree, self-hosting, state-mutation

# State Changes Go Through Owned Interfaces

Mutate state only through the interface that owns it: task lifecycle via the API (never raw ORM writes), agent worktrees never by hand, product defects via board tasks (never operator hand-patches). Reading out-of-band is fine; writing out-of-band silently skips the side-effects, invariants, and measurements the owning interface exists to provide.

## Why it matters

Every out-of-band write in the 2026-07-03→05 sessions either broke something downstream or corrupted what the system was trying to measure: ORM status flips skipped merge dispatch (F14), a hand-renamed file in a live worktree made the reflection correctly fail the task and burn a retry, and hand-committed product fixes defeated the self-hosting experiment itself — "*if you do changes after a task is done then what is even the point of it*". The rules now codified in OPERATIONS.md (ORM read-only, worktree mutation ban, operator non-intervention) all exist because this pattern was violated first.

## The signal

You have direct access to the substrate (Django shell, the worktree on disk, the repo checkout) and the owning interface feels slower — one more dispatch, one more API call, one more board task. The convenience of the direct write is exactly the cost being deferred onto the system's invariants.

## Instances

- **2026-07-03 / ORM lifecycle writes (F14 + 7 more)**: Repeated raw `t.status = …` / `TaskHistory.objects.create(…)` mutations bypassed the lifecycle API; a `REVIEW→TESTING` flip left `merge_status=pending` with no merge ever dispatched. Rule since: ORM for seeding and diagnostics only; all transitions via API.
- **2026-07-03 / live worktree mutated mid-review**: Renamed `IGNITION.log→.md` (to dodge `.gitignore`), deleted a skill file, and manually merged the spec branch inside task 96's live worktree. Reflection #72 failed the task — evidence integrity worked *against the operator*, at the cost of a wasted retry cycle. Rule since: never mutate worktrees; convention fixes go through description + retry.
- **2026-07-05 / self-hosting boundary violated twice**: The agy CLI-flag defect was hand-fixed and committed, and a qwen test cleanup was done by subagent — both instead of board tasks. Both reverted/refiled as tasks #110/#111 after the user's correction; the operator non-intervention rule (commit e6cf4e9) now draws the line: harness layer yes, agent-produced work never.
- **2026-07-03 / out-of-scope write**: While answering "why are there 4 running tasks", an unrelated stale task on a *different* board was moved to BACKLOG via ORM, unasked. Scope of the question ≠ license to mutate whatever it uncovers.
