"""Tests for the reflection watchdog (``scan_pending_reflections``).

Background
----------
Reflections are fire-and-forget: when a task enters REVIEW a reflection is
dispatched once, and if that moment is lost (host slept mid-transition,
worker died, broker dropped the message) the task sits in REVIEW forever
with no reviewer. Two tasks waited 6 hours in production.

Merges had the same disease and got ``scan_pending_merges`` + a dedicated
queue (task #196). Reflections deserve the same explicit ownership. This
watchdog mirrors the merge watchdog's shape — same module, same style, one
pattern.

What these tests pin
--------------------
1. Missed dispatch (no report at all) → watchdog dispatches a reflection.
2. Stale PENDING/RUNNING report (worker died) → reaped + re-dispatched.
3. Fresh active report (within window) → skip (no double-dispatch).
4. Retry / backoff via the idle window, then escalation comment.
5. No-verdict (verdict="ERROR") counts as an attempt and retries.
6. Dedicated reflection queue routing (REFLECTION_QUEUE_NAME).
7. Beat schedule + settings wiring.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.utils import timezone

from tasks.models import (
    CommentType,
    ReflectionReport,
    ReflectionStatus,
    Task,
    TaskComment,
    TaskStatus,
)
from tests.base import APITestCase

_REVIEWER = ("claude", "claude-sonnet-4-5-20250929", "size_medium")


def _patch_reviewer():
    """Patch reviewer selection so tests don't depend on seeded agent users."""
    return patch(
        "tasks.views.select_reviewer_by_context_size",
        return_value=_REVIEWER,
    )


class _ReflectionWatchdogFixture(APITestCase):
    """Shared fixture: board, REVIEW task."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)

    def _make_report(self, *, status=ReflectionStatus.PENDING, verdict="", minutes_ago=0):
        """Create a reflection report, optionally back-dated past the window."""
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=status,
            verdict=verdict,
        )
        if minutes_ago:
            old = timezone.now() - timedelta(minutes=minutes_ago)
            ReflectionReport.objects.filter(id=report.id).update(created_at=old)
            report.refresh_from_db()
        return report


class MissedDispatchTests(_ReflectionWatchdogFixture):
    """A REVIEW task whose auto-reflection was lost gets one dispatched."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_no_report_at_all_dispatches_reflection(self, mock_exec, _mock_rev):
        """Missed dispatch: no reflection report exists → watchdog creates one."""
        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=2)

        self.assertIn(self.task.id, result["dispatched"])
        mock_exec.delay.assert_called_once()
        report = ReflectionReport.objects.get(task=self.task)
        self.assertEqual(report.status, ReflectionStatus.PENDING)
        self.assertEqual(report.requested_by, "system@taskit")

        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata.get("reflection_dispatch_attempts"), 1)
        self.assertIsNotNone(self.task.metadata.get("reflection_dispatched_at"))

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_dispatched_report_uses_size_scaled_selection(self, mock_exec, _mock_rev):
        """The watchdog re-dispatch uses the size-scaled reviewer selection."""
        from tasks.dag_executor import scan_pending_reflections

        scan_pending_reflections(minutes_idle=0, max_attempts=2)

        report = ReflectionReport.objects.get(task=self.task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.selection_reason, "size_medium")


class StaleReportReapTests(_ReflectionWatchdogFixture):
    """A PENDING/RUNNING report whose worker died is reaped and re-dispatched."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_stale_pending_report_reaped_and_redispatched(self, mock_exec, _mock_rev):
        """Stale PENDING (worker died before pickup) → FAILED + fresh dispatch."""
        stale = self._make_report(status=ReflectionStatus.PENDING, minutes_ago=60)

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["reaped"])
        self.assertIn(self.task.id, result["dispatched"])
        stale.refresh_from_db()
        self.assertEqual(stale.status, ReflectionStatus.FAILED)
        self.assertIn("reaped", (stale.error_message or "").lower())
        # A fresh PENDING report was created.
        fresh = ReflectionReport.objects.filter(
            task=self.task, status=ReflectionStatus.PENDING,
        ).exclude(id=stale.id).first()
        self.assertIsNotNone(fresh)
        mock_exec.delay.assert_called_once_with(fresh.id)

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_stale_running_report_reaped(self, mock_exec, _mock_rev):
        """Stale RUNNING (worker died mid-subprocess) → FAILED + fresh dispatch."""
        stale = self._make_report(status=ReflectionStatus.RUNNING, minutes_ago=60)

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["reaped"])
        stale.refresh_from_db()
        self.assertEqual(stale.status, ReflectionStatus.FAILED)


class NoDoubleDispatchTests(_ReflectionWatchdogFixture):
    """An active reflection that is still fresh must not be re-dispatched."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_fresh_pending_report_is_skipped(self, mock_exec, _mock_rev):
        """A PENDING report created moments ago is in-flight — leave it alone."""
        self._make_report(status=ReflectionStatus.PENDING, minutes_ago=0)

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_exec.delay.assert_not_called()

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_fresh_running_report_is_skipped(self, mock_exec, _mock_rev):
        """A RUNNING report created moments ago is in-flight — leave it alone."""
        self._make_report(status=ReflectionStatus.RUNNING, minutes_ago=1)

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        mock_exec.delay.assert_not_called()

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_recent_watchdog_dispatch_within_window_skipped(self, mock_exec, _mock_rev):
        """Even with no active report, a dispatch stamped moments ago waits."""
        meta = dict(self.task.metadata or {})
        meta["reflection_dispatched_at"] = timezone.now().isoformat()
        meta["reflection_dispatch_attempts"] = 1
        self.task.metadata = meta
        self.task.save(update_fields=["metadata"])

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=10, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        mock_exec.delay.assert_not_called()


class RetryAndEscalateTests(_ReflectionWatchdogFixture):
    """Retry N times then escalate with a clear comment."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_first_retry_dispatches_and_bumps_attempts(self, mock_exec, _mock_rev):
        """attempts=0, past window → dispatch, attempts becomes 1."""
        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=2)

        self.assertIn(self.task.id, result["dispatched"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata["reflection_dispatch_attempts"], 1)

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_attempts_exhausted_escalates_with_question_comment(self, mock_exec, _mock_rev):
        """attempts >= max_attempts, past window → escalate, no dispatch."""
        meta = dict(self.task.metadata or {})
        meta["reflection_dispatch_attempts"] = 2
        meta["reflection_dispatched_at"] = (
            timezone.now() - timedelta(minutes=120)
        ).isoformat()
        self.task.metadata = meta
        self.task.save(update_fields=["metadata"])

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["escalated"])
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_exec.delay.assert_not_called()

        question = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).first()
        self.assertIsNotNone(question, "Escalation must post a QUESTION comment")
        self.assertIn("reflection", question.content.lower())
        self.assertIn("human", question.content.lower())

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_escalation_comment_names_what_failed_with_log_tail(self, mock_exec, _mock_rev):
        """The escalation comment includes the reflect log tail."""
        # Seed a reflect log file for this task.
        log_dir = Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(exist_ok=True)
        report = self._make_report(status=ReflectionStatus.FAILED, minutes_ago=60)
        log_file = log_dir / f"reflect_{self.task.id}_{report.id}.log"
        log_file.write_text("line one\nIMPORTED_FAILURE_TOKEN\nline three\n")

        meta = dict(self.task.metadata or {})
        meta["reflection_dispatch_attempts"] = 1
        meta["reflection_dispatched_at"] = (
            timezone.now() - timedelta(minutes=120)
        ).isoformat()
        self.task.metadata = meta
        self.task.save(update_fields=["metadata"])

        try:
            from tasks.dag_executor import scan_pending_reflections

            scan_pending_reflections(minutes_idle=5, max_attempts=1)

            question = TaskComment.objects.filter(
                task=self.task, comment_type=CommentType.QUESTION,
            ).first()
            self.assertIsNotNone(question)
            self.assertIn("IMPORTED_FAILURE_TOKEN", question.content)
        finally:
            log_file.unlink(missing_ok=True)


class NoVerdictErrorPathTests(_ReflectionWatchdogFixture):
    """A reviewer that emits no verdict counts as an attempt and retries."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_error_verdict_counts_as_attempt_and_retries(self, mock_exec, _mock_rev):
        """A COMPLETED report with verdict=ERROR → retry with size-scaled fallback."""
        # The most recent report completed but emitted no usable verdict.
        self._make_report(
            status=ReflectionStatus.COMPLETED, verdict="ERROR", minutes_ago=10,
        )

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["dispatched"])
        # The retry used the size-scaled reviewer selection.
        new_report = ReflectionReport.objects.filter(
            task=self.task, status=ReflectionStatus.PENDING,
        ).first()
        self.assertIsNotNone(new_report)
        self.assertEqual(new_report.selection_reason, "size_medium")

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_empty_verdict_counts_as_attempt(self, mock_exec, _mock_rev):
        """A COMPLETED report with an empty verdict also retries."""
        self._make_report(
            status=ReflectionStatus.COMPLETED, verdict="", minutes_ago=10,
        )

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["dispatched"])

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_completed_pass_verdict_is_skipped(self, mock_exec, _mock_rev):
        """A COMPLETED report with a usable verdict must NOT be re-reflected.

        Regression (task 266, board 6): reflection passed, the merge parked
        on a human conflict question, the task legitimately stayed in
        REVIEW — and the watchdog dispatched a second reflection, whose
        pass spawned a second merge agent on the same conflict. A usable
        verdict means the post-reflection pipeline owns the task.
        """
        self._make_report(
            status=ReflectionStatus.COMPLETED, verdict="PASS", minutes_ago=10,
        )

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        self.assertNotIn(self.task.id, result["dispatched"])
        mock_exec.delay.assert_not_called()
        self.assertEqual(
            ReflectionReport.objects.filter(task=self.task).count(), 1,
        )

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_completed_pass_from_prior_cycle_still_redispatches(self, mock_exec, _mock_rev):
        """A PASS from a PREVIOUS review cycle doesn't suppress the watchdog.

        After rework the task re-enters REVIEW; if the new cycle's
        auto-reflection was lost, the stale old PASS must not make the
        watchdog skip forever.
        """
        from tasks.models import TaskHistory

        self._make_report(
            status=ReflectionStatus.COMPLETED, verdict="PASS", minutes_ago=30,
        )
        # Task re-entered REVIEW after that report was written.
        TaskHistory.objects.create(
            task=self.task,
            field_name="status",
            old_value=TaskStatus.EXECUTING,
            new_value=TaskStatus.REVIEW,
            changed_by="claude+m@odin.agent",
        )

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=5, max_attempts=2)

        self.assertIn(self.task.id, result["dispatched"])


class EscalationIdempotencyTests(_ReflectionWatchdogFixture):
    """Task 300 accumulated 40 identical 'Reflection stalled' comments:
    the escalation block set reflection_watchdog_escalated_at but never
    checked it, so every beat past the attempt cap posted again."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_escalation_comment_posts_exactly_once(self, mock_exec, _mock_rev):
        from tasks.dag_executor import scan_pending_reflections
        from tasks.models import TaskComment

        meta = dict(self.task.metadata or {})
        meta["reflection_dispatch_attempts"] = 5
        meta["reflection_dispatched_at"] = (
            timezone.now() - timedelta(minutes=60)
        ).isoformat()
        self.task.metadata = meta
        self.task.save(update_fields=["metadata"])

        for _ in range(3):
            scan_pending_reflections(minutes_idle=5, max_attempts=2)

        n = TaskComment.objects.filter(
            task=self.task, content__startswith="**Reflection stalled"
        ).count()
        self.assertEqual(n, 1)


class RetryDiversityTests(_ReflectionWatchdogFixture):
    """297 got three identical ERROR verdicts: the deterministic walk
    re-picked the same unparseable reviewer on every retry."""

    @patch("tasks.dag_executor.execute_reflection")
    def test_retry_skips_reviewer_that_errored_this_cycle(self, mock_exec):
        from tasks.dag_executor import scan_pending_reflections
        from tasks.models import BoardMembership, User, UserRole

        codex = User.objects.create(
            email="codex@odin.agent", name="codex", role=UserRole.AGENT,
            is_active=True,
            available_models=[{"name": "gpt-5.5", "is_default": True,
                               "output_price_per_1m_tokens": 25.0}],
        )
        claude = User.objects.create(
            email="claude@odin.agent", name="claude", role=UserRole.AGENT,
            is_active=True,
            available_models=[{"name": "claude-sonnet-5", "is_default": True,
                               "output_price_per_1m_tokens": 15.0}],
        )
        BoardMembership.objects.create(board=self.board, user=codex)
        BoardMembership.objects.create(board=self.board, user=claude)
        self.board.reviewer_order = [
            {"agent_name": "codex", "model_name": "gpt-5.5"},
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        # Last completed reflection this cycle: codex, unusable verdict.
        report = self._make_report(
            status=ReflectionStatus.COMPLETED, verdict="ERROR", minutes_ago=10,
        )
        report.reviewer_agent = "codex"
        report.reviewer_model = "gpt-5.5"
        report.save(update_fields=["reviewer_agent", "reviewer_model"])

        result = scan_pending_reflections(minutes_idle=5, max_attempts=5)

        self.assertIn(self.task.id, result["dispatched"])
        fresh = ReflectionReport.objects.filter(
            task=self.task, status=ReflectionStatus.PENDING,
        ).first()
        self.assertIsNotNone(fresh)
        self.assertEqual(
            (fresh.reviewer_agent, fresh.reviewer_model),
            ("claude", "claude-sonnet-5"),
            "retry must skip the reviewer whose verdict just errored",
        )


class ReflectionQueueRoutingTests(_ReflectionWatchdogFixture):
    """Opt-in dedicated reflection queue so executions cannot starve reviews."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_default_uses_delay(self, mock_exec, _mock_rev):
        """No REFLECTION_QUEUE_NAME → plain ``.delay()`` on the default queue."""
        from tasks.dag_executor import scan_pending_reflections

        scan_pending_reflections(minutes_idle=0, max_attempts=2)

        mock_exec.delay.assert_called_once()
        mock_exec.apply_async.assert_not_called()

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_queue_set_routes_to_named_queue(self, mock_exec, _mock_rev):
        """REFLECTION_QUEUE_NAME set → ``apply_async(queue=...)``."""
        from tasks.dag_executor import scan_pending_reflections

        with self.settings(REFLECTION_QUEUE_NAME="reflections"):
            scan_pending_reflections(minutes_idle=0, max_attempts=2)

        mock_exec.apply_async.assert_called_once()
        _, kwargs = mock_exec.apply_async.call_args
        self.assertEqual(kwargs.get("queue"), "reflections")
        mock_exec.delay.assert_not_called()


class SkipGuardTests(_ReflectionWatchdogFixture):
    """The watchdog must not interfere with tasks it shouldn't touch."""

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_skip_when_reflection_disabled_on_task(self, mock_exec, _mock_rev):
        self.task.skip_reflection = True
        self.task.save(update_fields=["skip_reflection"])

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        mock_exec.delay.assert_not_called()

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_skip_when_reflection_disabled_on_board(self, mock_exec, _mock_rev):
        self.board.skip_reflection = True
        self.board.save(update_fields=["skip_reflection"])

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=2)

        self.assertIn(self.task.id, result["skipped"])
        mock_exec.delay.assert_not_called()

    @_patch_reviewer()
    @patch("tasks.dag_executor.execute_reflection")
    def test_skip_non_review_status(self, mock_exec, _mock_rev):
        self.task.status = TaskStatus.TESTING
        self.task.save(update_fields=["status"])

        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=2)

        self.assertNotIn(self.task.id, result["dispatched"])
        mock_exec.delay.assert_not_called()


class BeatScheduleAndSettingsTests(APITestCase):
    """The watchdog only works if Beat schedules it and settings exist."""

    def test_scan_pending_reflections_in_beat_schedule(self):
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
        entry = schedule.get("reflection-watchdog-scan")
        self.assertIsNotNone(
            entry,
            "reflection-watchdog-scan must be registered in CELERY_BEAT_SCHEDULE",
        )
        self.assertEqual(entry["task"], "tasks.dag_executor.scan_pending_reflections")
        self.assertIsInstance(entry["schedule"], int)
        self.assertGreater(entry["schedule"], 0)

    def test_watchdog_settings_exposed(self):
        idle = getattr(settings, "REFLECTION_WATCHDOG_MINUTES_IDLE", None)
        max_attempts = getattr(settings, "REFLECTION_WATCHDOG_MAX_ATTEMPTS", None)
        self.assertIsNotNone(idle)
        self.assertIsNotNone(max_attempts)
        self.assertGreaterEqual(idle, 1)
        self.assertGreaterEqual(max_attempts, 1)

    def test_idle_window_default_exceeds_reflection_timeout(self):
        """Default idle window must exceed the reflection subprocess timeout.

        A reflection can legitimately run up to
        ``DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS`` (default 1800s = 30min).
        The idle window must exceed that or a legitimately-running reflection
        gets reaped as stale.
        """
        from tasks.dag_executor import REFLECTION_WATCHDOG_MINUTES_IDLE_DEFAULT

        timeout = getattr(settings, "DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS", 1800)
        self.assertGreater(
            REFLECTION_WATCHDOG_MINUTES_IDLE_DEFAULT * 60,
            timeout,
            "REFLECTION_WATCHDOG_MINUTES_IDLE_DEFAULT must exceed the reflection "
            "subprocess timeout or a live reflection gets reaped as stale",
        )
