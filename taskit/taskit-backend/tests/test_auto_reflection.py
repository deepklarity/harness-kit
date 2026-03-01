"""Tests for automatic reflection trigger on task REVIEW transition.

The auto-reflection trigger lives in the view layer (same pattern as the
IN_PROGRESS → execution trigger). When a task transitions to REVIEW via
the update endpoint or execution_result endpoint, a ReflectionReport is
created with PENDING status and the Celery execute_reflection task is
dispatched.

Covers:
- Auto-trigger via PUT /tasks/:id/ (manual status change)
- Auto-trigger via POST /tasks/:id/execution_result/ (agent reports success)
- Duplicate prevention (skip if PENDING/RUNNING reflection exists)
- Allow re-trigger when only COMPLETED/FAILED reflections exist
- No trigger on non-REVIEW transitions (DONE, FAILED, etc.)
- Default values on the auto-created report
"""

from unittest.mock import patch

from .base import APITestCase
from tasks.models import (
    ReflectionReport, ReflectionStatus, TaskStatus,
)


class TestAutoReflectionViaUpdate(APITestCase):
    """PUT /tasks/:id/ with status=REVIEW triggers auto-reflection."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _update_status(self, task_id, new_status, updated_by="admin@test.com"):
        return self.client.put(
            f"/tasks/{task_id}/",
            {"status": new_status, "updated_by": updated_by},
            format="json",
        )

    # ── Happy path ────────────────────────────────────────────────

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_review_transition_creates_reflection_report(self, mock_delay):
        """Task → REVIEW should create a PENDING ReflectionReport."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Reset to a pre-REVIEW state (can't create as EXECUTING via make_task easily
        # because the executing lock would block updates, so use REVIEW → done → REVIEW)
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self._update_status(task.id, "REVIEW")
        self.assertEqual(resp.status_code, 200)

        reports = ReflectionReport.objects.filter(task=task)
        self.assertEqual(reports.count(), 1)
        report = reports.first()
        self.assertEqual(report.status, ReflectionStatus.PENDING)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_review_transition_dispatches_celery_task(self, mock_delay):
        """Task → REVIEW should dispatch the Celery execute_reflection task."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        self._update_status(task.id, "REVIEW")

        report = ReflectionReport.objects.get(task=task)
        mock_delay.assert_called_once_with(report.id)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_review_transition_uses_default_values(self, mock_delay):
        """Auto-created report should use sensible defaults."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        self._update_status(task.id, "REVIEW")

        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, "claude-sonnet-4-5-20250929")
        self.assertEqual(report.requested_by, "system@taskit")
        self.assertEqual(
            report.context_selections,
            ["description", "comments", "execution_result", "dependencies", "metadata"],
        )
        self.assertEqual(report.custom_prompt, "")

    # ── Duplicate prevention ──────────────────────────────────────

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_duplicate_when_pending_reflection_exists(self, mock_delay):
        """If a PENDING reflection already exists, don't create another."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.PENDING,
        )

        self._update_status(task.id, "REVIEW")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 1)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_duplicate_when_running_reflection_exists(self, mock_delay):
        """If a RUNNING reflection exists, don't create another."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.RUNNING,
        )

        self._update_status(task.id, "REVIEW")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 1)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_allows_new_reflection_when_only_completed_exist(self, mock_delay):
        """If all existing reflections are COMPLETED, allow a new one."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.COMPLETED,
            verdict="PASS",
        )

        self._update_status(task.id, "REVIEW")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 2)
        new_report = ReflectionReport.objects.filter(
            task=task, status=ReflectionStatus.PENDING
        ).first()
        self.assertIsNotNone(new_report)
        mock_delay.assert_called_once_with(new_report.id)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_allows_new_reflection_when_only_failed_exist(self, mock_delay):
        """If all existing reflections are FAILED, allow a new one."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.FAILED,
            error_message="Timeout",
        )

        self._update_status(task.id, "REVIEW")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 2)
        mock_delay.assert_called_once()

    # ── No-trigger cases ──────────────────────────────────────────

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_trigger_on_done_transition(self, mock_delay):
        """Task → DONE should NOT trigger auto-reflection."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._update_status(task.id, "DONE")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 0)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_trigger_on_failed_transition(self, mock_delay):
        """Task → FAILED should NOT trigger auto-reflection."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        self._update_status(task.id, "FAILED")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 0)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_trigger_when_already_review(self, mock_delay):
        """Updating a task that is already REVIEW should not trigger again."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Update title while already in REVIEW — not a status transition
        self.client.put(
            f"/tasks/{task.id}/",
            {"title": "Updated title", "updated_by": "admin@test.com"},
            format="json",
        )

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 0)
        mock_delay.assert_not_called()


class TestAutoReflectionViaExecutionResult(APITestCase):
    """POST /tasks/:id/execution_result/ with status=REVIEW triggers auto-reflection."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _post_execution_result(self, task_id, success=True, status="REVIEW"):
        return self.client.post(
            f"/tasks/{task_id}/execution_result/",
            {
                "execution_result": {
                    "success": success,
                    "raw_output": "Task completed successfully.",
                    "duration_ms": 5000,
                },
                "status": status,
                "updated_by": "claude+sonnet-4@odin.agent",
            },
            format="json",
        )

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_successful_execution_result_triggers_reflection(self, mock_delay):
        """execution_result with status=REVIEW creates a reflection."""
        task = self.make_task(self.board, status=TaskStatus.EXECUTING)
        resp = self._post_execution_result(task.id, success=True, status="REVIEW")
        self.assertEqual(resp.status_code, 200)

        reports = ReflectionReport.objects.filter(task=task)
        self.assertEqual(reports.count(), 1)
        self.assertEqual(reports.first().status, ReflectionStatus.PENDING)
        mock_delay.assert_called_once_with(reports.first().id)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_failed_execution_result_no_trigger(self, mock_delay):
        """execution_result with status=FAILED does NOT trigger reflection."""
        task = self.make_task(self.board, status=TaskStatus.EXECUTING)
        resp = self._post_execution_result(task.id, success=False, status="FAILED")
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 0)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_execution_result_skips_if_active_reflection_exists(self, mock_delay):
        """execution_result with status=REVIEW skips if reflection already active."""
        task = self.make_task(self.board, status=TaskStatus.EXECUTING)
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.RUNNING,
        )

        self._post_execution_result(task.id, success=True, status="REVIEW")

        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 1)
        mock_delay.assert_not_called()
