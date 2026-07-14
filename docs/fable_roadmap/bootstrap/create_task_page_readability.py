#!/usr/bin/env python
"""Load the task-page readability spec: the task modal tells the truth and
reads like speech.

Born from the user's read of task #356: 78 comments, a stale failure banner
suggesting the wrong action, the same system message posted 11 times, and
key:value soup where sentences should be. Rated 0/10 by the human who has
to use it. Eight findings agreed with the user; this spec fixes them.

Usage (from taskit/taskit-backend/):
    python ../../docs/fable_roadmap/bootstrap/create_task_page_readability.py [--dry-run]

PRE-DISPATCH CHECKLIST
1. `git branch spec/sp_task_page_readability main && git push -u origin spec/sp_task_page_readability` (done when this landed).
2. Agents/models confirmed against the board's active lineup.
"""
import os
import sys
from pathlib import Path

if not Path("manage.py").exists():
    sys.exit("Run from taskit/taskit-backend/ (manage.py not found in cwd)")

sys.path.insert(0, "testing_tools")
from _utils import setup_django  # noqa: E402

setup_django()

from tasks.models import Board, Spec, Task, User  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv
CREATED_BY = os.environ.get("ODIN_OPERATOR_EMAIL", "operator@harness.kit")
SPEC_ODIN_ID = "sp_task_page_readability"

WHY = """WHY (Watching 6/10 + The human's seat 6/10 — user finding, rated 0/10):
the task page is where a human decides what to do with a task, and today it
works against them. On task #356 the failure banner showed a three-day-old
failure with the wrong suggested action, the same system message appeared
eleven times, and the failure story read as key:value fields instead of
sentences. Reference: the eight agreed findings in this spec's content."""

COMMON_FOOTER = """
## Working protocol (applies to every fable task)
- Fix the cause, not the symptom.
- Failing tests before implementation (root CLAUDE.md).
- Write every user-facing string the way you would say it out loud
  (docs/fable_roadmap/TONE.md). If it sounds like a log line, rewrite it.
- Attach proof to `.proof/task-<id>/proof.md` in your worktree and post a
  summary comment. Do NOT commit `.proof/`.
- You are on your own branch in your own worktree — no branch switching,
  never git stash, nothing outside your workspace.
"""

TASKS = [
    dict(
        key="truth",
        title="The task page's failure banner always shows the latest failure and the right next step",
        agent="minimax", model="minimax-coding-plan/MiniMax-M3", depends=[],
        description=f"""{WHY}

## What is wrong today
Task #356 failed a review three times, but its red banner still said
"No agent progress for ~267727s ... requeued via the stale-execution path"
— a failure from three days earlier — and the top banner offered "Requeue
to re-dispatch the same agent", the wrong move for a review failure. The
human reads the banner first, and the banner lies.

## What to do
1. Find every place a run can fail (dispatch gate, execution reap, crash,
   review/reflection cap) and make each one write the task's
   `last_failure_type` / `last_failure_reason` / friends at the moment it
   fails. Today the reflection-cap path leaves the previous failure in
   place. The banner must always describe the failure that put the task in
   FAILED now.
2. Give each failure class its own honest one-line suggested action, in
   plain words: a review failure suggests reading the reviewer's finding,
   not re-dispatching. Keep the mapping next to the failure-policy table so
   the two can't drift.
3. Write the failure reason itself as a sentence a person can say out loud
   (TONE.md). "The agent stopped producing output for 15 minutes, so the
   run was stopped and retried" — not "Run reaped and requeued via the
   stale-execution path." Keep the machine fields (type, origin, pid) in
   metadata for the trace viewer; the sentence is what the banner shows.
4. Tests: one per failure path asserting the metadata reflects THAT
   failure and the suggested action matches its class.

## Where a human sees it
The red failure banner and sidebar FAILURE box on any failed task's modal.
Open a failed task: the banner text names the failure that actually parked
it, in plain words, with the right next step.

## Prove it
Drive one task through a review-cap failure and one through an execution
failure (mock or fixture); screenshot or curl both tasks' metadata showing
the banner fields match the latest failure. Under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="dedupe",
        title="System comments never repeat themselves and open with one readable line",
        agent="glm", model="zai-coding-plan/glm-5.2", depends=["truth"],
        description=f"""{WHY}

## What is wrong today
On task #356: the "Memory — closest finished twins" block was posted 11
times (byte-identical), the same 8KB "Effective input" dump 5 times, and
two failure bursts posted the identical set of comments twice within ten
seconds (a double-posting bug — the backend log shows the same lines
twice). 78 comments, of which maybe 12 say something new.

## What to do
1. Repeat suppression at the posting site: before posting a system comment
   (twins, effective-input, fingerprint history), check the task's existing
   comments for an identical body (or same kind + same content hash). Post
   only when the content changed — e.g. twins re-post when the assignee or
   the twin list changed, not on every attempt.
2. Find and fix the double-post: the same failure burst lands twice within
   seconds. Trace why (two writers? signal fired twice?) and fix the cause,
   not with a time-window hack.
3. The run-start comment becomes one line a human can read: "Run started ·
   glm/glm-5.2 · attempt 3 · full input attached" — with the full effective
   input attached the same way execution traces are (the machine-output
   channel the UI already hides by default). The trace stays complete;
   only the comment body gets small. Do not drop any information.
4. Tests: a task retried N times gets each system message at most once per
   real change; the run-start comment body stays under ~200 chars with the
   input reachable as an attachment.

## Where a human sees it
The comment stream on any task that retried: one twins card, one run-start
line per attempt, no identical neighbours.

## Prove it
A fixture task retried 3 times: comment list before/after, counts named.
Under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="modal",
        title="The task modal reads top to bottom: live sidebar, one history, lists that render",
        agent="glm", model="zai-coding-plan/glm-5.2", depends=[],
        description=f"""{WHY}

## What is wrong today (all on task #356's modal)
- The sidebar shows fields that say nothing: WHY reads "OVERRIDE —",
  time budget reads "Budget: — / Used: 33m 23s" without saying which
  attempt, the model dropdown truncates its own label, and the failure tag
  renders twice ("stale_execution stale_execution").
- Two competing histories on one page: COMMENTS (69) in the middle and
  ACTIVITY (20, +44 more) below it, overlapping content in different
  formats. A human cannot know which to read.
- The memory-twins card renders every list item as "1." (a warning line
  between items resets markdown numbering) and each line is a
  dot-separated metadata string that wraps badly.

## What to do
1. Sidebar honesty: a field with no value doesn't render (no "OVERRIDE —",
   no "Budget: —"); "Used" says what it counts (e.g. "this attempt"); the
   model label never truncates below readability; the failure tag renders
   once. Frontend only — the banner CONTENT is the truth task, may land
   before or after you; don't block on it.
2. One history: fold ACTIVITY into the comment stream as thin one-line
   ticks (status changes, assignments) with a filter toggle, or collapse
   ACTIVITY to a link — pick the simpler; the page must have ONE place to
   read what happened. Say which you picked and why in the proof.
3. Fix the twins-card rendering: list numbering survives interleaved
   lines; long metadata strings wrap on the separators, not mid-word.
4. Tests: component tests for empty-field suppression, single history
   presence, and the list rendering; a snapshot of the modal on a fixture
   task with 70+ comments.

## Where a human sees it
Any task modal, worst case a failed task with many retries (use #356 as
the manual check).

## Prove it
Before/after screenshots of #356's modal (sidebar + history area) under
`.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="briefs",
        title="Briefs and titles read like speech: tone pass on the templates every task is born from",
        agent="claude", model="claude-sonnet-5", depends=[],
        description=f"""{WHY}

Titles like "Breadcrumb: task liveness, reaping, and retry — with the
stale-trace bug as the worked example (W12.9)" mean nothing to a person who
doesn't live in the code. The fix is at the source: the templates and
rules that every future brief is generated from. Do NOT edit briefs of
tasks currently in flight.

## What to do
1. Tone pass on docs/task_brief_template.md: the template itself should
   demand plain-speech titles (a title is what you'd say a task does out
   loud — no system slang like "reaping", wave codes allowed only as a
   trailing tag), a WHY a stranger clicks on first read, and sections
   whose headings are questions a human asks ("What is wrong today",
   "Where a human sees it", "Prove it").
2. Same pass on the loader templates in docs/fable_roadmap/bootstrap/
   (the COMMON_FOOTER blocks and title conventions) and on
   docs/fable_roadmap/ORCHESTRATION.md where it tells operators how to
   write tasks.
3. Check data/task_presets.json prompts for the worst offenders (log-line
   language in user-facing preset names/descriptions) — fix names and
   descriptions only; leave prompt bodies for the preset-curation task
   that already exists if it's still open (check the board first, rule:
   never re-file existing work).
4. Add the one-sentence rule to TONE.md if it isn't implied already:
   "A task title is something you could say to a teammate out loud."

## Where a human sees it
Every future task card and modal title on the board; the templates in
docs/.

## Prove it
Three before/after title+brief examples rewritten through the new
template, in `.proof/task-<id>/proof.md`.
{COMMON_FOOTER}""",
    ),
]

AGENT_EMAILS = {"glm": "glm@odin.agent", "minimax": "minimax@odin.agent", "claude": "claude@odin.agent"}


def main():
    if Spec.objects.filter(odin_id=SPEC_ODIN_ID).exists():
        sys.exit(f"Spec {SPEC_ODIN_ID} already exists.")
    board = Board.objects.get(id=5)
    if DRY_RUN:
        for t in TASKS:
            print(f"[dry-run] {t['title']} → {t['agent']} deps={t['depends']}")
        return
    spec = Spec.objects.create(
        odin_id=SPEC_ODIN_ID,
        title="The task page tells the truth and reads like speech",
        source="user finding on task #356 (rated 0/10); eight agreed fixes",
        content=WHY,
        board=board, status=Spec.STATUS_PLANNING_COMPLETE,
        metadata={"fable_wave": 12, "branch": f"spec/{SPEC_ODIN_ID}"},
    )
    print(f"spec: {spec.id} ({SPEC_ODIN_ID})")
    created = {}
    for t in TASKS:
        task = Task.objects.create(
            board=board, spec=spec, title=t["title"], description=t["description"],
            status="TODO", assignee=User.objects.get(email=AGENT_EMAILS[t["agent"]]),
            created_by=CREATED_BY, model_name=t["model"],
            metadata={"fable_wave": 12, "suggested_agent": t["agent"], "selected_model": t["model"]},
        )
        task.description = task.description.replace("<id>", str(task.id))
        task.save(update_fields=["description"])
        created[t["key"]] = task
        print(f"task {task.id}: {t['title'][:70]}")
    for t in TASKS:
        if t["depends"]:
            task = created[t["key"]]
            task.depends_on = [str(created[d].id) for d in t["depends"]]
            task.save(update_fields=["depends_on"])
            print(f"  deps: {task.id} ← {t['depends']}")


if __name__ == "__main__":
    main()
