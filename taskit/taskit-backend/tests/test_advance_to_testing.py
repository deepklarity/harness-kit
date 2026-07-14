"""Tests for the post-reflection REVIEW → TESTING advance (W5 — task #205).

Tags: [mock] — uses Django SQLite + mocked merge.

The promote-check gate and the auto-promote celery task were retired (every
W4 hold they produced was a phantom gap that parked green work on a human).
The contract now is the simplest one that does not manufacture work:

  - When reflection passes and the post-reflection merge lands cleanly, the
    task advances REVIEW → TESTING and STAYS there.  No gate runs, no
    celery task dispatches, no status comment is posted by the advance.
  - TESTING means "merged, as good as done".  DONE is the human's optional
    housekeeping flip after a manual spot-check — the system never sets it.

What was removed (and what these tests pin against re-introduction):

  - ``tasks.auto_promote`` (the celery body that ran ``odin promote-check``
    and flipped TESTING → DONE on PROMOTE).
  - ``odin.promote_check`` + the ``odin promote-check`` CLI command.

The fixture pattern follows ``test_auto_advance_reflection.py`` and
``test_dag_executor_worktree.py``.
"""

import importlib.util
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tasks.models import CommentType, TaskComment, TaskHistory, TaskStatus
from tests.base import APITestCase


def _module_present(name: str) -> bool:
    """True iff *name* is importable as a real module (not a namespace)."""
    spec = importlib.util.find_spec(name)
    # A retired module has no spec at all.  A namespace package would have a
    # spec whose origin/submodule_search_locations are set but loader is
    # None — guard against that by requiring a concrete loader.
    return spec is not None and spec.loader is not None


class AdvanceToTestingTests(APITestCase):
    """``_advance_task_to_testing`` moves REVIEW → TESTING and records a
    history row under the system identity.  It does NOT dispatch any
    promotion gate, post a status comment, or touch DONE."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board, odin_id="sp_fable_w5",
            metadata={"branch": "spec/sp_fable_w5"},
        )

    def _review_task(self):
        return self.make_task(
            self.board, spec=self.spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_fable_w5/205"},
        )

    def test_advance_moves_review_to_testing(self):
        """REVIEW → TESTING; history row names the system, never DONE."""
        from tasks.dag_executor import _advance_task_to_testing

        task = self._review_task()
        _advance_task_to_testing(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)

        history = TaskHistory.objects.filter(
            task=task, field_name="status",
        ).order_by("-changed_at").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.REVIEW)
        self.assertEqual(history.new_value, TaskStatus.TESTING)
        self.assertEqual(history.changed_by, "system@taskit")

    def test_advance_noop_when_not_review(self):
        """A task that already left REVIEW is left alone (idempotency)."""
        from tasks.dag_executor import _advance_task_to_testing

        task = self.make_task(
            self.board, spec=self.spec, status=TaskStatus.IN_PROGRESS,
            metadata={"branch": "task/sp_fable_w5/205"},
        )

        _advance_task_to_testing(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_advance_posts_no_status_comment(self):
        """The advance itself is silent — no comment lands on the task.

        The merge path posts its own 'Merged …' comment; the advance must
        not add a second one.  (Pre-retirement this was also true — pinned
        here so a future 'auto-promote posted' comment cannot sneak back.)
        """
        from tasks.dag_executor import _advance_task_to_testing

        task = self._review_task()
        _advance_task_to_testing(task)

        self.assertFalse(
            TaskComment.objects.filter(task=task).exists(),
            "advance must not post any comment",
        )


class PromoteGateRetiredTests(APITestCase):
    """Structural guard: the gate machinery has been removed, not merely
    disabled.  If either module comes back, these tests fail — which is
    the point.  Re-introducing auto-promotion is a design reversal that
    should be a visible decision, not a silent import."""

    def test_auto_promote_module_removed(self):
        self.assertFalse(
            _module_present("tasks.auto_promote"),
            "tasks.auto_promote must stay retired (W5 task #205)",
        )

    def test_promote_check_module_removed(self):
        self.assertFalse(
            _module_present("odin.promote_check"),
            "odin.promote_check must stay retired (W5 task #205)",
        )


class CleanMergeLandsInTestingTests(APITestCase):
    """Full post-reflection pipeline: a clean merge advances the task to
    TESTING and leaves it there.  No DONE transition, no promotion report,
    no operator action required.

    Mirrors the merge-conflict tests in ``test_dag_executor_worktree.py``
    but asserts the *success* path's terminal state.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board, odin_id="sp_clean", metadata={"branch": "spec/sp_clean"},
        )

    def _task_in_review(self):
        return self.make_task(
            self.board, spec=self.spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_clean/205"},
        )

    @patch("tasks.dag_executor._merge_task_branch")
    def test_clean_merge_advances_to_testing(self, mock_merge_branch):
        """A successful merge → REVIEW → TESTING.  End state is TESTING."""
        from odin.worktree import MergeResult
        from tasks.dag_executor import merge_task_on_reflection

        mock_merge_branch.return_value = MergeResult(success=True)
        task = self._task_in_review()

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)

    @patch("tasks.dag_executor._merge_task_branch")
    def test_clean_merge_does_not_auto_promote_to_done(self, mock_merge_branch):
        """The keystone regression: a clean merge must NOT flip to DONE.

        Pre-retirement this would have dispatched the auto-promote celery
        task, which re-ran the suites and (in wave 4) held every green task
        on a phantom gap.  Now the task simply stays in TESTING until the
        human flips it by hand.
        """
        from odin.worktree import MergeResult
        from tasks.dag_executor import merge_task_on_reflection

        mock_merge_branch.return_value = MergeResult(success=True)
        task = self._task_in_review()

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        self.assertNotEqual(task.status, TaskStatus.DONE)

        # No DONE history row was ever written.
        self.assertFalse(
            TaskHistory.objects.filter(
                task=task, field_name="status", new_value=TaskStatus.DONE,
            ).exists(),
            "system must never auto-set DONE",
        )

        # No promotion-report comment was posted (the gate is gone).
        self.assertFalse(
            TaskComment.objects.filter(
                task=task, comment_type=CommentType.PROMOTION_REPORT,
            ).exists(),
            "no promotion_report comment should be posted",
        )
        # And no auto-promote status comment either.
        self.assertFalse(
            TaskComment.objects.filter(
                task=task, author_email="system+auto-promote@taskit",
            ).exists(),
            "no auto-promote comment should be posted",
        )
