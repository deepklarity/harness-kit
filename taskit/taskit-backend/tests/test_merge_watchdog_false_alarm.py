"""Regression tests for the merge watchdog false-alarm bug.

Background
----------
The post-reflection merge watchdog (``dag_executor.scan_pending_merge_dispatches``)
marked merges as ``needs_human`` while they were still running. Three false
alarms fired in one day; the worst escalated ONE SECOND after the merge was
dispatched (dispatch 12:55:49, escalation 12:55:50).

Root cause: the escalation branch checked
``if last_dispatched_at and attempts >= max_attempts`` and escalated immediately,
*without* consulting the idle-window check. The window check existed but sat
below the escalation branch, so a task about to be escalated never reached it.
Because ``_merge_task_on_reflection_pass`` stamps ``merge_dispatch_attempts=1``
on the initial dispatch and ``max_attempts`` defaults to 1, the very next beat
tick (60s later — or sooner if the clocks align) escalated.

Second contributor: merges routinely take ~20 min to land because they share
the celery worker pool with long-running executions and starve behind them.
A 10-minute idle window is shorter than the real merge latency, so even a
correctly-windowed watchdog would fire early.

These tests pin three behaviors:

1. ``test_recent_dispatch_does_not_escalate_even_when_attempts_exhausted``
   The false alarm: attempts >= max but dispatched seconds ago → SKIP.

2. ``test_already_merged_branch_is_reconciled_not_escalated``
   Git reality: task branch already an ancestor of spec branch → mark
   ``merge_status=merged`` and stop (the worker crashed after the merge
   commit but before stamping metadata).

3. ``test_window_default_above_observed_merge_latency``
   The default idle window must exceed the observed ~20 min merge latency.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from tasks.models import (
    CommentType, ReflectionReport, ReflectionStatus,
    Task, TaskComment, TaskStatus,
)
from tests.base import APITestCase


class _WatchdogFixture(APITestCase):
    """Shared fixture: board, spec, REVIEW task with a branch + PASS reflection."""

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
            metadata={"branch": "task/sp_fable_w4/196"},
        )

    def _mark_dispatched(self, *, attempts=1, minutes_ago=0, merge_status="pending"):
        """Make the task look like a merge was dispatched ``minutes_ago`` ago."""
        stamp = (timezone.now() - timedelta(minutes=minutes_ago)).isoformat()
        meta = dict(self.task.metadata or {})
        meta["merge_status"] = merge_status
        meta["merge_dispatch_attempts"] = attempts
        meta["merge_dispatched_at"] = stamp
        self.task.metadata = meta
        self.task.status = TaskStatus.REVIEW
        self.task.save(update_fields=["metadata", "status"])

        ReflectionReport.objects.update_or_create(
            task=self.task,
            defaults=dict(
                reviewer_agent="claude",
                reviewer_model="claude-sonnet-4-5-20250929",
                requested_by="system@taskit",
                status=ReflectionStatus.COMPLETED,
                verdict="PASS",
                verdict_summary="stuck",
            ),
        )
        self.task.refresh_from_db()


class FalseAlarmWindowTests(_WatchdogFixture):
    """The window must gate escalation, not just retry."""

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_recent_dispatch_does_not_escalate_even_when_attempts_exhausted(
        self, _mock_merged, mock_merge,
    ):
        """THE false alarm: dispatched 1 second ago, attempts >= max → SKIP.

        Replay of today's three false alarms (tasks 185, 191, 192): each was
        dispatched and then escalated on the next beat tick because the
        escalation branch ignored the idle window.
        """
        # attempts=1 (>= default max_attempts=1), dispatched ~0 minutes ago.
        self._mark_dispatched(attempts=1, minutes_ago=0)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10, max_attempts=1)

        self.assertNotIn(self.task.id, result["escalated"])
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_not_called()

        # merge_status must NOT have been flipped to needs_human.
        self.task.refresh_from_db()
        self.assertNotEqual(self.task.metadata.get("merge_status"), "needs_human")

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_replay_false_alarms_185_191_192(self, _mock_merged, mock_merge):
        """All three of today's false alarms were dispatched within ~1 minute.

        A dispatch 1 minute ago must survive a scan with the production default
        window (30 min). Parametrized as a loop so a failure names the task.
        """
        from tasks.dag_executor import scan_pending_merge_dispatches

        for task_id_label, minutes_ago in [("185", 0), ("191", 1), ("192", 0)]:
            with self.subTest(task=task_id_label, minutes_ago=minutes_ago):
                self._mark_dispatched(attempts=1, minutes_ago=minutes_ago)
                result = scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)
                self.assertNotIn(
                    self.task.id, result["escalated"],
                    f"false alarm replay for task {task_id_label}: should not escalate",
                )
                self.task.refresh_from_db()
                self.assertNotEqual(
                    self.task.metadata.get("merge_status"), "needs_human",
                    f"false alarm replay for task {task_id_label}: merge_status must not flip",
                )

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_within_window_skips_even_past_max_attempts(self, _mock_merged, mock_merge):
        """attempts >= max but only 5 min into a 30-min window → SKIP."""
        self._mark_dispatched(attempts=1, minutes_ago=5)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)

        self.assertNotIn(self.task.id, result["escalated"])
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_not_called()

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_dead_merge_still_escalates_after_window(self, _mock_merged, mock_merge):
        """Genuinely dead merge: window expired, not merged, attempts exhausted → escalate."""
        self._mark_dispatched(attempts=1, minutes_ago=45)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)

        self.assertIn(self.task.id, result["escalated"])
        mock_merge.delay.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        question = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).first()
        self.assertIsNotNone(question)


class GitRealityCheckTests(_WatchdogFixture):
    """Before escalating, check git: maybe the merge already landed."""

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=True)
    def test_already_merged_branch_is_reconciled_not_escalated(
        self, _mock_merged, mock_merge,
    ):
        """Task branch already an ancestor of spec branch → mark merged, stop.

        Covers the case where the merge commit landed but the celery worker
        died before writing ``merge_status=merged``. The watchdog must not
        cry wolf — it reconciles and leaves the task alone.
        """
        # Window expired AND attempts exhausted — i.e. would otherwise escalate.
        self._mark_dispatched(attempts=1, minutes_ago=45)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)

        self.assertNotIn(self.task.id, result["escalated"])
        self.assertIn(self.task.id, result["merged"])
        mock_merge.delay.assert_not_called()

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "merged")

        # A STATUS_UPDATE comment records the reconciliation.
        note = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.STATUS_UPDATE,
            content__icontains="already merged",
        ).first()
        self.assertIsNotNone(note)

    def test_git_helper_fails_open_when_no_manager(self):
        """No WorktreeManager (no board working_dir) → helper returns False, not raise.

        Fail-open matters: a false 'merged' would silently drop a real merge,
        while a false 'not merged' just keeps waiting (the safe direction).
        """
        from tasks.dag_executor import _is_task_branch_merged

        # Task has no board working_dir → _get_worktree_manager returns None.
        # The helper must return False (not raise) so a scan never crashes.
        self.assertFalse(_is_task_branch_merged(self.task))


class MergeWatchdogDefaultsTests(APITestCase):
    """The default idle window must clear the observed ~20 min merge latency."""

    def test_window_default_above_observed_merge_latency(self):
        """Default MERGE_WATCHDOG_MINUTES_IDLE must exceed 20 minutes.

        Merges routinely take ~20 min when execution VMs starve the worker
        pool. A window shorter than the real latency guarantees false alarms.
        """
        from django.conf import settings
        from tasks.dag_executor import MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT

        idle = getattr(settings, "MERGE_WATCHDOG_MINUTES_IDLE", MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT)
        self.assertGreater(
            idle, 20,
            "MERGE_WATCHDOG_MINUTES_IDLE must exceed the observed ~20 min merge "
            "latency or the watchdog will fire while merges are still in flight",
        )
        self.assertEqual(idle, MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT)


class MergeQueueRoutingTests(_WatchdogFixture):
    """Opt-in dedicated merge queue so executions cannot starve merges.

    Default (MERGE_QUEUE_NAME unset) must use ``.delay()`` — unchanged
    behavior. When set, dispatch routes to that queue.
    """

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_default_uses_delay(self, _mock_merged, mock_merge):
        """No MERGE_QUEUE_NAME → plain ``.delay()`` on the default queue."""
        self._mark_dispatched(attempts=0, minutes_ago=45)

        from tasks.dag_executor import scan_pending_merge_dispatches
        scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)

        mock_merge.delay.assert_called_once_with(self.task.id)
        mock_merge.apply_async.assert_not_called()

    @patch("tasks.dag_executor.merge_task_on_reflection")
    @patch("tasks.dag_executor._is_task_branch_merged", return_value=False)
    def test_queue_set_routes_to_named_queue(self, _mock_merged, mock_merge):
        """MERGE_QUEUE_NAME set → ``apply_async(queue=...)`` so a dedicated
        worker can drain merges without waiting behind executions."""
        self._mark_dispatched(attempts=0, minutes_ago=45)

        from tasks.dag_executor import scan_pending_merge_dispatches
        with self.settings(MERGE_QUEUE_NAME="merges"):
            scan_pending_merge_dispatches(minutes_idle=30, max_attempts=1)

        mock_merge.apply_async.assert_called_once_with(
            args=(self.task.id,), queue="merges",
        )
        mock_merge.delay.assert_not_called()
