"""Tests for auto-advancing tasks based on reflection verdicts.

PASS → REVIEW → TESTING (existing)
NEEDS_WORK → REVIEW → IN_PROGRESS (retry) or REVIEW → FAILED (after 3 attempts)
FAIL → stays in REVIEW (human triage)
Quota failure → reassign to different agent before retry
"""

from unittest.mock import MagicMock, patch

from .base import APITestCase
from tasks.models import (
    BoardMembership, ReflectionReport, ReflectionStatus,
    TaskComment, TaskHistory, TaskStatus, User, UserRole,
)


class TestAutoAdvanceOnReflection(APITestCase):
    """PATCH /reflections/:id/ auto-advances task based on verdict."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _create_report(self, task, status=ReflectionStatus.RUNNING):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=status,
        )

    def _create_completed_report(self, task, verdict="NEEDS_WORK"):
        """Create a report already in COMPLETED state (counts toward limit)."""
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.COMPLETED,
            verdict=verdict,
            verdict_summary="Previous attempt.",
        )

    def _complete_report(self, report_id, verdict, verdict_summary="Summary."):
        return self.client.patch(
            f"/reflections/{report_id}/",
            {
                "status": "COMPLETED",
                "verdict": verdict,
                "verdict_summary": verdict_summary,
            },
            format="json",
        )

    # ── PASS → auto-advance ──────────────────────────────────────

    def test_pass_verdict_moves_task_from_review_to_testing(self):
        """Reflection PASS should advance task REVIEW → TESTING."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        resp = self._complete_report(report.id, verdict="PASS")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)

    def test_pass_verdict_records_task_history(self):
        """Auto-advance should create a TaskHistory entry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        history = TaskHistory.objects.filter(
            task=task, field_name="status"
        ).order_by("-changed_at").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.REVIEW)
        self.assertEqual(history.new_value, TaskStatus.TESTING)
        self.assertEqual(history.changed_by, "system@taskit")

    # ── NEEDS_WORK → retry loop ──────────────────────────────────

    def test_needs_work_moves_task_from_review_to_in_progress(self):
        """First NEEDS_WORK should send task back to IN_PROGRESS for retry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        resp = self._complete_report(report.id, verdict="NEEDS_WORK")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_needs_work_records_task_history(self):
        """NEEDS_WORK retry should create a TaskHistory entry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        history = TaskHistory.objects.filter(
            task=task, field_name="status"
        ).order_by("-changed_at").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.REVIEW)
        self.assertEqual(history.new_value, TaskStatus.IN_PROGRESS)
        self.assertEqual(history.changed_by, "system@taskit")

    @patch("tasks.execution.get_strategy")
    def test_needs_work_triggers_execution_strategy(self, mock_get_strategy):
        """NEEDS_WORK should fire the execution strategy for assigned tasks."""
        user = User.objects.create(name="Agent", email="agent@test.com")
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=user)
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        mock_strategy.trigger.assert_called_once_with(task)

    # ── 3-strike failure ─────────────────────────────────────────

    def test_third_needs_work_fails_task(self):
        """After 3 completed reflections, NEEDS_WORK should FAIL the task."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Two prior completed reflections
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="NEEDS_WORK")
        # Third attempt (this one completes via the API, making count = 3)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_third_needs_work_posts_failure_comment(self):
        """Failure after 3 attempts should post an explanatory comment."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="NEEDS_WORK")
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        comment = TaskComment.objects.filter(task=task, author_email="system@taskit").last()
        self.assertIsNotNone(comment)
        self.assertIn("3 reflection attempts", comment.content)

    def test_mixed_verdicts_count_toward_limit(self):
        """Any completed reflection counts — not just NEEDS_WORK verdicts."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Two prior: one NEEDS_WORK + one FAIL = 2 completed
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="FAIL")
        # Third attempt via API (count becomes 3)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    # ── FAIL verdict → same retry loop as NEEDS_WORK ────────────

    def test_fail_verdict_moves_task_to_in_progress(self):
        """Reflection FAIL should send task back for retry (same as NEEDS_WORK)."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_third_fail_verdict_fails_task(self):
        """After 3 completed reflections, FAIL should FAIL the task."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._create_completed_report(task, verdict="FAIL")
        self._create_completed_report(task, verdict="FAIL")
        report = self._create_report(task)

        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    # ── Guard against concurrent status changes ──────────────────

    def test_pass_verdict_does_not_overwrite_done_status(self):
        """If task already moved to DONE, PASS should not regress it."""
        task = self.make_task(self.board, status=TaskStatus.DONE)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_pass_verdict_does_not_overwrite_testing_status(self):
        """If task is already in TESTING, no duplicate transition."""
        task = self.make_task(self.board, status=TaskStatus.TESTING)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        # No new history entry since status didn't change
        history_count = TaskHistory.objects.filter(
            task=task, field_name="status", changed_by="system@taskit"
        ).count()
        self.assertEqual(history_count, 0)

    def test_needs_work_does_not_overwrite_non_review_status(self):
        """If task already moved out of REVIEW, NEEDS_WORK should not overwrite."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # No history entry since guard prevented the transition
        history_count = TaskHistory.objects.filter(
            task=task, field_name="status", changed_by="system@taskit"
        ).count()
        self.assertEqual(history_count, 0)


class TestQuotaFailureReassignment(APITestCase):
    """Quota/rate-limit failures should reassign to a different agent before retry."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # Create two agent users
        self.claude_agent = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=["claude-sonnet-4-5-20250929"],
        )
        self.gemini_agent = User.objects.create(
            name="gemini", email="gemini@odin.agent", role=UserRole.AGENT,
            available_models=["gemini-2.5-flash"],
        )
        # Add both to the board
        BoardMembership.objects.create(board=self.board, user=self.claude_agent)
        BoardMembership.objects.create(board=self.board, user=self.gemini_agent)

    def _create_report(self, task, status=ReflectionStatus.RUNNING, **kwargs):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=status,
            **kwargs,
        )

    def _complete_report(self, report_id, verdict, verdict_summary="Summary.", **extra):
        payload = {
            "status": "COMPLETED",
            "verdict": verdict,
            "verdict_summary": verdict_summary,
            **extra,
        }
        return self.client.patch(
            f"/reflections/{report_id}/",
            payload,
            format="json",
        )

    # ── Quota detection from task metadata ────────────────────────

    @patch("tasks.execution.get_strategy")
    def test_quota_failure_from_metadata_triggers_reassignment(self, mock_get_strategy):
        """Task with last_failure_type=llm_call_failure + quota keyword → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL", verdict_summary="Rate limit hit.")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.gemini_agent.id)
        self.assertEqual(task.model_name, "gemini-2.5-flash")

    # ── Quota detection from reflection quota_failure field ───────

    @patch("tasks.execution.get_strategy")
    def test_quota_failure_from_reflection_field_triggers_reassignment(self, mock_get_strategy):
        """Reflection quota_failure field flagged → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="FAIL",
            verdict_summary="Agent could not complete.",
            quota_failure="QUOTA_FAILURE: claude",
        )

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.gemini_agent.id)

    # ── Quota detection from verdict_summary keywords ────────────

    @patch("tasks.execution.get_strategy")
    def test_quota_keyword_in_verdict_summary_triggers_reassignment(self, mock_get_strategy):
        """Verdict summary mentioning 'quota exceeded' → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="FAIL",
            verdict_summary="Task failed because quota exceeded on Claude.",
        )

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.gemini_agent.id)
        self.assertEqual(task.model_name, "gemini-2.5-flash")

    # ── History and comment recording ────────────────────────────

    @patch("tasks.execution.get_strategy")
    def test_reassignment_records_history_and_comment(self, mock_get_strategy):
        """Reassignment should create TaskHistory entries and a comment."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "quota exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL")

        # Assignee history
        assignee_history = TaskHistory.objects.filter(
            task=task, field_name="assignee", changed_by="system@taskit",
        ).first()
        self.assertIsNotNone(assignee_history)
        self.assertEqual(assignee_history.old_value, "claude")
        self.assertEqual(assignee_history.new_value, "gemini")

        # Model history
        model_history = TaskHistory.objects.filter(
            task=task, field_name="model", changed_by="system@taskit",
        ).first()
        self.assertIsNotNone(model_history)

        # Comment
        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()
        self.assertIn("Quota/rate-limit failure", comment.content)
        self.assertIn("gemini", comment.content)

    # ── No reassignment when no alternative agent ────────────────

    @patch("tasks.execution.get_strategy")
    def test_no_reassignment_when_no_alternative_agent(self, mock_get_strategy):
        """If only one agent exists, skip reassignment but still retry."""
        mock_get_strategy.return_value = MagicMock()

        # Remove gemini from the board
        BoardMembership.objects.filter(user=self.gemini_agent).delete()
        self.gemini_agent.delete()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "quota exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        # Still retries (goes to IN_PROGRESS) but keeps same agent
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.claude_agent.id)

        # Should post a warning comment
        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()
        self.assertIn("no alternative agent", comment.content)

    # ── Non-quota failure should NOT reassign ────────────────────

    @patch("tasks.execution.get_strategy")
    def test_non_quota_failure_does_not_reassign(self, mock_get_strategy):
        """Normal NEEDS_WORK (code quality issue) should NOT reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="NEEDS_WORK",
            verdict_summary="Code quality needs improvement.",
        )

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Should keep the same agent
        self.assertEqual(task.assignee_id, self.claude_agent.id)
        self.assertEqual(task.model_name, "claude-sonnet-4-5-20250929")
