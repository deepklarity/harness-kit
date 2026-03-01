"""Tests for SpecComment model and planning trace endpoints.

Covers:
- SpecComment model CRUD
- SpecComment inclusion in SpecSerializer
- POST /specs/:id/planning_result/ endpoint
- GET /specs/:id/comments/ endpoint
"""

from .base import APITestCase
from tasks.models import Spec, SpecComment, CommentType


class TestSpecCommentModel(APITestCase):
    """SpecComment model creation and basic behavior."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    def test_create_spec_comment(self):
        """SpecComment can be created with all required fields."""
        comment = SpecComment.objects.create(
            spec=self.spec,
            author_email="claude+claude-opus-4-6@odin.agent",
            author_label="claude (claude-opus-4-6)",
            content="Planning completed successfully.",
            comment_type=CommentType.PLANNING,
        )
        self.assertEqual(comment.spec_id, self.spec.id)
        self.assertEqual(comment.author_email, "claude+claude-opus-4-6@odin.agent")
        self.assertEqual(comment.comment_type, "planning")
        self.assertIsNotNone(comment.created_at)

    def test_spec_comment_default_attachments(self):
        """attachments defaults to empty list."""
        comment = SpecComment.objects.create(
            spec=self.spec,
            author_email="test@example.com",
            content="Test content",
        )
        self.assertEqual(comment.attachments, [])

    def test_spec_comment_cascade_delete(self):
        """Deleting a spec cascades to its comments."""
        SpecComment.objects.create(
            spec=self.spec,
            author_email="test@example.com",
            content="Will be deleted",
        )
        self.assertEqual(SpecComment.objects.count(), 1)
        self.spec.delete()
        self.assertEqual(SpecComment.objects.count(), 0)

    def test_spec_comment_ordering(self):
        """Comments are ordered by created_at ascending."""
        c1 = SpecComment.objects.create(
            spec=self.spec,
            author_email="a@test.com",
            content="First",
        )
        c2 = SpecComment.objects.create(
            spec=self.spec,
            author_email="b@test.com",
            content="Second",
        )
        comments = list(SpecComment.objects.filter(spec=self.spec))
        self.assertEqual(comments[0].id, c1.id)
        self.assertEqual(comments[1].id, c2.id)


class TestSpecCommentInSerializer(APITestCase):
    """SpecSerializer includes comments field."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    def test_spec_detail_includes_comments(self):
        """GET /specs/:id/ includes comments array."""
        SpecComment.objects.create(
            spec=self.spec,
            author_email="claude@odin.agent",
            author_label="claude",
            content="Planned 3 tasks in 45s.",
            comment_type=CommentType.PLANNING,
        )
        resp = self.client.get(f"/specs/{self.spec.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("comments", resp.data)
        self.assertEqual(len(resp.data["comments"]), 1)
        comment = resp.data["comments"][0]
        self.assertEqual(comment["author_email"], "claude@odin.agent")
        self.assertEqual(comment["content"], "Planned 3 tasks in 45s.")
        self.assertEqual(comment["comment_type"], "planning")

    def test_spec_detail_empty_comments(self):
        """GET /specs/:id/ returns empty comments array when none exist."""
        resp = self.client.get(f"/specs/{self.spec.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("comments", resp.data)
        self.assertEqual(resp.data["comments"], [])


class TestPlanningResultEndpoint(APITestCase):
    """POST /specs/:id/planning_result/ — stores trace and creates comment."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    def test_planning_result_creates_comment(self):
        """POST creates a SpecComment with planning type."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "Exploring codebase...\nAnalyzing spec...\nPlan: 3 tasks.",
                "duration_ms": 45000,
                "agent": "claude",
                "model": "claude-opus-4-6",
                "effective_input": "Plan this spec: build a login page...",
                "success": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Comment created
        comments = SpecComment.objects.filter(spec=self.spec)
        self.assertEqual(comments.count(), 1)
        comment = comments.first()
        self.assertEqual(comment.comment_type, "planning")
        self.assertIn("45", comment.content)  # Duration should appear

    def test_planning_result_stores_metadata(self):
        """POST stores planning trace data in spec.metadata."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "Full agent trace here...",
                "duration_ms": 30000,
                "agent": "claude",
                "model": "claude-opus-4-6",
                "effective_input": "Plan prompt...",
                "success": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.spec.refresh_from_db()
        self.assertIn("planning_trace", self.spec.metadata)
        trace = self.spec.metadata["planning_trace"]
        self.assertEqual(trace["agent"], "claude")
        self.assertEqual(trace["model"], "claude-opus-4-6")
        self.assertEqual(trace["duration_ms"], 30000)
        self.assertTrue(trace["success"])

    def test_planning_result_failure(self):
        """POST with success=false still stores trace."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "Error: agent crashed.",
                "duration_ms": 5000,
                "agent": "claude",
                "model": "claude-opus-4-6",
                "effective_input": "Plan prompt...",
                "success": False,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        comments = SpecComment.objects.filter(spec=self.spec)
        self.assertEqual(comments.count(), 1)
        comment = comments.first()
        self.assertIn("Failed", comment.content)

    def test_planning_result_invalid_spec_404(self):
        """POST to nonexistent spec returns 404."""
        resp = self.client.post(
            "/specs/99999/planning_result/",
            {
                "raw_output": "trace...",
                "duration_ms": 1000,
                "agent": "claude",
                "model": "claude-opus-4-6",
                "effective_input": "prompt...",
                "success": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_planning_result_missing_fields_400(self):
        """POST without required fields returns 400."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {"raw_output": "trace..."},  # Missing required fields
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_planning_result_empty_output(self):
        """POST with empty raw_output still creates comment."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "",
                "duration_ms": 2000,
                "agent": "claude",
                "model": "claude-opus-4-6",
                "effective_input": "prompt...",
                "success": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SpecComment.objects.filter(spec=self.spec).count(), 1)


class TestSpecCommentsListEndpoint(APITestCase):
    """GET /specs/:id/comments/ — list spec comments."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    def test_list_comments_empty(self):
        """GET returns empty results when no comments exist."""
        resp = self.client.get(f"/specs/{self.spec.id}/comments/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)
        self.assertEqual(resp.data["results"], [])

    def test_list_comments_with_data(self):
        """GET returns all spec comments."""
        SpecComment.objects.create(
            spec=self.spec,
            author_email="claude@odin.agent",
            content="Planning trace 1",
            comment_type=CommentType.PLANNING,
        )
        SpecComment.objects.create(
            spec=self.spec,
            author_email="claude@odin.agent",
            content="Planning trace 2",
            comment_type=CommentType.PLANNING,
        )
        resp = self.client.get(f"/specs/{self.spec.id}/comments/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 2)

    def test_list_comments_invalid_spec_404(self):
        """GET for nonexistent spec returns 404."""
        resp = self.client.get("/specs/99999/comments/")
        self.assertEqual(resp.status_code, 404)
