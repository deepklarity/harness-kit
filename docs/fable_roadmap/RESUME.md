# RESUME

Read [`OBJECTIVE.md`](OBJECTIVE.md) first. It says what we are building and
what your role is. Then this page, then do what [`BACKLOG.md`](BACKLOG.md)
says. [`ORCHESTRATION.md`](ORCHESTRATION.md) explains how to run tasks.
[`TONE.md`](TONE.md) explains how to write. Briefs follow
[`../task_brief_template.md`](../task_brief_template.md). Assignments follow
[`../routing_guidance.md`](../routing_guidance.md).

The plan is in [`fable_roadmap.md`](fable_roadmap.md) — a scoreboard of
buckets, each with a score, a WHY, and ideas. Audits are in `audits/`.
`archive/` is history, search it, never update it — it lives only on this
machine (gitignored, like `docs/wiki/`), so no remote carries it.

About these docs. Current state lives on this page. Work lives in the
backlog. Nothing else. No dates, no task numbers in prose. Finished work
gets deleted, git remembers it. If a doc does not match the board, fix the
doc and get back to work.

## Where things stand

The repo went open source: secrets and personal traces swept, docs
restructured for strangers, README rebuilt around the Pattern Engineering
Quickstart (docs/Quickstart.md), the scorecard defined (SCORECARD.md), and
the working branch squashed into main. The gate is green except 9 known-red
tests listed in the backlog. The next session starts with onboarding — the
user's call.

Eleven waves are done and merged, services run the
latest code. Waves 10-11 were the trust rebuild, forced by one bad night:
run outcomes now come from evidence (worktree + stream), truncated runs
resume in their worktree, one editable routing table replaced the ad-hoc
escalation ladder, failures show up in the product, the ledger answers repeat
failures, merges get a syntax gate, and odin plan asks questions before it
plans. The audit (audits/2026-07-11-wave10-11-close.md) verified every
claim in code and moved the scores.

Still true and unfixed: the merge agent can silently splice two versions
of a file (three operator surgeries in one night) — top of wave 12. agy's
exec path crashes on contact and is benched. The new machinery has run
once, not a hundred times.

Two tracks now. This board (Fable, the kit building itself) resumes from
the backlog's "Now" — proof screenshots first, then pain-point audit
presets. The second track is the first external project. Everything about
it — opener, notes, knowledge — lives in that project's own repo
(`docs/OPERATOR-OPENER.md` there), and there the planner drives without
operator intervention. Rule: another project's state never lives in this
repo, not even gitignored. This repo carries one pointer, this paragraph.

Deployment note: the user HAS a Linux box. Running the kit there as a
second instance (own ports and DB, sandboxes included) is a queued task,
not a decision — msb runs on KVM there the same way it runs on HVF here.
Never present it as waiting on the user.

## Before you start

```bash
lsof -nP -iTCP:9100 -sTCP:LISTEN | tail -1       # backend up?
pgrep -f "celery.*worker" | head -1               # executor up?
cd taskit/taskit-backend && python testing_tools/board_overview.py 5
# if down (never plain ./dev.sh from a Claude session):
sh docs/fable_roadmap/bootstrap/start_services.sh   # runs in background
```

The board is the truth. If a doc disagrees with the board, fix the doc.

## Rules

These all came from the user. Follow them.

1. Never leave the board unwatched. Try the watcher once in the foreground,
   then run it in the background, and check it prints something. Restart it
   whenever you restart anything else. It raises STALLED when a live task
   sits unchanged too long — treat that as a failure event and inspect
   immediately with a cheap subagent. A stuck task the human notices before
   you do is your failure; it has happened.
2. Play by the system's rules, never around them. Past sessions hand-merged
   in worktrees, edited briefs mid-flight, flipped database flags, and
   patched task work directly. Never again. The sanctioned channels:
   dispatch through the API, reply to parked questions as a comment (the
   flow executes it), requeue through status, file gaps as tasks. If the
   system can't do something without ad-hoc surgery, THAT is the task.
3. Fixed something by hand anyway? That is a gap — file the real fix the
   same day. A failed task never waits: look immediately, then requeue,
   hold with a plan, or ask. Fix causes, not symptoms.
4. Send every ready task at once; the executor's slots are the queue. And
   keep at least three meaningful tasks executing — never idle waiting on a
   user decision, let the question ride alongside dispatched work.
5. Write to the board through the API only, as yourself, and check the
   response code. Never post as the human.
6. Don't type the same commands twice — the second time, it becomes a
   script in `bootstrap/`.
7. Check before you build. Search the code and the finished board first; we
   once triple-filed work that was already done. And when a reviewer quotes
   a rule that does not exist, check the source and remove it.
8. Every brief answers two questions in its acceptance: where will a human
   SEE this feature, and where does any operational decision inside it
   surface as a setting? Data and decisions that aren't where humans look
   don't exist. (Pattern: `../patterns/visible_decisions.md`.)
9. Errors compound. Every error anyone sees gets a ledger entry with
   context; second occurrences get answered from history, not re-diagnosed.
10. Never keep a `spec/*` branch checked out while the executor may merge
    into it. Never edit files inside `.odin/worktrees/`. Never bring back
    GitHub CI. Before dispatch, confirm agents and models against the full
    `opencode models` output.
11. While the user is away, keep the Mission Control artifact current at
    every meaningful transition — it is how they catch up. Rewrite it,
    never append.
12. Agent routing (user directive): always push work to glm / minimax
    first; use agy whenever the task is fine for it; save claude / codex
    for firepower — the tasks where getting it wrong costs more than the
    model difference. This applies to task assignment on every board and
    to schedule templates.
13. FAILED is a queue, not a graveyard (user directive): when a task
    fails and the cause is identified and fixed, apply the fix to the
    task itself (brief, template, assignment) and REQUEUE it to prove
    the fix — in the same session, not the next run. Leave a task FAILED
    only when the work is unfixable or already superseded, and say which
    on the task.
14. Spec creation goes through odin plan (user directive): once W12.4
    lands, the operator plans specs by requesting a plan on the board and
    answering the gate's questions there — no more ORM loader scripts for
    new waves. The loaders stay only as the emergency fallback.
15. Knowledge capture runs through the board (user directive): when an
    incident closes or a flow stabilizes, dispatch a breadcrumb/audit
    task using the kit's own presets (hk-breadcrumb-creator, the audit
    presets) to a cheap agent — don't leave the understanding in the
    operator's head or a chat thread. Breadcrumbs are code: they get
    reviewed and merged like everything else. Trigger on events, not
    calendars; a flow still changing weekly is not ready for a
    breadcrumb.
16. Status updates follow TONE.md, no gloss (user directive, flagged
    twice). Lead with what is broken or waiting, in plain words. Never
    call a beat "routine" or say "nothing needed" while known problems
    are open — a busy queue is not a finished job. Every update answers:
    what's broken, what's in flight, what's waiting on the human.

## The daily loop

Send work, watch, triage failures the moment they appear, keep this page
current, run `/fable-audit` when a wave ends and let it re-grade the
scoreboard with evidence.

The queue ran empty twice while we were busy elsewhere, so it is simple
now: if the watcher shows fewer than cap+2 tasks live, add work before
anything else. An empty slot is time we never get back. New work comes from
the backlog, from today's ledgered errors, or from fresh measurement — take
the biggest win, not the top of the list. The loop stops when the user
stops it, never because the queue looks empty.

## Facts

Board `Fable Roadmap #5`. External track board `#6`. Backend :9100, UI
:9200. Loaders and watchers in `bootstrap/`. Executor runs 4 slots;
sandbox RAM is per-agent in `.odin/config.yaml` and visible in Settings.
The Mission Control artifact URL lives in `archive/OPERATOR_LINKS.md`
(local only) — rewrite the page, same URL, never append.
