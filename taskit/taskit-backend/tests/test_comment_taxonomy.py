"""Tests for comment_type taxonomy — model, serializer, view, and backfill layers.

Covers:
- CommentType choices on TaskComment model
- Serializer inclusion and validation of comment_type
- View endpoints setting correct comment_type
- ?type= query parameter filtering
- Backfill logic for pre-migration comments
"""

from .base import APITestCase
from tasks.models import TaskComment


class TestCommentTypeField(APITestCase):
    """Model layer: comment_type field behavior."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_default_comment_type_is_status_update(self):
        """Comment created without explicit type defaults to status_update."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Making progress",
        )
        self.assertEqual(comment.comment_type, "status_update")

    def test_comment_type_choices_valid(self):
        """All comment types save correctly."""
        types = ["status_update", "question", "reply", "proof", "summary", "reflection"]
        for ct in types:
            comment = TaskComment.objects.create(
                task=self.task,
                author_email="agent@odin.agent",
                content=f"Test {ct}",
                comment_type=ct,
            )
            self.assertEqual(comment.comment_type, ct)

    def test_comment_type_persists_on_refresh(self):
        """comment_type survives save + refresh_from_db round-trip."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Proof data",
            comment_type="proof",
        )
        comment.refresh_from_db()
        self.assertEqual(comment.comment_type, "proof")


class TestCommentTypeSerializer(APITestCase):
    """Serializer layer: comment_type in read/write serializers."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_serializer_includes_comment_type(self):
        """TaskCommentSerializer output includes comment_type key."""
        from tasks.serializers import TaskCommentSerializer

        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Hello",
            comment_type="proof",
        )
        data = TaskCommentSerializer(comment).data
        self.assertIn("comment_type", data)
        self.assertEqual(data["comment_type"], "proof")

    def test_create_serializer_accepts_comment_type(self):
        """CreateTaskCommentSerializer validates with comment_type='proof'."""
        from tasks.serializers import CreateTaskCommentSerializer

        ser = CreateTaskCommentSerializer(data={
            "author_email": "agent@odin.agent",
            "content": "Execution result",
            "comment_type": "proof",
        })
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["comment_type"], "proof")

    def test_create_serializer_defaults_to_status_update(self):
        """CreateTaskCommentSerializer without comment_type defaults to status_update."""
        from tasks.serializers import CreateTaskCommentSerializer

        ser = CreateTaskCommentSerializer(data={
            "author_email": "agent@odin.agent",
            "content": "Status report",
        })
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["comment_type"], "status_update")

    def test_create_serializer_rejects_invalid_type(self):
        """CreateTaskCommentSerializer rejects comment_type='bogus'."""
        from tasks.serializers import CreateTaskCommentSerializer

        ser = CreateTaskCommentSerializer(data={
            "author_email": "agent@odin.agent",
            "content": "Invalid type",
            "comment_type": "bogus",
        })
        self.assertFalse(ser.is_valid())
        self.assertIn("comment_type", ser.errors)


class TestCommentEndpointTypes(APITestCase):
    """View layer: endpoints set correct comment_type."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_post_comment_with_type(self):
        """POST /comments/ with comment_type='proof' stores it."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "agent@odin.agent",
                "content": "Proof log",
                "comment_type": "proof",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        comment = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(comment.comment_type, "proof")

    def test_post_comment_default_type(self):
        """POST /comments/ without comment_type defaults to status_update."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {
                "author_email": "agent@odin.agent",
                "content": "Status update",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        comment = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(comment.comment_type, "status_update")

    def test_question_endpoint_sets_question_type(self):
        """POST /question/ sets comment_type='question'."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "agent@odin.agent",
                "content": "What database?",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        comment = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(comment.comment_type, "question")

    def test_reply_endpoint_sets_reply_type(self):
        """POST /reply/ sets comment_type='reply'."""
        # First create a question
        q_resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "agent@odin.agent",
                "content": "Which framework?",
            },
            format="json",
        )
        question_id = q_resp.data["id"]

        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {
                "author_email": "human@example.com",
                "content": "Use Django.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        comment = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(comment.comment_type, "reply")

    def test_execution_result_creates_status_update_comment(self):
        """POST /execution_result/ creates comment with comment_type='status_update'."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "All tests passed.",
                    "duration_ms": 1234.5,
                    "agent": "claude",
                },
                "status": "DONE",
                "updated_by": "admin@example.com",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        comment = TaskComment.objects.filter(task=self.task).last()
        self.assertIsNotNone(comment)
        self.assertEqual(comment.comment_type, "status_update")

    def test_filter_comments_by_type(self):
        """GET /comments/?type=question returns only questions."""
        # Create mixed comments
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Status", "comment_type": "status_update"},
            format="json",
        )
        self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "a@b.com", "content": "Question?"},
            format="json",
        )
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Proof", "comment_type": "proof"},
            format="json",
        )

        resp = self.client.get(f"/tasks/{self.task.id}/comments/?type=question")
        self.assertEqual(resp.status_code, 200)
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Question?")

    def test_filter_by_type_combined_with_after(self):
        """GET /comments/?type=proof&after=<id> applies both filters."""
        # Create a status_update first
        r1 = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Status 1"},
            format="json",
        )
        after_id = r1.data["id"]

        # Create proof after
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Proof 1", "comment_type": "proof"},
            format="json",
        )
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Status 2"},
            format="json",
        )

        resp = self.client.get(f"/tasks/{self.task.id}/comments/?type=proof&after={after_id}")
        results = resp.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Proof 1")

    def test_detail_endpoint_includes_comment_types(self):
        """GET /detail/ returns comments with comment_type field."""
        self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "a@b.com", "content": "Hello", "comment_type": "proof"},
            format="json",
        )
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        comments = resp.data["comments"]
        self.assertEqual(len(comments), 1)
        self.assertIn("comment_type", comments[0])
        self.assertEqual(comments[0]["comment_type"], "proof")


class TestBackfillCommentType(APITestCase):
    """Data migration logic: infer comment_type from attachments/content patterns."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def _backfill(self, comment):
        """Apply the backfill logic to a single comment."""
        from tasks.backfill_comment_type import backfill_single_comment
        return backfill_single_comment(comment)

    def test_backfill_question_from_attachments(self):
        """attachment with {"type":"question"} -> comment_type="question"."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="What database?",
            attachments=[{"type": "question", "status": "pending"}],
        )
        result = self._backfill(comment)
        self.assertEqual(result, "question")

    def test_backfill_reply_from_attachments(self):
        """attachment with {"type":"reply"} -> comment_type="reply"."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="human@example.com",
            content="Use PostgreSQL.",
            attachments=[{"type": "reply", "reply_to": 42}],
        )
        result = self._backfill(comment)
        self.assertEqual(result, "reply")

    def test_backfill_execution_pattern_falls_through_to_status_update(self):
        """Content starting with 'Completed in ' -> comment_type="status_update" (no telemetry type)."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Completed in 1.2s\nAll tests passed.",
        )
        result = self._backfill(comment)
        self.assertEqual(result, "status_update")

    def test_backfill_leaves_plain_as_status_update(self):
        """No signals -> stays status_update."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="human@example.com",
            content="Just a plain comment.",
        )
        result = self._backfill(comment)
        self.assertEqual(result, "status_update")
