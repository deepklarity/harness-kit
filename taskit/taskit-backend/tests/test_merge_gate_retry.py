"""Tests for the post-merge safety gate retry/park protocol (task 338).

The merge gate catches two breakage classes that historically rode a
merge commit into the spec branch:

  1. Literal ``<<<<<<<``/``=======``/``>>>>>>>`` conflict markers
  2. Python syntax errors (the keep-both unclosed-dict failure mode)

When the gate refuses a merge, the task goes back to the merge agent
automatically — once. The gate is deterministic so a third attempt
without intervention will fail the same way. After two strikes, the
merge parks permanently for a human with the diff.

Acceptance criterion:

  - First gate refusal → re-run merge with the violation details as
    guidance (the "task goes back to the merge agent with the exact
    file and line")
  - Second gate refusal → park with merge_status = "needs_human" +
    merge_gate_parked = True, and the QUESTION comment carries the
    diff so a human can decide what to do
  - Clean merge → no retry, advances REVIEW → TESTING normally
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from odin.worktree import MergeResult

from tasks.dag_executor import merge_task_on_reflection
from tasks.models import (
    CommentType,
    MergeAttempt,
    MergeTrigger,
    Task,
    TaskComment,
    TaskStatus,
)
from tests.base import APITestCase


def _gate_refusal_result(path="tasks/views.py", line_no=42):
    """Build a MergeResult that the post-merge safety gate refused."""
    return MergeResult(
        success=False,
        conflict=True,
        needs_human=True,
        error=(
            "Post-merge safety gate refused: 1 violation(s) "
            "in resolved files"
        ),
        diff_stat=f" {path} | 2 +-",
        conflicting_files=[path],
        gate_violations=[(path, "conflict_marker", line_no)],
    )


class _GateRetryCase(APITestCase):
    """Shared fixtures: board, spec, REVIEW task with branch."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board,
            odin_id="sp_gate_retry",
            metadata={"branch": "spec/sp_gate_retry"},
        )
        self.task = self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_gate_retry/1"},
        )


class GateRetryFirstAttemptTests(_GateRetryCase):
    """First gate refusal: task goes back to the merge agent automatically."""

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_first_refusal_reruns_merge_with_guidance(self, mock_branch, _mock_advance):
        """First refusal: second _merge_task_branch call carries the violation
        details as resolution_guidance so the merge agent has a chance to
        pick a different resolution on its second attempt."""
        first = _gate_refusal_result()
        second = MergeResult(success=True, diff_stat=" 1 file changed")
        mock_branch.side_effect = [first, second]

        merge_task_on_reflection.__wrapped__(self.task.id)

        self.assertEqual(mock_branch.call_count, 2)
        # First call: no guidance.
        first_kwargs = mock_branch.call_args_list[0].kwargs
        self.assertEqual(first_kwargs.get("resolution_guidance", ""), "")
        # Second call: guidance lists the violation (file + line + error type).
        second_kwargs = mock_branch.call_args_list[1].kwargs
        guidance = second_kwargs.get("resolution_guidance", "")
        self.assertIn("tasks/views.py", guidance)
        # error_type "conflict_marker" surfaces as "conflict marker" in the
        # human-readable guidance — the operator and the merge agent both
        # read the same string.
        self.assertIn("conflict marker", guidance)
        self.assertIn("line 42", guidance)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_first_refusal_does_not_immediately_park(self, mock_branch, mock_advance):
        """The first gate refusal does NOT immediately park — it triggers a
        retry. The bug fixed in this task was that the first refusal set
        merge_status = "needs_human" which is terminal and stops the
        watchdog from re-dispatching. Now the retry happens inline (one
        merge call), and if the retry succeeds the task advances to
        TESTING normally."""
        first = _gate_refusal_result()
        second = MergeResult(success=True, diff_stat=" 1 file changed")
        mock_branch.side_effect = [first, second]

        merge_task_on_reflection.__wrapped__(self.task.id)

        # Two merge calls: first refused, retry succeeded.
        self.assertEqual(mock_branch.call_count, 2)
        # The retry succeeded → status is merged, not parked.
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "merged")
        self.assertEqual(self.task.metadata.get("merge_gate_attempts"), 1)
        self.assertNotEqual(
            self.task.metadata.get("merge_status"), "needs_human",
        )
        mock_advance.assert_called_once_with(self.task)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_first_refusal_succeeding_on_retry_advances_to_testing(self, mock_branch, mock_advance):
        """Retry succeeds: merge_status = merged, task advances to TESTING."""
        first = _gate_refusal_result()
        second = MergeResult(success=True, diff_stat=" 1 file changed")
        mock_branch.side_effect = [first, second]

        merge_task_on_reflection.__wrapped__(self.task.id)

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "merged")
        self.assertEqual(self.task.metadata.get("merge_gate_attempts"), 1)
        mock_advance.assert_called_once_with(self.task)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_first_refusal_posts_status_update_about_retry(self, mock_branch, _mock_advance):
        """Operator sees a STATUS_UPDATE naming the gate refusal so the retry
        isn't silent."""
        mock_branch.return_value = _gate_refusal_result()

        merge_task_on_reflection.__wrapped__(self.task.id)

        status_update = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.STATUS_UPDATE,
            author_email="merge-agent@odin",
        ).first()
        self.assertIsNotNone(status_update)
        self.assertIn("gate", status_update.content.lower())
        self.assertIn("attempt 1", status_update.content.lower())


class GateRetrySecondAttemptTests(_GateRetryCase):
    """Second gate refusal: park permanently with the diff."""

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_second_refusal_parks_permanently(self, mock_branch, mock_advance):
        """Both attempts refused → merge_status = needs_human + parked = True,
        task does NOT advance to TESTING."""
        mock_branch.side_effect = [
            _gate_refusal_result(),
            _gate_refusal_result(),
        ]

        merge_task_on_reflection.__wrapped__(self.task.id)

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        self.assertTrue(self.task.metadata.get("merge_gate_parked"))
        self.assertEqual(self.task.metadata.get("merge_gate_attempts"), 2)
        mock_advance.assert_not_called()

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_second_refusal_question_includes_diff(self, mock_branch, _mock_advance):
        """The park comment includes the diff so a human can decide without
        checking out the worktree."""
        first = _gate_refusal_result()
        second = MergeResult(
            success=False,
            conflict=True,
            needs_human=True,
            error="Post-merge safety gate refused: 1 violation(s)",
            diff_stat=" tasks/views.py | 4 ++--",
            conflicting_files=["tasks/views.py"],
            gate_violations=[("tasks/views.py", "syntax_error", 2486)],
        )
        mock_branch.side_effect = [first, second]

        merge_task_on_reflection.__wrapped__(self.task.id)

        question = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.QUESTION,
        ).first()
        self.assertIsNotNone(question)
        # The diff stat must appear inside a code fence.
        self.assertIn("tasks/views.py | 4 ++--", question.content)
        # And the parked / 2-of-2 messaging must be present.
        self.assertIn("parking permanently", question.content.lower())

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_second_refusal_records_two_merge_attempts(self, mock_branch, _mock_advance):
        """Each gate refusal produces its own MergeAttempt row so the cost
        ledger and spec_trace see both attempts."""
        mock_branch.side_effect = [
            _gate_refusal_result(),
            _gate_refusal_result(),
        ]

        merge_task_on_reflection.__wrapped__(self.task.id)

        attempts = list(MergeAttempt.objects.filter(task=self.task).order_by("id"))
        self.assertEqual(len(attempts), 2)


class GateRetryCleanMergeTests(_GateRetryCase):
    """Clean merges skip the retry path entirely."""

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_clean_merge_no_retry(self, mock_branch, mock_advance):
        """A merge that returns success on the first attempt does NOT trigger
        any retry — only ONE merge call lands."""
        mock_branch.return_value = MergeResult(success=True, diff_stat=" ok")

        merge_task_on_reflection.__wrapped__(self.task.id)

        self.assertEqual(mock_branch.call_count, 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "merged")
        self.assertNotIn("merge_gate_attempts", self.task.metadata)
        mock_advance.assert_called_once_with(self.task)


class GateRetryNonGateConflictTests(_GateRetryCase):
    """A regular conflict (not a gate refusal) does NOT trigger retry."""

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_regular_conflict_does_not_retry(self, mock_branch, mock_advance):
        """A needs_human conflict WITHOUT gate_violations is the legacy
        ambiguous-conflict path — it parks immediately, no retry loop."""
        mock_branch.return_value = MergeResult(
            success=False,
            conflict=True,
            needs_human=True,
            conflicting_files=["x.py"],
            ambiguous_files=["x.py"],
            error="Merge conflict (needs human): ambiguous",
        )

        merge_task_on_reflection.__wrapped__(self.task.id)

        self.assertEqual(mock_branch.call_count, 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        self.assertNotIn("merge_gate_attempts", self.task.metadata)
        mock_advance.assert_not_called()