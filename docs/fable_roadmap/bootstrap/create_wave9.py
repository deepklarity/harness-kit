#!/usr/bin/env python
"""Load wave 9: the user's six picks — Ease + Moonshots tracks.

Usage (from taskit/taskit-backend/):
    python ../../docs/fable_roadmap/bootstrap/create_wave9.py [--dry-run]

PRE-DISPATCH CHECKLIST
1. `git branch spec/sp_fable_w9 <work-branch> && git push -u origin spec/sp_fable_w9`.
2. Agents/models confirmed against full `opencode models`.
3. Wave 8 may still be draining — specs run side by side; that is fine.
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
SPEC_ODIN_ID = "sp_fable_w9"

EASE_WHY = """## What is wrong today
Only the people who built this kit can switch it on. A stranger who clones
the repo hits a wall before they reach a working board. The doctor and the
quickstart exist; this wave carries a first-timer the rest of the way."""

MOON_WHY = """## What is wrong today
The kit works end to end, but the screens a human watches are a floor, not
a finished place to work. A person still has to leave the page to answer
the things waiting on them, and the kit's way of working isn't portable to
any other repo yet."""

# This script already ran (its spec exists — see the sys.exit guard in
# main()), so it stays here as the emergency-fallback shape for a future
# wave script. The TITLES and the opening "What is wrong today" lines
# follow docs/task_brief_template.md: plain sentences a person would say
# out loud, no bucket labels or scores (Ease 3/10, Moonshot, Trust …), and
# the wave code only as a trailing tag. A copy inherits that convention.
# The Scope and Acceptance headings below are the old wave-9 names; when
# you write a new wave, use the template's section headings (What to do,
# Where a human sees it, Done means, Prove it) instead. The canonical
# Standing rules block lives there — paste it, don't rewrite it.
COMMON_FOOTER = """
## Standing rules
- Fix the cause, not the symptom. If your fix only covers this one case,
  look one level up.
- Write the failing test before the fix or feature.
- Put your evidence in .proof/task-<id>/proof.md inside your workspace and
  post a short comment pointing at it. Don't commit the .proof folder —
  the system uploads it to the task for you. Only capture a test run's
  output after the run has finished.
- You are already on your own branch in your own workspace. Don't create
  or switch branches, and don't touch anything outside your workspace.
- Never use git stash.
"""

TASKS = [
    dict(
        key="install",
        title="Install the kit from clone to a green doctor in one command (W9.1)",
        agent="glm", model="zai-coding-plan/glm-5.2", depends=[],
        description=f"""{EASE_WHY}

## Scope
1. `install.sh` at repo root: idempotent, POSIX-ish, mac+linux. It does what
   a careful human would: check prerequisites honestly (python3.11+, node,
   git; name exact install commands per OS when missing), create venv,
   install backend deps + odin editable, npm install, migrate, seed agents
   — reusing dev.sh's provisioning blocks (extract shared steps rather than
   duplicating; dev.sh becomes a caller or they share a lib).
2. Ends by running `odin doctor` and printing the stranger's next step
   (QUICKSTART.md path).
3. Explicitly NO provider setup — doctor names what's missing per provider;
   one provider is enough to proceed.
4. Test: a scripted run in a scratch clone on this host (fresh venv path)
   reaching green-enough doctor; assert idempotency by running twice.

## Acceptance
- Fresh scratch clone: `sh install.sh` → doctor output → quickstart pointer,
  twice, no errors. README gains the one-liner at the top. Suites green.

## Verify (attach as proof)
- Both install transcripts under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="codex-only",
        title="Run the quickstart with a single provider and fix what breaks (W9.2)",
        agent="minimax", model="minimax-coding-plan/MiniMax-M3", depends=[],
        description=f"""{EASE_WHY}

252's audit finds provider assumptions statically; this finds them
empirically, as a first-timer would.

## Scope
1. Simulate codex-only: a controlled environment (PATH without opencode/
   claude/agy binaries, or config listing only codex) on this host. Run
   doctor, then the QUICKSTART sample spec end to end.
2. Every failure or confusing message becomes either (a) a fix in this
   task if small, or (b) a precise filed finding in the proof (file:line,
   what a stranger sees, what they should see). Fix the top blockers;
   honesty about the rest.
3. Repeat the run after fixes: the sample spec must land with only codex.
   (If codex quota blocks a live run, use the cheapest available single
   provider and SAY SO — the point is single-provider, not codex per se.)
4. Tests for each fixed site.

## Acceptance
- A single-provider environment completes doctor → sample spec → merged,
  with clear messages at every step. Findings table in proof. Suites green.

## Verify (attach as proof)
- Before/after transcripts under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="new-project",
        title="Point the kit at an outside repo in one command (W9.3)",
        agent="claude", model="claude-sonnet-5", depends=[],
        description=f"""{EASE_WHY}

This is also the first time the kit meets a repo it did not build itself.
That one command is the handshake.

## Scope
1. `odin new-project <path-or-url>` (or `hk new-project` if a wrapper name
   fits existing CLI conventions — check first): clones/uses the target
   repo, creates a board + first spec via the API, writes the project-local
   config the executor needs (working dir, spec-branch convention), runs
   doctor against that project, and prints exactly what to do next.
2. Safe by default: never touches the target's main branch; everything on
   spec/task branches per our convention. Reversibility rules apply.
3. Works with the sample spec as the first task, so new-project → one
   merged change is a ten-minute demo on ANY repo.
4. Tests: scaffold against a scratch git repo fixture; config correctness;
   idempotent re-run refuses politely.

## Acceptance
- `new-project` against a scratch repo yields a board with a dispatchable
  sample task and a green doctor; the printed next steps are copy-paste
  runnable. Suites green.

## Verify (attach as proof)
- Full transcript against the scratch repo under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="inbox",
        title="Build the inbox where everything waiting on you gets answered in place (W9.4)",
        agent="claude", model="claude-sonnet-5", depends=[],
        description=f"""{MOON_WHY}

The single feature that changes daily operation: one panel where everything
waiting on a human lives, answerable without leaving the page.

## Scope
1. /factory gains an Inbox region (URL-addressable): parked merges with
   their explain-both-sides question and a reply box that posts the comment
   (the reply-resume flow does the rest — it already works); the TESTING
   shelf with one-click flip to DONE (the human's housekeeping); reversibility
   parks; open ErrorEvents needing disposition (buttons for fixed/non-issue).
2. Each inbox item shows the one-line WHY and links to full evidence
   (new tab). Empty inbox states say so honestly.
3. Reuse existing APIs (comments POST, task PATCH, errors disposition);
   add at most one thin aggregation endpoint for \"everything waiting\" —
   the same query the changelog TL;DR needs (coordinate with 251s work,
   merged by now — reuse its builder).
4. Tests: each item type renders + acts; a reply round-trips in a mocked
   flow; empty states.

## Acceptance
- A parked conflict answered FROM THE PAGE merges without any API curl; a
  shelf task flips to DONE from the page. Ten-second legibility holds.
  Suites green.

## Verify (attach as proof)
- Screenshots (populated inbox, empty inbox) + a reply round-trip under
  `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="rework-conversation",
        title="Turn one sentence on a shelved task into a follow-up the kit sends out (W9.5)",
        agent="claude", model="claude-sonnet-5", depends=["inbox"],
        description=f"""{MOON_WHY}

Point at any merged/shelved task, say what is wrong in one sentence; the
kit builds the follow-up — brief with the buckets WHY, twins, quote —
and dispatches it. The human never writes a brief.

## Scope
1. Backend: POST /tasks/<id>/rework/ with {{\"instruction\": \"...\"}} —
   composes a follow-up task: title from the instruction, description
   assembled from (a) the parent tasks context (title, WHY section parsed
   from its description, proof pointers), (b) the instruction verbatim as
   the requirement, (c) the standard working protocol footer. Twins +
   quote fire automatically at dispatch (existing machinery). Routing
   picks the agent (existing suggester); metadata links parent.
2. NO model call to write the brief — pure assembly. If the instruction is
   under 10 chars or ambiguous by simple rules, 400 with a helpful message
   (this is a fast path, not a chat).
3. Frontend: a Rework box on the task modal (shelf tasks) and on inbox
   shelf items (coordinate with W9.4 — it merges before you; extend, do
   not duplicate).
4. Tests: composition correctness, parent linkage, guard rails, UI test.

## Acceptance
- One sentence typed on a real shelved task produces a dispatched task
  whose brief a fresh agent can execute; parent linkage visible. Suites
  green.

## Verify (attach as proof)
- The composed brief + screenshots under `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
    dict(
        key="preset-parity",
        title="Curate the presets and export them as portable skills (W9.6)",
        agent="claude", model="claude-sonnet-5", depends=[],
        description=f"""## What is wrong today
The board presets and the hk- skills are the same know-how, drifted apart:
about 27 presets and 20 skills that overlap but don't line up. A person has
to learn the kit's way of working twice. Curate once, export everywhere,
and that way of working travels to any repo the kit touches.

## Scope
1. CURATE the preset library (data/task_presets.json) against
   docs/fable_roadmap/TONE.md and each presets actual intent: rewrite
   flabby prompts in the plain voice, merge near-duplicates, DELETE dead
   ones (list every deletion with a one-line reason in proof — deletions
   are decisions, not losses; git remembers). Presets should carry the
   same structure our fable briefs do: WHY, scope, acceptance, proof.
2. PARITY MAP: a table (in proof and as a docs/patterns/ entry) of preset
   <-> skill coverage: which presets deserve skill twins, which skills
   deserve preset twins, which are doorway-specific by nature (say why).
3. EXPORTER: a generator (script or odin subcommand) that renders each
   curated preset as an hk-prefixed SKILL.md (frontmatter description from
   the presets intent, body from its prompt) into a target repos
   .claude/skills/ — so `odin new-project` (W9.3, may merge before you —
   coordinate) or a flag installs the kits practice into any project.
   ONE canonical source: presets JSON generates skills; never hand-edit
   generated skills (generated marker in frontmatter).
4. Round-trip guard: a test that every generated skill parses (frontmatter
   + body) and that regeneration is idempotent.

## Acceptance
- Curated preset set loads via /api/presets/ (count + deletions named);
  parity table exists; the exporter generates valid hk- skills into a
  scratch dir; regeneration idempotent. Suites green.

## Verify (attach as proof)
- Deletion/merge table + parity map + generated skill samples under
  `.proof/task-<id>/`.
{COMMON_FOOTER}""",
    ),
]

AGENT_EMAILS = {"glm": "glm@odin.agent", "minimax": "minimax@odin.agent", "claude": "claude@odin.agent"}


def main():
    if Spec.objects.filter(odin_id=SPEC_ODIN_ID).exists():
        sys.exit(f"Spec {SPEC_ODIN_ID} already exists.")
    board = Board.objects.get(id=5)
    if DRY_RUN:
        for t in TASKS: print(f"[dry-run] {t['title']} → {t['agent']} deps={t['depends']}")
        return
    spec = Spec.objects.create(
        odin_id=SPEC_ODIN_ID,
        title="Fable roadmap — wave 9 (Ease + Moonshots: the user's six)",
        source="docs/fable_roadmap/BACKLOG.md",
        content=(Path(__file__).resolve().parents[3] / "docs/fable_roadmap/BACKLOG.md").read_text(),
        board=board, status=Spec.STATUS_PLANNING_COMPLETE,
        metadata={"fable_wave": 9, "milestone": "L2", "branch": f"spec/{SPEC_ODIN_ID}"},
    )
    print(f"spec: {spec.id} ({SPEC_ODIN_ID})")
    created = {}
    for t in TASKS:
        task = Task.objects.create(
            board=board, spec=spec, title=t["title"], description=t["description"],
            status="TODO", assignee=User.objects.get(email=AGENT_EMAILS[t["agent"]]),
            created_by=CREATED_BY, model_name=t["model"],
            metadata={"fable_wave": 9, "suggested_agent": t["agent"], "selected_model": t["model"]},
        )
        task.description = task.description.replace("<id>", str(task.id))
        task.save(update_fields=["description"])
        created[t["key"]] = task
        print(f"task {task.id}: {t['title'][:60]}")
    for t in TASKS:
        if t["depends"]:
            task = created[t["key"]]
            task.depends_on = [str(created[d].id) for d in t["depends"]]
            task.save(update_fields=["depends_on"])
            print(f"  deps: {task.id} ← {t['depends']}")


if __name__ == "__main__":
    main()
