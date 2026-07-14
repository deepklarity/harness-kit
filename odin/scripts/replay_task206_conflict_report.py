"""Replay script: regenerate the real wave-4 merge conflict between task
188 and task 192 against the new merge-agent report format (task 206).

Scenario (real history in this repo, not synthetic):
- Merge base ``9e36e7ca`` is the wave-4 kickoff commit both task branches
  fork from.
- ``task/sp_fable_w4/188`` (data-driven agent routing) merges first —
  this mirrors what actually happened: task 188 landed on the spec
  branch before task 192 attempted its own merge.
- ``task/sp_fable_w4/192`` (CANCELED task status) is based on the same
  merge base and rewrites the same ``.proof/proof.md`` path with its
  own, unrelated proof document.  Before the ``.proof/task-<id>/``
  convention existed, every task's proof collided at this one shared
  path — the class of bug the fable BACKLOG records as "task 177
  deleted task 181's proof".  Merging 192 in after 188 reproduces that
  exact conflict on real content.

This script builds both merges in a throwaway git worktree (removed on
exit — nothing in the real repo is touched), captures the conflict
exactly as ``resolve_conflicts_in_worktree`` does before the merge is
aborted, and prints the resulting report.

Acceptance gate (task 206): a non-author can decide from the report
from their chair — both sides' WHY (grounded in their real commit
messages), why they collide, a reasoned proposed resolution, and a
concrete question — with raw hunks collapsed to an appendix, not the
headline.

Usage:
    python3 odin/scripts/replay_task206_conflict_report.py [--save PATH]
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "odin" / "src"))

from odin.merge_agent import resolve_conflicts_in_worktree  # noqa: E402
from odin.worktree import WorktreeManager  # noqa: E402

MERGE_BASE = "9e36e7cad9f3c3c983dc085c53feeefe9ada405b"
SPEC_SIDE_BRANCH = "task/sp_fable_w4/188"
TASK_SIDE_BRANCH = "task/sp_fable_w4/192"
CONFLICT_PATH = ".proof/proof.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--save", type=Path, default=None,
        help="Optional path to write the generated report to disk.",
    )
    args = parser.parse_args()

    wt = WorktreeManager(ROOT)
    tmp = Path(tempfile.mkdtemp(prefix="odin-replay-206-"))
    worktree = tmp / "wt"

    try:
        add = wt._git("worktree", "add", "--detach", str(worktree), MERGE_BASE)
        if add.returncode != 0:
            print(f"worktree add failed: {add.stderr}", file=sys.stderr)
            return 1

        # Task 188 lands first — this is what actually happened historically.
        merge_188 = wt._git(
            "merge", "--no-ff", SPEC_SIDE_BRANCH, "-m", "spec: merge task 188",
            cwd=worktree,
        )
        if merge_188.returncode != 0:
            print(f"pre-req merge of 188 failed (fixture drifted): {merge_188.stderr}", file=sys.stderr)
            return 1

        # Task 192's merge is the one that collides on .proof/proof.md.
        merge_192 = wt._git(
            "merge", "--no-ff", TASK_SIDE_BRANCH, "-m", "spec: merge task 192",
            cwd=worktree,
        )
        if merge_192.returncode == 0:
            print("No conflict reproduced — history may have changed upstream.", file=sys.stderr)
            return 1

        status = wt._git("status", "--porcelain", cwd=worktree)
        conflicting = wt._parse_conflicting_files(status.stdout.splitlines())
        if CONFLICT_PATH not in conflicting:
            print(f"Expected conflict on {CONFLICT_PATH}, got: {conflicting}", file=sys.stderr)
            return 1

        resolution = resolve_conflicts_in_worktree(
            wt, worktree, conflicting,
            task_brief="192: add CANCELED as terminal-neutral Task status",
        )
        report = resolution.question_text or ""

        print("=" * 70)
        print("REPLAY: real wave-4 conflict — task 188 vs task 192 on .proof/proof.md")
        print("=" * 70)
        print()
        print(report)
        print()
        print("=" * 70)

        report_lower = report.lower()
        gate_checks = {
            "file path listed": CONFLICT_PATH in report,
            "spec side why present": "spec side" in report_lower,
            "task side why present": "task side" in report_lower,
            "collision reason present": "why they collide" in report_lower,
            "proposed resolution present": "proposed resolution" in report_lower,
            "concrete question present": "question" in report_lower and "?" in report,
            "raw hunk collapsed after the why block": (
                report_lower.index("why they collide") < report.index("<<<<<<<")
                if "<<<<<<<" in report else True
            ),
        }
        print("Acceptance gate:")
        for label, ok in gate_checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            args.save.write_text(report)
            print(f"\nReport saved to: {args.save}")

        wt._git("merge", "--abort", cwd=worktree)
        return 0 if all(gate_checks.values()) else 1
    finally:
        if worktree.exists():
            wt._git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
