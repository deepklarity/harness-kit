#!/usr/bin/env python3
"""Build real reflection-input fixtures from this repo's merged-task history.

Task 191 could only transcribe one real capture (N=1) because its sandbox had
no TaskIt DB and no model access. This builder closes the "where do N>=5 real
inputs come from" gap without a DB: it treats each *merged* fable task as a
real, ground-truthed reflection input.

Why merged tasks are legitimate ground truth: a task only lands on the spec
branch after its reflection verdict passes and the operator merges it. The
merge commit is the operator-asserted PASS. So for each merged task we have:

  * a real assembled reflection prompt  — built here with the *production*
    `build_reflection_prompt()` fed the task's real title, merge diffstat
    (execution_output), and committed `.proof/task-<id>/proof.md` (comments),
  * an operator-asserted ground-truth verdict — PASS (it was merged).

The output is one fixture dir per task under `fixtures/inputs_real_history/`,
each with `prompt.txt` + `ground_truth.json` (fixture_category=real_capture).
`eval.py --mode live --inputs-dir <that dir>` then runs candidate models over
these prompts and scores parse% / agreement% / ERROR%.

Run from the repo root:

    PYTHONPATH=odin/src python3 odin/tests/reflection_eval/build_real_fixtures.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from odin.reflection import build_reflection_prompt

REPO_ROOT = Path(__file__).resolve().parents[3]
PROOF_DIR = REPO_ROOT / ".proof"
OUT_DIR = Path(__file__).resolve().parent / "fixtures" / "inputs_real_history"

# Merged fable tasks (title from the merge commit) → operator-asserted PASS.
# Chosen for size diversity (proof.md ranges ~4 KB … ~14 KB) so the candidate
# models are exercised across small and large review contexts.
MERGED_TASKS = [
    ("185", "Per-task proof paths: .proof/task-<id>/ (W4.1)"),
    ("186", "Auto-promotion v1.5: RECOMMEND PROMOTE auto-advances TESTING->DONE (W4.2)"),
    ("187", "Wave-planning automation: draft dispatchable briefs from BACKLOG (W4.3)"),
    ("188", "Data-driven agent routing (W4.4)"),
    ("193", "needs_human merge reconciliation (W4.9)"),
    ("194", "Failure-policy auto-requeue for transient classes (W4.10)"),
    ("195", "Frontend deps in task worktrees: honest verify gate (W4.11)"),
    ("197", "Promote gate: resolve <id> placeholders in brief artifact paths (W5.1)"),
    ("198", "Promote gate: run verify.sh via sh, survive lost exec bits (W5.2)"),
]

# Mirror production's execution-output bound so prompts stay a realistic size.
_PROOF_CHAR_LIMIT = 8000


def _merge_diffstat(task_id: str) -> str:
    """Diffstat of the task's merge commit — the shape of what shipped."""
    rev = subprocess.run(
        ["git", "log", "--all", "--grep", f"Merge task {task_id}:",
         "--format=%H", "-n", "1"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.strip()
    if not rev:
        return "(merge commit not found)"
    stat = subprocess.run(
        ["git", "show", "--stat", "--format=%s%n%n%b", rev],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    return stat[:_PROOF_CHAR_LIMIT]


def build_one(task_id: str, title: str) -> bool:
    proof_file = PROOF_DIR / f"task-{task_id}" / "proof.md"
    if not proof_file.exists():
        print(f"skip task {task_id}: no proof.md")
        return False
    proof_text = proof_file.read_text()[:_PROOF_CHAR_LIMIT]
    task_context = {
        "task_id": task_id,
        "title": title,
        "status": "DONE",
        "agent": "claude",
        "model": "(historical)",
        "description": (
            f"Fable roadmap task {title}. This task was executed on an isolated "
            f"worktree branch and merged after its reflection verdict passed; the "
            f"reviewer's job is to judge whether the committed work and proof "
            f"justify a PASS."
        ),
        "execution_output": _merge_diffstat(task_id),
        "comments": (
            f"--- CURRENT ATTEMPT ---\n"
            f"Proof of work (committed at .proof/task-{task_id}/proof.md):\n\n"
            f"{proof_text}"
        ),
        "dependencies": "No blocking dependencies.",
        "metadata_summary": f"spec=sp_fable_w4/w5 task_id={task_id} status=DONE",
    }
    prompt = build_reflection_prompt(task_context)
    out = OUT_DIR / f"task_{task_id}_merged"
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(prompt)
    (out / "ground_truth.json").write_text(json.dumps({
        "verdict": "PASS",
        "summary": (
            f"Task {task_id} ({title}) was merged after its reflection verdict "
            f"passed — operator-asserted PASS is the ground truth."
        ),
        "fixture_category": "real_capture",
        "source": f".proof/task-{task_id}/proof.md + merge diffstat",
    }, indent=2))
    print(f"built task_{task_id}_merged  prompt={len(prompt)} chars")
    return True


def main() -> int:
    n = sum(build_one(tid, title) for tid, title in MERGED_TASKS)
    print(f"\n{n} real fixtures written to {OUT_DIR}")
    return 0 if n >= 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
