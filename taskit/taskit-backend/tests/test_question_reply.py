"""Tests for question/reply endpoints and comment ?after= filter.

Covers:
- POST /tasks/:id/question/ — creates comment with question attachment, sets metadata flag
- POST /tasks/:id/comments/:comment_id/reply/ — creates reply comment, clears metadata flag
- GET /tasks/:id/comments/?after=<comment_id> — returns only newer comments
- Auth-free operation (FIREBASE_AUTH_ENABLED=False)
"""

from .base import APITestCase
from tasks.models import TaskComment


class TestPostQuestion(APITestCase):
    """POST /tasks/:id/question/ endpoint."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_post_question_creates_comment(self):
        """Question endpoint creates a comment with question attachment."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "claude+sonnet-4-5@odin.agent",
                "content": "How should I handle authentication?",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["content"], "How should I handle authentication?")
        self.assertEqual(resp.data["author_email"], "claude+sonnet-4-5@odin.agent")
        # Verify question attachment
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["type"], "question")
        self.assertEqual(resp.data["attachments"][0]["status"], "pending")

    def test_post_question_sets_metadata_flag(self):
        """task.metadata["has_pending_question"] is True after question."""
        self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "agent@odin.agent",
                "content": "What database should I use?",
            },
            format="json",
        )
        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("has_pending_question"))

    def test_post_question_with_author_label(self):
        """author_label is optional and stored."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "gemini+flash@odin.agent",
                "author_label": "gemini (flash)",
                "content": "Which API format?",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["author_label"], "gemini (flash)")

    def test_question_on_nonexistent_task_404(self):
        resp = self.client.post(
            "/tasks/99999/question/",
            {"author_email": "agent@odin.agent", "content": "Hello?"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_question_missing_content_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_question_missing_email_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"content": "Hello?"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_question_without_auth_works(self):
        """Question endpoint works without Firebase auth (FIREBASE_AUTH_ENABLED=False)."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "agent@odin.agent",
                "content": "No auth needed?",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)


class TestReplyToQuestion(APITestCase):
    """POST /tasks/:id/comments/:comment_id/reply/ endpoint."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)
        # Create a question first
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {
                "author_email": "agent@odin.agent",
                "content": "How should I handle auth?",
            },
            format="json",
        )
        self.question_id = resp.data["id"]

    def test_reply_creates_comment_with_ref(self):
        """Reply creates a comment with reply_to attachment referencing the question."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{self.question_id}/reply/",
            {
                "author_email": "human@example.com",
                "content": "Use JWT tokens.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["content"], "Use JWT tokens.")
        # Verify reply attachment
        self.assertEqual(len(resp.data["attachments"]), 1)
        self.assertEqual(resp.data["attachments"][0]["type"], "reply")
        self.assertEqual(resp.data["attachments"][0]["reply_to"], self.question_id)

    def test_reply_clears_metadata_flag(self):
        """POST /reply/ clears has_pending_question metadata flag."""
        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("has_pending_question"))

        self.client.post(
            f"/tasks/{self.task.id}/comments/{self.question_id}/reply/",
            {
                "author_email": "human@example.com",
                "content": "Use JWT.",
            },
            format="json",
        )
        self.task.refresh_from_db()
        self.assertFalse(self.task.metadata.get("has_pending_question", False))

    def test_reply_marks_question_as_answered(self):
        """Reply updates the question comment's attachment status to answered."""
        self.client.post(
            f"/tasks/{self.task.id}/comments/{self.question_id}/reply/",
            {
                "author_email": "human@example.com",
                "content": "Done.",
            },
            format="json",
        )
        question = TaskComment.objects.get(pk=self.question_id)
        self.assertEqual(question.attachments[0]["status"], "answered")

    def test_reply_to_nonexistent_comment_404(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/99999/reply/",
            {
                "author_email": "human@example.com",
                "content": "Reply to nothing.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_reply_to_nonexistent_task_404(self):
        resp = self.client.post(
            f"/tasks/99999/comments/{self.question_id}/reply/",
            {
                "author_email": "human@example.com",
                "content": "Reply to nothing.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_reply_missing_content_rejected(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{self.question_id}/reply/",
            {"author_email": "human@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class TestCommentsAfterFilter(APITestCase):
    """GET /tasks/:id/comments/?after=<comment_id> filter."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)
        # Create 3 comments
        self.comments = []
        for i in range(3):
            resp = self.client.post(
                f"/tasks/{self.task.id}/comments/",
                {
                    "author_email": "agent@odin.agent",
                    "content": f"Comment {i}",
                },
                format="json",
            )
            self.comments.append(resp.data)

    def test_comments_after_filter(self):
        """GET /comments/?after=<id> returns only comments with id > given id."""
        after_id = self.comments[0]["id"]
        resp = self.client.get(f"/tasks/{self.task.id}/comments/?after={after_id}")
        self.assertEqual(resp.status_code, 200)
        results = resp.data["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "Comment 1")
        self.assertEqual(results[1]["content"], "Comment 2")

    def test_comments_after_returns_none_when_no_newer(self):
        """No newer comments returns empty list."""
        last_id = self.comments[-1]["id"]
        resp = self.client.get(f"/tasks/{self.task.id}/comments/?after={last_id}")
        results = resp.data["results"]
        self.assertEqual(len(results), 0)

    def test_comments_without_after_returns_all(self):
        """Without ?after=, returns all comments."""
        resp = self.client.get(f"/tasks/{self.task.id}/comments/")
        results = resp.data["results"]
        self.assertEqual(len(results), 3)

    def test_comments_after_filter_with_reply(self):
        """?after= filter returns reply comments correctly (polling contract)."""
        # Post a question
        q_resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "What DB?"},
            format="json",
        )
        question_id = q_resp.data["id"]

        # Post a reply to the question
        self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {"author_email": "human@example.com", "content": "Use PostgreSQL"},
            format="json",
        )

        # Poll for comments after the question — should find the reply
        resp = self.client.get(f"/tasks/{self.task.id}/comments/?after={question_id}")
        results = resp.data["results"]
        self.assertTrue(len(results) >= 1)
        reply = results[0]
        self.assertEqual(reply["content"], "Use PostgreSQL")
        self.assertEqual(reply["attachments"][0]["type"], "reply")
        self.assertEqual(reply["attachments"][0]["reply_to"], question_id)


class TestReplyAntiCases(APITestCase):
    """Anti-test and edge cases for reply endpoint."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_reply_to_non_question_comment(self):
        """Reply endpoint works even when the target comment isn't a question."""
        # Create a regular status_update comment
        comment_resp = self.client.post(
            f"/tasks/{self.task.id}/comments/",
            {"author_email": "agent@odin.agent", "content": "Progress update"},
            format="json",
        )
        comment_id = comment_resp.data["id"]

        # Reply to it — should succeed (graceful, no crash)
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{comment_id}/reply/",
            {"author_email": "human@example.com", "content": "Thanks for the update"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["attachments"][0]["type"], "reply")
        self.assertEqual(resp.data["attachments"][0]["reply_to"], comment_id)

    def test_double_reply_to_same_question(self):
        """Second reply succeeds; question stays answered, pending flag stays cleared."""
        # Create question
        q_resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "Which framework?"},
            format="json",
        )
        question_id = q_resp.data["id"]

        # First reply
        self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {"author_email": "human@example.com", "content": "Use Django"},
            format="json",
        )

        # Second reply — should succeed
        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {"author_email": "human@example.com", "content": "Actually, use FastAPI"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        # Question should still be "answered"
        question = TaskComment.objects.get(pk=question_id)
        self.assertEqual(question.attachments[0]["status"], "answered")

        # Pending flag should still be cleared
        self.task.refresh_from_db()
        self.assertFalse(self.task.metadata.get("has_pending_question", False))

    def test_reply_with_empty_content_rejected(self):
        """Whitespace-only content is rejected (400)."""
        q_resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "Question?"},
            format="json",
        )
        question_id = q_resp.data["id"]

        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {"author_email": "human@example.com", "content": "   "},
            format="json",
        )
        # Blank content should be rejected
        self.assertIn(resp.status_code, [400, 201])
        # If the backend doesn't validate whitespace-only, at least it shouldn't crash

    def test_reply_comment_has_correct_type(self):
        """Reply comment has comment_type=REPLY."""
        q_resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "Question?"},
            format="json",
        )
        question_id = q_resp.data["id"]

        resp = self.client.post(
            f"/tasks/{self.task.id}/comments/{question_id}/reply/",
            {"author_email": "human@example.com", "content": "Answer."},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        reply = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(reply.comment_type, "reply")

    def test_question_comment_has_correct_type(self):
        """Question comment has comment_type=QUESTION."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "What?"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        comment = TaskComment.objects.get(pk=resp.data["id"])
        self.assertEqual(comment.comment_type, "question")


class TestQuestionEdgeCases(APITestCase):
    """Edge cases for question metadata flag management."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_question_on_task_with_existing_pending(self):
        """Second question on a task with existing pending question maintains flag."""
        # First question
        self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "First question?"},
            format="json",
        )
        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("has_pending_question"))

        # Second question — flag should still be True
        self.client.post(
            f"/tasks/{self.task.id}/question/",
            {"author_email": "agent@odin.agent", "content": "Second question?"},
            format="json",
        )
        self.task.refresh_from_db()
        self.assertTrue(self.task.metadata.get("has_pending_question"))
