"""Tests for needs_human → merged reconciliation against git reality.

Background
----------
When the merge agent flags a task ``merge_status='needs_human'`` (ambiguous
conflict, watchdog escalation, or Celery broker failure) and the operator
then resolves the conflict by hand, the merge commit lands on the spec
branch but nothing ever clears the flag — the board shows phantom pending
merges forever.

The reconciliation pass runs on the existing periodic seam
(``scan_pending_merges`` / Celery Beat) and, for each needs_human task,
checks whether the task branch is now an ancestor of the spec branch.  If
it is, the flag is cleared, the resolution is recorded, stale error fields
are dropped, and exactly one status comment is posted.

These tests pin down:

1. ``test_reconciles_when_branch_merged_into_spec``
   Flagged + branch is ancestor of spec → cleared to ``merged``,
   ``merge_resolution`` recorded, ``merge_error``/``merge_ambiguous_files``
   dropped, exactly ONE status comment.

2. ``test_leaves_unmerged_branch_untouched``
   Flagged + branch is NOT an ancestor → untouched (status, metadata, and
   comment count all unchanged).

3. ``test_already_merged_not_iterated``
   A task already at ``merge_status='merged'`` is never selected → no
   duplicate comment, no metadata churn.

4. ``test_skips_when_no_worktree_manager``
   No WorktreeManager resolvable (odin unavailable / no board working dir)
   → skipped gracefully, no crash, no change.

5. ``test_skips_task_without_branch_metadata``
   Flagged but no ``branch`` on metadata → skipped.

6. ``test_scan_pending_merges_invokes_reconciliation``
   The periodic Beat entry point calls the reconciliation pass — this is
   the natural seam (no new cron).

7. ``test_multiple_tasks_only_reconciled_one_touched``
   Two flagged tasks, one merged one not → only the merged one reconciled.

8. ``test_idempotent_across_scans``
   Running reconciliation twice produces exactly one comment (the second
   pass no longer sees the task as needs_human).
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tasks.models import CommentType, Task, TaskComment, TaskStatus
from tests.base import APITestCase


class _ReconciliationCase(APITestCase):
    """Shared fixtures: board, spec with a branch, a flagged task."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board, odin_id="sp_fable_w4",
            metadata={"branch": "spec/sp_fable_w4"},
        )
        self.task = self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.REVIEW,
            metadata={
                "branch": "task/sp_fable_w4/193",
                "merge_status": "needs_human",
                "merge_error": "CONFLICT (content): Merge conflict in src/app.py",
                "merge_ambiguous_files": ["src/app.py"],
            },
        )

    def _mock_wt(self, is_ancestor_return):
        """Patch ``_get_worktree_manager`` to return a fake whose
        ``is_ancestor`` yields ``is_ancestor_return``."""
        fake_wt = type(
            "FakeWT", (), {"is_ancestor": lambda self, a, d: is_ancestor_return},
        )()
        return patch("tasks.dag_executor._get_worktree_manager", return_value=fake_wt)


class ReconcileNeedsHumanTests(_ReconciliationCase):

    def test_reconciles_when_branch_merged_into_spec(self):
        """Flagged + branch merged on spec → cleared, recorded, one comment."""
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        with self._mock_wt(is_ancestor_return=True):
            result = reconcile_needs_human_merges()

        self.assertIn(self.task.id, result["reconciled"])

        self.task.refresh_from_db()
        meta = self.task.metadata
        self.assertEqual(meta.get("merge_status"), "merged")
        self.assertEqual(
            meta.get("merge_resolution"),
            "reconciled: branch merged on spec branch",
        )
        # Stale error fields dropped.
        self.assertNotIn("merge_error", meta)
        self.assertNotIn("merge_ambiguous_files", meta)

        # Exactly one status comment posted by the reconciliation.
        comments = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.STATUS_UPDATE,
        )
        self.assertEqual(comments.count(), 1)
        body = comments.first().content.lower()
        self.assertIn("reconcil", body)
        self.assertIn("merged", body)

    def test_leaves_unmerged_branch_untouched(self):
        """Flagged + branch NOT an ancestor → untouched."""
        TaskComment.objects.filter(task=self.task).delete()
        before_meta = dict(self.task.metadata)
        from tasks.dag_executor import reconcile_needs_human_merges

        with self._mock_wt(is_ancestor_return=False):
            result = reconcile_needs_human_merges()

        self.assertNotIn(self.task.id, result["reconciled"])
        self.assertIn(self.task.id, result["skipped"])

        self.task.refresh_from_db()
        # Status, error fields, and comment count all unchanged.
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        self.assertEqual(
            self.task.metadata.get("merge_error"), before_meta["merge_error"],
        )
        self.assertEqual(
            self.task.metadata.get("merge_ambiguous_files"),
            before_meta["merge_ambiguous_files"],
        )
        self.assertEqual(
            TaskComment.objects.filter(task=self.task).count(), 0,
        )

    def test_already_merged_not_iterated(self):
        """A task already at merge_status='merged' is never selected."""
        self.task.metadata = {**self.task.metadata, "merge_status": "merged"}
        self.task.save(update_fields=["metadata"])
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        with self._mock_wt(is_ancestor_return=True):
            result = reconcile_needs_human_merges()

        self.assertNotIn(self.task.id, result["reconciled"])
        self.assertEqual(
            TaskComment.objects.filter(task=self.task).count(), 0,
        )

    def test_skips_when_no_worktree_manager(self):
        """No WorktreeManager → skipped gracefully, no crash, no change."""
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        with patch("tasks.dag_executor._get_worktree_manager", return_value=None):
            result = reconcile_needs_human_merges()

        self.assertIn(self.task.id, result["skipped"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        self.assertEqual(
            TaskComment.objects.filter(task=self.task).count(), 0,
        )

    def test_skips_task_without_branch_metadata(self):
        """Flagged but no branch on metadata → skipped."""
        self.task.metadata = {
            "merge_status": "needs_human",
            "merge_error": "boom",
        }
        self.task.save(update_fields=["metadata"])
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        with self._mock_wt(is_ancestor_return=True):
            result = reconcile_needs_human_merges()

        self.assertIn(self.task.id, result["skipped"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")

    def test_multiple_tasks_only_reconciled_one_touched(self):
        """Two flagged tasks, one merged one not → only the merged one."""
        merged_task = self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.REVIEW,
            title="merged by hand",
            metadata={
                "branch": "task/sp_fable_w4/200",
                "merge_status": "needs_human",
            },
        )
        TaskComment.objects.filter(task=merged_task).delete()
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        def fake_is_ancestor(branch, spec_branch):
            # Only merged_task's branch is an ancestor.
            return branch == "task/sp_fable_w4/200"

        fake_wt = type(
            "FakeWT", (), {"is_ancestor": lambda s, a, d: fake_is_ancestor(a, d)},
        )()
        with patch("tasks.dag_executor._get_worktree_manager", return_value=fake_wt):
            result = reconcile_needs_human_merges()

        self.assertIn(merged_task.id, result["reconciled"])
        self.assertIn(self.task.id, result["skipped"])

        merged_task.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(merged_task.metadata.get("merge_status"), "merged")
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        # Only the reconciled task got a comment.
        self.assertEqual(
            TaskComment.objects.filter(task=merged_task).count(), 1,
        )
        self.assertEqual(
            TaskComment.objects.filter(task=self.task).count(), 0,
        )

    def test_idempotent_across_scans(self):
        """Running reconciliation twice posts exactly one comment."""
        TaskComment.objects.filter(task=self.task).delete()
        from tasks.dag_executor import reconcile_needs_human_merges

        with self._mock_wt(is_ancestor_return=True):
            reconcile_needs_human_merges()
            reconcile_needs_human_merges()

        self.assertEqual(
            TaskComment.objects.filter(
                task=self.task, comment_type=CommentType.STATUS_UPDATE,
            ).count(), 1,
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "merged")


class ScanPendingMergesSeamTests(APITestCase):
    """The periodic Beat entry point must drive the reconciliation pass.

    This is the 'natural seam' requirement: reconciliation rides the
    existing Celery Beat schedule (``merge-watchdog-scan``), not a new
    cron. If the wire-up is dropped, this test fails first.
    """

    def test_scan_pending_merges_invokes_reconciliation(self):
        from tasks.dag_executor import scan_pending_merges

        with patch("tasks.dag_executor.reconcile_needs_human_merges") as mock_recon, \
             patch("tasks.dag_executor.scan_pending_merge_dispatches") as mock_watchdog:
            mock_recon.return_value = {"reconciled": [], "skipped": []}
            mock_watchdog.return_value = {"dispatched": [], "escalated": [], "skipped": []}
            scan_pending_merges()

        mock_recon.assert_called_once()
