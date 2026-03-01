"""Tests for the reflection audit feature.

Covers:
- ReflectionReport model creation and defaults
- POST /tasks/:id/reflect/ — trigger endpoint (status validation, Celery dispatch)
- GET /tasks/:id/reflections/ — list reports for a task
- GET /reflections/ — list all reflections with filters
- PATCH /reflections/:id/ — Odin submits results
- POST /reflections/:id/cancel/ — cancel a reflection
- assembled_prompt field + serializer task_title
"""

from unittest.mock import patch
from django.utils import timezone

from .base import APITestCase
from tasks.models import ReflectionReport, ReflectionStatus, TaskComment, TaskStatus


class TestReflectionReportModel(APITestCase):
    """ReflectionReport model basics."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)

    def test_reflection_report_creation_with_defaults(self):
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
        )
        self.assertEqual(report.status, ReflectionStatus.PENDING)
        self.assertEqual(report.custom_prompt, "")
        self.assertEqual(report.context_selections, [])
        self.assertEqual(report.quality_assessment, "")
        self.assertEqual(report.verdict, "")
        self.assertEqual(report.raw_output, "")
        self.assertEqual(report.assembled_prompt, "")
        self.assertIsNone(report.duration_ms)
        self.assertEqual(report.token_usage, {})
        self.assertIsNone(report.completed_at)

    def test_reflection_status_choices(self):
        for status_val in ["PENDING", "RUNNING", "COMPLETED", "FAILED"]:
            report = ReflectionReport.objects.create(
                task=self.task,
                reviewer_agent="claude",
                reviewer_model="claude-opus-4-6",
                requested_by="admin@test.com",
                status=status_val,
            )
            self.assertEqual(report.status, status_val)

    def test_reflection_report_linked_to_task(self):
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
        )
        self.assertEqual(report.task_id, self.task.id)
        self.assertIn(report, self.task.reflections.all())


class TestReflectEndpoint(APITestCase):
    """POST /tasks/:id/reflect/ — trigger a reflection audit."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _reflect(self, task_id, data=None):
        data = data or {}
        return self.client.post(f"/tasks/{task_id}/reflect/", data, format="json")

    def test_reflect_rejects_todo_status(self):
        task = self.make_task(self.board, status=TaskStatus.TODO)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 400)

    def test_reflect_rejects_in_progress_status(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 400)

    def test_reflect_rejects_executing_status(self):
        task = self.make_task(self.board, status=TaskStatus.EXECUTING)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 400)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_dispatches_celery_task(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)
        # Celery task should be dispatched with the report ID
        report = ReflectionReport.objects.get(task=task)
        mock_delay.assert_called_once_with(report.id)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_accepts_review_status(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_accepts_done_status(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.DONE)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_accepts_failed_status(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.FAILED)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_creates_pending_report(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["status"], "PENDING")
        self.assertEqual(resp.data["task"], task.id)
        self.assertEqual(resp.data["reviewer_agent"], "claude")
        self.assertEqual(resp.data["reviewer_model"], "claude-opus-4-6")
        # Verify DB
        self.assertEqual(ReflectionReport.objects.filter(task=task).count(), 1)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_default_context_selections(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id)
        self.assertEqual(
            resp.data["context_selections"],
            ["description", "comments", "execution_result", "dependencies", "metadata"],
        )

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_reflect_custom_model_and_prompt(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._reflect(task.id, {
            "reviewer_agent": "gemini",
            "reviewer_model": "gemini-2.5-pro",
            "custom_prompt": "Focus on error handling",
            "context_selections": ["description", "comments"],
        })
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["reviewer_agent"], "gemini")
        self.assertEqual(resp.data["reviewer_model"], "gemini-2.5-pro")
        self.assertEqual(resp.data["custom_prompt"], "Focus on error handling")
        self.assertEqual(resp.data["context_selections"], ["description", "comments"])


class TestReflectionsListEndpoint(APITestCase):
    """GET /tasks/:id/reflections/ — list reports for a task."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)

    def test_reflections_list_empty_when_none(self):
        resp = self.client.get(f"/tasks/{self.task.id}/reflections/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_reflections_list_returns_reports_for_task(self):
        ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
        )
        ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="gemini",
            reviewer_model="gemini-2.5-pro",
            requested_by="admin@test.com",
        )
        resp = self.client.get(f"/tasks/{self.task.id}/reflections/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)


class TestReflectionReportUpdate(APITestCase):
    """PATCH /reflections/:id/ — Odin submits results."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self.report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
            status=ReflectionStatus.RUNNING,
        )

    def test_reflection_update_sets_completed_status(self):
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "COMPLETED", "verdict": "PASS", "verdict_summary": "Looks good"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "COMPLETED")

    def test_reflection_update_sets_verdict(self):
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {
                "status": "COMPLETED",
                "quality_assessment": "Code is clean",
                "slop_detection": "No slop found",
                "improvements": "Add error handling",
                "agent_optimization": "Model was appropriate",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Minor improvements needed",
                "raw_output": "Full agent output here",
                "duration_ms": 45000,
                "token_usage": {"input_tokens": 1000, "output_tokens": 2000},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.verdict, "NEEDS_WORK")
        self.assertEqual(self.report.verdict_summary, "Minor improvements needed")
        self.assertEqual(self.report.quality_assessment, "Code is clean")
        self.assertEqual(self.report.duration_ms, 45000)
        self.assertEqual(self.report.token_usage, {"input_tokens": 1000, "output_tokens": 2000})

    def test_reflection_update_sets_completed_at(self):
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "COMPLETED", "verdict": "PASS"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.completed_at)

    def test_assembled_prompt_stored_in_report(self):
        """PATCH with assembled_prompt stores it on the report."""
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "RUNNING", "assembled_prompt": "You are a reviewer..."},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.assembled_prompt, "You are a reviewer...")

    def test_assembled_prompt_preserved_on_second_patch(self):
        """Second PATCH without assembled_prompt should NOT clobber it."""
        # First PATCH: set assembled_prompt
        self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "RUNNING", "assembled_prompt": "You are a reviewer..."},
            format="json",
        )
        # Second PATCH: COMPLETED without assembled_prompt
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "COMPLETED", "verdict": "PASS", "raw_output": "Some output"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        # assembled_prompt must still be there
        self.assertEqual(self.report.assembled_prompt, "You are a reviewer...")

    def test_completed_reflection_posts_comment_on_task(self):
        """PATCH with COMPLETED + verdict_summary creates a reflection comment on the task."""
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Missing input validation on the API endpoint",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        comment = TaskComment.objects.filter(task=self.task, comment_type="reflection").first()
        self.assertIsNotNone(comment, "A reflection comment should be created")
        self.assertIn("NEEDS_WORK", comment.content)
        self.assertIn("Missing input validation", comment.content)
        self.assertEqual(comment.author_email, "admin@test.com")
        self.assertEqual(comment.author_label, "claude/claude-opus-4-6")
        # Attachments should link back to the report
        self.assertEqual(len(comment.attachments), 1)
        self.assertEqual(comment.attachments[0]["type"], "reflection")
        self.assertEqual(comment.attachments[0]["report_id"], self.report.id)
        self.assertEqual(comment.attachments[0]["verdict"], "NEEDS_WORK")

    def test_completed_reflection_without_summary_skips_comment(self):
        """PATCH with COMPLETED but empty verdict_summary should NOT create a comment."""
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "COMPLETED", "verdict": "PASS"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        count = TaskComment.objects.filter(task=self.task, comment_type="reflection").count()
        self.assertEqual(count, 0, "No comment should be created without verdict_summary")

    def test_failed_reflection_does_not_post_comment(self):
        """PATCH with FAILED status should NOT create a reflection comment."""
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "FAILED", "error_message": "Timeout"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        count = TaskComment.objects.filter(task=self.task, comment_type="reflection").count()
        self.assertEqual(count, 0)

    def test_completed_at_not_set_on_running_patch(self):
        """PATCH with status=RUNNING should NOT set completed_at."""
        resp = self.client.patch(
            f"/reflections/{self.report.id}/",
            {"status": "RUNNING"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.completed_at)

    def test_retrieve_single_reflection(self):
        """GET /reflections/:id/ returns a single report."""
        resp = self.client.get(f"/reflections/{self.report.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], self.report.id)
        self.assertEqual(resp.json()["task_title"], self.report.task.title)


class TestReflectionCancel(APITestCase):
    """POST /reflections/:id/cancel/ — cancel a reflection."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)

    def _make_report(self, **kwargs):
        defaults = dict(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
        )
        defaults.update(kwargs)
        return ReflectionReport.objects.create(**defaults)

    def test_cancel_pending_reflection(self):
        report = self._make_report(status=ReflectionStatus.PENDING)
        resp = self.client.post(f"/reflections/{report.id}/cancel/")
        self.assertEqual(resp.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, ReflectionStatus.FAILED)
        self.assertEqual(report.error_message, "Cancelled by user")
        self.assertIsNotNone(report.completed_at)

    def test_cancel_running_reflection(self):
        report = self._make_report(status=ReflectionStatus.RUNNING)
        resp = self.client.post(f"/reflections/{report.id}/cancel/")
        self.assertEqual(resp.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, ReflectionStatus.FAILED)

    def test_cancel_completed_reflection_rejected(self):
        report = self._make_report(status=ReflectionStatus.COMPLETED)
        resp = self.client.post(f"/reflections/{report.id}/cancel/")
        self.assertEqual(resp.status_code, 400)

    def test_cancel_failed_reflection_rejected(self):
        report = self._make_report(status=ReflectionStatus.FAILED)
        resp = self.client.post(f"/reflections/{report.id}/cancel/")
        self.assertEqual(resp.status_code, 400)

    def test_delete_reflection(self):
        report = self._make_report(status=ReflectionStatus.COMPLETED)
        resp = self.client.delete(f"/reflections/{report.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ReflectionReport.objects.filter(id=report.id).exists())

    def test_delete_pending_reflection(self):
        report = self._make_report(status=ReflectionStatus.PENDING)
        resp = self.client.delete(f"/reflections/{report.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ReflectionReport.objects.filter(id=report.id).exists())


class TestReflectionListAll(APITestCase):
    """GET /reflections/ — list all reflections with optional filters."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task1 = self.make_task(self.board, title="Task A", status=TaskStatus.REVIEW)
        self.task2 = self.make_task(self.board, title="Task B", status=TaskStatus.DONE)

    def _make_report(self, task, **kwargs):
        defaults = dict(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-opus-4-6",
            requested_by="admin@test.com",
        )
        defaults.update(kwargs)
        return ReflectionReport.objects.create(**defaults)

    def test_list_all_reflections(self):
        self._make_report(self.task1, status=ReflectionStatus.COMPLETED, verdict="PASS")
        self._make_report(self.task2, status=ReflectionStatus.FAILED)
        resp = self.client.get("/reflections/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)

    def test_list_reflections_filter_by_status(self):
        self._make_report(self.task1, status=ReflectionStatus.COMPLETED, verdict="PASS")
        self._make_report(self.task2, status=ReflectionStatus.FAILED)
        resp = self.client.get("/reflections/?status=COMPLETED")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["status"], "COMPLETED")

    def test_list_reflections_filter_by_verdict(self):
        self._make_report(self.task1, status=ReflectionStatus.COMPLETED, verdict="PASS")
        self._make_report(self.task2, status=ReflectionStatus.COMPLETED, verdict="NEEDS_WORK")
        resp = self.client.get("/reflections/?verdict=PASS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["verdict"], "PASS")

    def test_serializer_includes_task_title(self):
        self._make_report(self.task1, status=ReflectionStatus.COMPLETED, verdict="PASS")
        resp = self.client.get("/reflections/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data[0]["task_title"], "Task A")
