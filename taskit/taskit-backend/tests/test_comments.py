"""Tests for TaskComment model and API endpoints.

Covers: POST/GET comments, validation, list exclusion, detail endpoint, actor identity format.
"""

from .base import APITestCase
from tasks.models import Spec, TaskComment


class TestCommentCRUD(APITestCase):
    """POST and GET /tasks/:id/comments/ endpoints."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_post_comment(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "minimax+MiniMax-M2.5@odin.agent",
                "author_label": "minimax (MiniMax-M2.5)",
                "content": "Completed in 12.3s\n\nAssembled final HTML.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author_email"], "minimax+MiniMax-M2.5@odin.agent")
        self.assertEqual(resp.data["author_label"], "minimax (MiniMax-M2.5)")
        self.assertEqual(resp.data["content"], "Completed in 12.3s\n\nAssembled final HTML.")
        self.assertEqual(resp.data["attachments"], [])
        self.assertIn("id", resp.data)
        self.assertIn("created_at", resp.data)
        self.assertEqual(resp.data["task_id"], self.task.id)

    def test_get_comments_empty(self):
        resp = self.client.get(f"/tasks/{self.task.id}/comments/")
        self.assertEqual(resp.status_code, 200)
        # Paginated response
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["results"], [])

    def test_get_comments_ordered_by_created_at(self):
        """Comments returned in chronological order (oldest first)."""
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@odin.agent", "content": "First"},
            format="json",
        )
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "b@odin.agent", "content": "Second"},
            format="json",
        )
        resp = self.client.get(f"/tasks/{self.task.id}/comments/")
        results = resp.data["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "First")
        self.assertEqual(results[1]["content"], "Second")

    def test_post_comment_with_attachments(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "claude+sonnet-4-5@odin.agent",
                "content": "See attached logs.",
                "attachments": [{"type": "file", "path": "/tmp/output.log"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["type"], "file")

    def test_post_comment_missing_content_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "test@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_comment_missing_email_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"content": "Hello"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_comment_invalid_email_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "not-an-email", "content": "Hello"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_comment_on_nonexistent_task_404(self):
        resp = self.client.post(
            "/tasks/99999/comments/",
            {"author_email": "test@example.com", "content": "Hello"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_comments_on_nonexistent_task_404(self):
        resp = self.client.get("/tasks/99999/comments/")
        self.assertEqual(resp.status_code, 404)

    def test_author_label_optional(self):
        """author_label defaults to empty string when not provided."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "human@example.com", "content": "Nice work."},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author_label"], "")


class TestCommentActorIdentity(APITestCase):
    """Verify the agent+model@odin.agent email format is accepted."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_agent_model_email_format(self):
        """RFC 5321 plus-delimited local part is valid."""
        emails = [
            "claude+sonnet-4-5@odin.agent",
            "minimax+MiniMax-M2.5@odin.agent",
            "codex+codex-mini@odin.agent",
        ]
        for email in emails:
            resp = self.client.post(
                f"/tasks/{self.task.id}/comments/",
                {"author_email": email, "content": f"Comment from {email}"},
                format="json",
            )
            self.assertEqual(resp.status_code, 201, f"Failed for email: {email}")

    def test_odin_system_email(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "odin@harness.kit", "content": "System note."},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_human_email(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "alice@example.com", "content": "Looks good!"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)


class TestTaskListExcludesComments(APITestCase):
    """Task list endpoint returns lightweight tasks — no comments payload."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_task_list_excludes_comments(self):
        TaskComment.objects.create(
            task=self.task,
            author_email="minimax+MiniMax-M2.5@odin.agent",
            author_label="minimax (MiniMax-M2.5)",
            content="Completed in 12.3s",
        )
        resp = self.client.get(f"/tasks/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.data["results"]
        self.assertEqual(len(tasks), 1)
        self.assertNotIn("comments", tasks[0])
        self.assertIn("comment_count", tasks[0])


class TestTaskDetailEndpoint(APITestCase):
    """GET /tasks/:id/detail/ returns full task with comments + spec_title."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = Spec.objects.create(
            board=self.board, odin_id="sp_detail", title="Detail Spec"
        )
        self.task = self.make_task(self.board, spec=self.spec)
        TaskComment.objects.create(
            task=self.task,
            author_email="claude+sonnet-4-5@odin.agent",
            content="Comment for detail test.",
        )

    def test_detail_returns_comments(self):
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("comments", resp.data)
        self.assertEqual(len(resp.data["comments"]), 1)
        self.assertEqual(resp.data["comments"][0]["content"], "Comment for detail test.")

    def test_detail_returns_history(self):
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertIn("history", resp.data)

    def test_detail_returns_spec_title(self):
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertEqual(resp.data["spec_title"], "Detail Spec")

    def test_detail_spec_title_null_when_no_spec(self):
        task_no_spec = self.make_task(self.board, title="No spec task")
        resp = self.client.get(f"/tasks/{task_no_spec.id}/detail/")
        self.assertIsNone(resp.data["spec_title"])

    def test_detail_nonexistent_task_404(self):
        resp = self.client.get("/tasks/99999/detail/")
        self.assertEqual(resp.status_code, 404)

    def test_detail_includes_base_fields(self):
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertIn("id", resp.data)
        self.assertIn("title", resp.data)
        self.assertIn("status", resp.data)
        self.assertIn("priority", resp.data)


class TestTaskFiltering(APITestCase):
    """Task list filtering via query params."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.task1 = self.make_task(self.board, title="High priority", priority="HIGH", status="TODO")
        self.task2 = self.make_task(self.board, title="Low priority", priority="LOW", status="IN_PROGRESS", assignee=self.user)

    def test_filter_by_status(self):
        resp = self.client.get(f"/tasks/?board_id={self.board.id}&status=TODO")
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "High priority")

    def test_filter_by_priority(self):
        resp = self.client.get(f"/tasks/?board_id={self.board.id}&priority=LOW")
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Low priority")

    def test_filter_by_assignee(self):
        resp = self.client.get(f"/tasks/?board_id={self.board.id}&assignee_id={self.user.id}")
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Low priority")

    def test_search_by_title(self):
        resp = self.client.get(f"/tasks/?board_id={self.board.id}&search=High")
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "High priority")


class TestCommentAppendOnly(APITestCase):
    """Comments are append-only — no edit, no delete via API."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_multiple_comments_accumulate(self):
        """Failure + retry scenario: both comments stay."""
        # First attempt fails
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "gemini+gemini-2.0@odin.agent",
                "content": "Failed in 23.1s\n\nError: syntax error.",
            },
            format="json",
        )
        # Retry with different agent succeeds
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "claude+sonnet-4-5@odin.agent",
                "content": "Completed in 45.0s\n\nFixed syntax and completed.",
            },
            format="json",
        )
        resp = self.client.get(f"/tasks/{self.task.id}/comments/")
        results = resp.data["results"]
        self.assertEqual(len(results), 2)
        self.assertIn("Failed", results[0]["content"])
        self.assertIn("Completed", results[1]["content"])

    def test_no_put_endpoint(self):
        """PUT on comments should not be allowed."""
        resp = self.client.put(
            f"/tasks/{self.task.id}/comments/",
            {"content": "Updated"},
            format="json",
        )
        self.assertIn(resp.status_code, [405, 400])

    def test_no_delete_endpoint(self):
        """DELETE on comments should not be allowed."""
        resp = self.client.delete(f"/tasks/{self.task.id}/comments/")
        self.assertIn(resp.status_code, [405, 400])
