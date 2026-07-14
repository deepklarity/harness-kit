"""Replay script: regenerate task 157's merge-conflict report against
the new merge-agent code and confirm the report is self-contained.

Scenario (from task 157's needs_human comment history):
- Spec branch and task branch both modified the same source file.
- Each task added its own complementary section; the two sides did
  NOT overlap.  An operator previously had to open the file to learn
  the conflict was complementary; the new report should tell them
  that inline.

This script builds a synthetic in-progress merge state (worktree
directory with conflict markers) on disk, invokes the merge-agent
helpers exactly as ``resolve_conflicts_in_worktree`` does, and prints
the resulting ``question_text``.  The text is compared against the
acceptance gate: a non-author can decide from the report in under a
minute without opening files.

Usage:
    python3 odin/scripts/replay_task157_conflict_report.py
"""

import argparse
import sys
import tempfile
import textwrap
from pathlib import Path

# Make sure ``import odin.*`` resolves from this worktree regardless of
# where the script is invoked.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "odin" / "src"))

from odin.merge_agent import (  # noqa: E402
    classify_resolution,
    extract_conflict_hunk_text,
    format_merge_question,
    resolve_conflicts_in_worktree,
    summarize_side,
    _split_hunk_sides,
)
from unittest.mock import MagicMock  # noqa: E402


# ----------------------------------------------------------------------------
# Replay scenario — task 157 added a complementary section to a file that
# task 155 also touched on a different line.  This is the canonical
# "two tasks added different things to the same file" conflict.
# ----------------------------------------------------------------------------

SPEC_FILE_PRE_CONFLICT = textwrap.dedent(
    """\
    # Existing baseline content shared by both branches
    def existing_helper():
        return "shared"

    def main():
        return existing_helper()
    """
)

# What task 155 added (now on the spec branch).
SPEC_SIDE_ADDITION = textwrap.dedent(
    """\

    def task_155_audit():
        \"\"\"Added by task 155: compliance audit hook.\"\"\"
        return audit_log("task_155")
    """
)

# What task 157 added (on its own branch).  Different function, different
# docstring, different line — complementary to the spec side.
TASK_SIDE_ADDITION = textwrap.dedent(
    """\

    def task_157_metrics():
        \"\"\"Added by task 157: runtime metrics collector.\"\"\"
        return collect_metrics("task_157")
    """
)


def _build_conflict_file(worktree: Path, file_name: str) -> str:
    """Build a conflict-marker file inside ``worktree`` matching the
    spec-vs-task scenario above.  Returns the file's path relative
    to the worktree so the caller can pass it to the helpers.
    """
    spec_text = SPEC_FILE_PRE_CONFLICT + SPEC_SIDE_ADDITION
    task_text = SPEC_FILE_PRE_CONFLICT + TASK_SIDE_ADDITION

    # Git produces 3-way conflict markers; this is what the worktree
    # would look like during a failed merge.
    conflicted = (
        SPEC_FILE_PRE_CONFLICT
        + "<<<<<<< HEAD\n"
        + SPEC_SIDE_ADDITION
        + "=======\n"
        + TASK_SIDE_ADDITION
        + ">>>>>>> task/spec/157\n"
    )
    target = worktree / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(conflicted)
    return file_name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Optional path to write the generated report to disk.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="odin-replay-157-") as tmp:
        worktree = Path(tmp)
        relative = _build_conflict_file(worktree, "src/runtime.py")

        # --- Helper-level replay (the same steps resolve_conflicts_in_worktree
        # takes when it sees the conflict, BEFORE the merge is aborted).
        hunk = extract_conflict_hunk_text(worktree, relative)
        spec_text, task_text = _split_hunk_sides(hunk)
        verdict, why = classify_resolution(spec_text, task_text)

        print("=" * 70)
        print("REPLAY: task 157 conflict (complementary changes vs spec side)")
        print("=" * 70)
        print()
        print(f"Conflicting file: {relative}")
        print()
        print("Per-side summary (what each side changed):")
        print(f"  - {summarize_side('spec', spec_text)}")
        print(f"  - {summarize_side('task', task_text)}")
        print()
        print(f"Resolution heuristic: {verdict} ({why})")
        print()
        print("--- Full question text (what the operator sees on the board) ---")
        print()

        # End-to-end replay: invoke the merge agent exactly as
        # worktree.py would, then read the pre-built question_text off
        # the returned MergeResolution.
        wt = MagicMock()
        resolution = resolve_conflicts_in_worktree(
            wt, worktree, [relative],
            task_brief="Add runtime metrics collector",
        )
        report = resolution.question_text or ""

        print(report)
        print()
        print("=" * 70)

        # --- Acceptance gate: self-contained report.
        # A non-author should be able to decide without opening the
        # worktree.  This is the "under a minute" gate.
        gate_passed = all(
            [
                relative in report,                       # file path
                "spec side" in report.lower(),            # spec side described
                "task side" in report.lower(),            # task side described
                "conflict region" in report.lower(),      # hunk head present
                "<<<<<<<" in report,                      # raw markers shown
                "suggested resolution" in report.lower(),  # verdict inline
                verdict in report.lower(),                # verdict text matches
            ]
        )

        print()
        print("Acceptance gate (self-contained report):")
        checks = {
            "file path listed": relative in report,
            "spec side described": "spec side" in report.lower(),
            "task side described": "task side" in report.lower(),
            "conflict hunk head present": "conflict region" in report.lower(),
            "raw conflict markers visible": "<<<<<<<" in report,
            "suggested resolution inline": "suggested resolution" in report.lower(),
            "verdict matches heuristic": verdict in report.lower(),
        }
        for label, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            args.save.write_text(report)
            print(f"\nReport saved to: {args.save}")

        return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())