"""Tests for the post-reflection auto-merge dispatch (W3.22 — task #171).

Background
----------
After reflection reports PASS, ``views._merge_task_on_reflection_pass`` is
expected to dispatch ``dag_executor.merge_task_on_reflection`` so the task
branch is merged into the spec branch and the task advances REVIEW → TESTING.
In wave 3, 9 tasks (150, 151, 154, 156, 157, 164, 165, 166, 167) had reflection
PASS but the post-PASS auto-merge silently never fired — ``merge_status``
stayed ``pending`` with no error, no log line, no needs_human report.

These tests pin down three behaviors that close the silent-skip holes:

1. ``test_dispatch_fires_for_review_with_branch``
   Happy path — task in REVIEW, branch exists → ``merge_task_on_reflection``
   is dispatched (via Celery ``.delay``).

2. ``test_skip_when_status_not_review_logs_and_posts_comment``
   Silent-skip path — task in DONE/TESTING when reflection arrives →
   ``logger.warning`` + a STATUS_UPDATE comment. The merge is NOT dispatched
   (race condition: another flow already moved the task), but the operator
   sees *why* nothing happened.

3. ``test_skip_when_already_merged_logs_and_posts_comment``
   Silent-skip path — ``merge_status == "merged"`` (duplicate dispatch or
   manual merge) → ``logger.warning`` + a STATUS_UPDATE comment instead of a
   bare ``return``.

4. ``test_celery_dispatch_failure_posted_as_comment``
   The ``.delay()`` call itself can raise if the broker is down. We wrap it
   in try/except so a broker outage doesn't 500 the reflection PATCH and
   doesn't lose the merge silently — a STATUS_UPDATE comment marks it.

5. ``test_watchdog_finds_pending_merges_and_dispatches_once``
   Watchdog — task with status=REVIEW, latest reflection PASS, merge still
   pending → re-dispatch merge once, increment attempt counter.

6. ``test_watchdog_retries_only_once_then_marks_needs_human``
   Watchdog — task whose previous watchdog attempt already happened →
   marks ``merge_status = "needs_human"`` and posts a QUESTION comment.

7. ``test_watchdog_skips_when_merge_already_done``
   Watchdog — task with merge_status=merged is left alone.

8. ``test_watchdog_skips_recent_dispatch``
   Watchdog — task whose last dispatch was < N minutes ago is left alone.
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


class _MergeDispatchCase(APITestCase):
    """Shared fixtures: board, spec, REVIEW task with branch."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_fable_w3", metadata={"branch": "spec/sp_fable_w3"})
        self.task = self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_fable_w3/171"},
        )

    def _complete_with_pass(self, task, verdict="PASS"):
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": verdict,
                "verdict_summary": "Looks good.",
            },
            format="json",
        )


class MergeDispatchHappyPathTests(_MergeDispatchCase):
    """Path 1 — fired correctly."""

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_dispatch_fires_for_review_with_branch(self, mock_merge):
        """REVIEW + branch → merge_task_on_reflection.delay() is called."""
        resp = self._complete_with_pass(self.task)
        self.assertEqual(resp.status_code, 200)

        # Celery dispatch fired
        mock_merge.delay.assert_called_once_with(self.task.id)

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_dispatch_records_metadata(self, mock_merge):
        """After dispatch, metadata.merge_dispatched_at + attempts are stamped."""
        self._complete_with_pass(self.task)

        self.task.refresh_from_db()
        meta = self.task.metadata or {}
        self.assertEqual(meta.get("merge_dispatch_attempts"), 1)
        self.assertIsNotNone(meta.get("merge_dispatched_at"))


class MergeDispatchSkipLoggingTests(_MergeDispatchCase):
    """Path 2 — silent skip paths now log + post a comment."""

    def setUp(self):
        super().setUp()
        self.task.status = TaskStatus.DONE
        self.task.save(update_fields=["status"])

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_skip_when_status_not_review_logs_and_posts_comment(self, mock_merge):
        """Task in DONE when reflection PASS arrives → skip must be loud.

        Regression: this silent skip is why 9 wave-3 tasks stalled. The
        reflection saved, the verdict was PASS, the status guard prevented
        dispatch, and the operator never saw anything.
        """
        # Wipe any prior comments so we can isolate the new one.
        TaskComment.objects.filter(task=self.task).delete()

        resp = self._complete_with_pass(self.task)
        self.assertEqual(resp.status_code, 200)

        mock_merge.delay.assert_not_called()

        # A STATUS_UPDATE comment marks the skip on the task timeline.
        skip_comment = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.STATUS_UPDATE,
        ).first()
        self.assertIsNotNone(skip_comment)
        # Reason names the new status so the operator understands why.
        body = skip_comment.content
        self.assertIn("merge dispatch skipped", body.lower())
        self.assertIn(TaskStatus.DONE, body)

        # Metadata records the skip too — a watchdog can pick it up.
        self.task.refresh_from_db()
        self.assertEqual(
            self.task.metadata.get("merge_dispatch_skip_reason"),
            f"status_not_review:{TaskStatus.DONE}",
        )


class MergeAlreadyMergedTests(_MergeDispatchCase):
    """The ``merge_status == "merged"`` skip was a silent bare return."""

    def setUp(self):
        super().setUp()
        # Task stays in REVIEW (default), but merge_status is already merged
        # — duplicate dispatch or manual merge.
        self.task.metadata = {**self.task.metadata, "merge_status": "merged"}
        self.task.save(update_fields=["metadata"])

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_skip_when_already_merged_logs_and_posts_comment(self, mock_merge):
        """merge_status == 'merged' must log + comment instead of bare return."""
        TaskComment.objects.filter(task=self.task).delete()

        resp = self._complete_with_pass(self.task)
        self.assertEqual(resp.status_code, 200)

        mock_merge.delay.assert_not_called()

        skip_comment = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.STATUS_UPDATE,
            content__icontains="already merged",
        ).first()
        self.assertIsNotNone(skip_comment)

        self.task.refresh_from_db()
        self.assertEqual(
            self.task.metadata.get("merge_dispatch_skip_reason"),
            "already_merged",
        )


class CeleryDispatchFailureTests(_MergeDispatchCase):
    """A Celery broker outage must not be silent — task is marked needs_human."""

    @patch("tasks.dag_executor.merge_task_on_reflection.delay",
           side_effect=RuntimeError("broker down"))
    def test_celery_dispatch_failure_posted_as_comment(self, mock_delay):
        """A Celery broker failure must not be silent — post a comment + log."""
        TaskComment.objects.filter(task=self.task).delete()

        resp = self._complete_with_pass(self.task)
        # The reflection itself still succeeds; only the merge dispatch failed.
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once_with(self.task.id)

        err_comment = TaskComment.objects.filter(
            task=self.task,
            comment_type=CommentType.STATUS_UPDATE,
            content__icontains="merge dispatch failed",
        ).first()
        self.assertIsNotNone(err_comment)
        self.assertIn("broker down", err_comment.content)

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        self.assertIn("broker down", self.task.metadata.get("merge_dispatch_error", ""))


class MergeWatchdogTests(_MergeDispatchCase):
    """Path 3 — watchdog retries once then escalates to needs_human."""

    def _make_stuck_task(self, *, attempts=0, last_dispatched_minutes_ago=None,
                        merge_status="pending", status=TaskStatus.REVIEW,
                        latest_verdict="PASS"):
        """Create a task that looks stalled from the watchdog's POV."""
        metadata = dict(self.task.metadata or {})
        metadata["merge_status"] = merge_status
        metadata["merge_dispatch_attempts"] = attempts
        if last_dispatched_minutes_ago is not None:
            stamp = (timezone.now() - timedelta(minutes=last_dispatched_minutes_ago)).isoformat()
            metadata["merge_dispatched_at"] = stamp
        self.task.metadata = metadata
        self.task.status = status
        self.task.save(update_fields=["metadata", "status"])

        # A latest completed reflection with the given verdict.
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.COMPLETED,
            verdict=latest_verdict,
            verdict_summary="stuck",
        )
        return report

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_finds_pending_merges_and_dispatches_once(self, mock_merge):
        """First observation: dispatch merge + bump attempts."""
        self._make_stuck_task(attempts=0, last_dispatched_minutes_ago=999)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)

        self.assertIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_called_once_with(self.task.id)

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_dispatch_attempts"), 1)
        self.assertIsNotNone(self.task.metadata.get("merge_dispatched_at"))

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_retries_only_once_then_marks_needs_human(self, mock_merge):
        """Second observation (after one retry): mark needs_human + question."""
        # attempts=1 means the watchdog already retried once.
        self._make_stuck_task(attempts=1, last_dispatched_minutes_ago=999)

        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)

        self.assertIn(self.task.id, result["escalated"])
        mock_merge.delay.assert_not_called()

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("merge_status"), "needs_human")
        question = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).first()
        self.assertIsNotNone(question)
        self.assertIn("merge", question.content.lower())
        self.assertIn("human", question.content.lower())

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_skips_when_merge_already_done(self, mock_merge):
        """merge_status in {merged, noop, needs_human, conflict, error}: skip."""
        self._make_stuck_task(merge_status="merged")
        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)
        self.assertNotIn(self.task.id, result["dispatched"])
        self.assertNotIn(self.task.id, result["escalated"])
        mock_merge.delay.assert_not_called()

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_skips_recent_dispatch(self, mock_merge):
        """If the last dispatch was within the idle window, the watchdog waits."""
        self._make_stuck_task(attempts=0, last_dispatched_minutes_ago=2)
        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_not_called()

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_only_considers_pass_verdicts(self, mock_merge):
        """A task whose latest reflection verdict is FAIL or NEEDS_WORK is ignored."""
        self._make_stuck_task(attempts=0, last_dispatched_minutes_ago=999, latest_verdict="NEEDS_WORK")
        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_not_called()

    @patch("tasks.dag_executor.merge_task_on_reflection")
    def test_watchdog_skips_non_review_status(self, mock_merge):
        """If the task already moved out of REVIEW, watchdog doesn't restart it."""
        self._make_stuck_task(attempts=0, last_dispatched_minutes_ago=999, status=TaskStatus.TESTING)
        from tasks.dag_executor import scan_pending_merge_dispatches
        result = scan_pending_merge_dispatches(minutes_idle=10)
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_merge.delay.assert_not_called()


class MergeTaskOnReflectionLoggingTests(_MergeDispatchCase):
    """The Celery worker path itself also had silent-skip holes."""

    def test_merge_task_on_reflection_no_branch_logs_info(self):
        """No branch on metadata → advance + log (no longer silent)."""
        self.task.metadata = {}
        self.task.save(update_fields=["metadata"])

        from tasks.dag_executor import merge_task_on_reflection
        merge_task_on_reflection.__wrapped__(self.task.id)

        # Status advanced
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.TESTING)

    def test_merge_task_on_reflection_already_merged_logs_info(self):
        """merge_status==merged at worker time → log + advance."""
        self.task.metadata = {
            **self.task.metadata,
            "merge_status": "merged",
        }
        self.task.save(update_fields=["metadata"])

        from tasks.dag_executor import merge_task_on_reflection
        merge_task_on_reflection.__wrapped__(self.task.id)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.TESTING)


class MergeWatchdogBeatScheduleTests(APITestCase):
    """The watchdog only does its job if Celery Beat actually schedules it.

    Regression: task #171 (NEEDS_WORK on the prior wave) shipped the
    ``scan_pending_merges`` task but forgot to wire it into
    ``CELERY_BEAT_SCHEDULE`` — so the watchdog sat idle and stalling tasks
    continued to stall.  This test pins the schedule entry so the next person
    who removes the beat registration has to touch this test first.
    """

    def test_scan_pending_merges_is_in_celery_beat_schedule(self):
        from django.conf import settings

        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
        entry = schedule.get("merge-watchdog-scan")
        self.assertIsNotNone(
            entry,
            "merge-watchdog-scan must be registered in CELERY_BEAT_SCHEDULE "
            "so celery-beat dispatches scan_pending_merges on a timer",
        )
        self.assertEqual(entry["task"], "tasks.dag_executor.scan_pending_merges")
        # Cadence: must be a positive integer (seconds).
        self.assertIsInstance(entry["schedule"], int)
        self.assertGreater(entry["schedule"], 0)

    def test_watchdog_settings_exposed(self):
        """Settings-exposed tunables exist with sane defaults."""
        from django.conf import settings

        idle = getattr(settings, "MERGE_WATCHDOG_MINUTES_IDLE", None)
        max_attempts = getattr(settings, "MERGE_WATCHDOG_MAX_ATTEMPTS", None)
        self.assertIsNotNone(idle)
        self.assertIsNotNone(max_attempts)
        self.assertGreaterEqual(idle, 1)
        self.assertGreaterEqual(max_attempts, 1)