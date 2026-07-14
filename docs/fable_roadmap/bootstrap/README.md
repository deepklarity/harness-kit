# Bootstrap scripts

Scripts the human uses to run the roadmap board. Wave loaders, watchers, and
service startup. Waves used to be loaded by hand-written scripts; odin plan
replaced them, and the spent loaders moved to `../archive/bootstrap/`.
`create_wave9.py` stays here as the emergency-fallback shape.
The goal is to replace that with `odin plan`, see the planning gap in
`fable_roadmap.md`. Task descriptions are self-contained, agents need nothing
but the board.

## Ignition procedure

Run from the repo root unless noted. Steps 1–3 are safe to re-run (idempotent).

```bash
# 1. Migrate the taskit DB and seed agent users (DB is currently behind on migrations)
cd taskit/taskit-backend
python manage.py migrate
python manage.py seedmodels          # seeds/refreshes agent users + model lineup

# 2. Create the Fable Roadmap board, wave spec, and tasks
python ../../docs/fable_roadmap/bootstrap/create_wave9.py        # emergency fallback only; add --dry-run to preview

# 3. Verify the board state
python testing_tools/board_overview.py
```

```bash
# 4. Start services (backend :9100, frontend :9200, celery) — separate terminal
./dev.sh
# For autonomous execution, taskit/taskit-backend/.env needs:
#   ODIN_EXECUTION_STRATEGY=celery_dag   (and Redis running)
# Tasks then execute when moved to IN_PROGRESS — or enable the board's
# auto_start_planned_tasks toggle to dispatch planned tasks automatically.
```

```bash
# 5. Watch the loop run
odin logs -f                          # follow all running tasks (from repo root)
# UI: http://localhost:9200 → Fable Roadmap board
```

Manual single-task dispatch (alternative to celery): `odin exec <task_id>` from the
repo root — fine for non-claude agents from any terminal; claude-assigned tasks must
be dispatched from a real terminal or via celery (nested-session limitation).

## Wave protocol

- **Commit before dispatch.** Task worktrees branch off the spec branch (created from
  main at first exec). Wave content — briefs, tracker, this directory — must be
  committed to the spec branch (or main) *before* dispatching tasks, or agents won't
  see the files their descriptions reference. (Learned live: finding F4 in
  `../archive/OPERATIONS.md`; wave 1's docs were committed onto `spec/sp_fable_w1` as
  `9d9b3d6`.)

- Wave files live here (`wave1_m1.md`, …); each has a creation script or is loaded by
  the auditor with `--dispatch` (see `../AUDIT.md` step 9).
- Task 0 of wave 1 is an **ignition smoke test** — a trivially small task whose only
  purpose is proving the loop end-to-end (execute → proof comment → reflection) at
  near-zero cost before real work dispatches.
- After a wave completes: run `/fable-audit`. The audit's next-actions list becomes
  the next wave. Humans reassign agents freely before dispatch (suggestive, not
  prescriptive).
- Agents work the *stable* clone's board but merge via worktrees/branches; the
  dual-instance discipline (`docs/solutions/dual-instance-setup.md`) applies as soon
  as changes touch the orchestrating instance itself — see initiative 14.2.
- **Advisor trial (routing-and-cost, task 243).** For fix-class task briefs
  assigned to `glm` or `minimax`, add this line so the executor knows the
  consult-when-stuck escalation is available: *"Advisor trial: enabled — if
  genuinely stuck (same test fails twice, or a stated architecture
  uncertainty), write one question to `.odin/advice_request.md` and keep
  working; capped at 2 consults/run."* Requires `advisor.enabled: true` (and
  `trial_agents` including the assignee) in board config — see
  `odin/src/odin/advisor.py`. Off by default elsewhere.

## Current wave

**Wave 1 — M1 safety net** (`wave1_m1.md`): ignition smoke test + CI pipeline +
lint/pre-commit + single-source DAG + TEST_PLAN subprocess gaps + loop registry.
