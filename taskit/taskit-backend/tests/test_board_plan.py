"""Tests for board-driven planning flow (task #345).

Verifies the full round-trip:
1. ``POST /specs/:id/request-board-plan/`` dispatches the Phase 1 Celery task
2. Phase 1 (``run_board_driven_plan``) runs ``odin plan --board-driven``,
   which posts gate questions as SpecComments and sets ``board_plan_status``
   to ``"awaiting_answers"``
3. A human reply (``POST /specs/:id/comments/``) on an awaiting_answers spec
   triggers the SpecComment signal → dispatches Phase 2 Celery task
4. Phase 2 (``resume_board_driven_plan``) runs ``odin plan --board-resume``,
   which creates tasks on the spec
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock

from tasks.models import CommentType, Spec, SpecComment, Task, TaskStatus
from tests.base import APITestCase


class BoardPlanRequestTests(APITestCase):
    """POST /specs/:id/request-board-plan/ dispatches Phase 1."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(
            self.board, odin_id="sp_345_test",
            title="Board Plan Test",
            content="# Test\nA test spec.",
        )

    @patch("tasks.board_planner.run_board_driven_plan")
    def test_request_board_plan_dispatches_celery(self, mock_task):
        """The endpoint sets metadata and dispatches the Celery task."""
        resp = self.client.post(f"/specs/{self.spec.id}/request-board-plan/", format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")

        self.spec.refresh_from_db()
        self.assertEqual(self.spec.status, Spec.STATUS_PLANNING)
        self.assertEqual(
            self.spec.metadata.get("board_plan_status"), "requested",
        )
        mock_task.delay.assert_called_once_with(self.spec.id)

    @patch("tasks.board_planner.run_board_driven_plan")
    def test_request_board_plan_idempotent_metadata(self, mock_task):
        """Calling twice updates metadata each time."""
        self.client.post(f"/specs/{self.spec.id}/request-board-plan/", format="json")
        self.client.post(f"/specs/{self.spec.id}/request-board-plan/", format="json")

        mock_task.delay.assert_called_with(self.spec.id)


class BoardPlanSignalTests(APITestCase):
    """The SpecComment signal detects human replies on awaiting_answers specs."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(
            self.board, odin_id="sp_345_sig",
            title="Signal Test",
            content="# Signal\nTest.",
            metadata={"board_plan_status": "awaiting_answers"},
        )

    @patch("tasks.board_planner.resume_board_driven_plan")
    def test_human_reply_triggers_resume(self, mock_task):
        """A human SpecComment on an awaiting_answers spec dispatches resume."""
        comment = SpecComment.objects.create(
            spec=self.spec,
            author_email="alice@test.com",
            author_label="Alice",
            content="Use React for the frontend.",
            comment_type=CommentType.REPLY,
        )

        mock_task.delay.assert_called_once_with(self.spec.id, comment.id)

    @patch("tasks.board_planner.resume_board_driven_plan")
    def test_agent_comment_does_not_trigger(self, mock_task):
        """Agent-authored comments must not trigger the signal."""
        SpecComment.objects.create(
            spec=self.spec,
            author_email="odin+planner@odin",
            author_label="odin-planner",
            content="Planning in progress...",
        )

        mock_task.delay.assert_not_called()

    @patch("tasks.board_planner.resume_board_driven_plan")
    def test_system_comment_does_not_trigger(self, mock_task):
        SpecComment.objects.create(
            spec=self.spec,
            author_email="system@taskit",
            author_label="system",
            content="System update",
        )

        mock_task.delay.assert_not_called()

    @patch("tasks.board_planner.resume_board_driven_plan")
    def test_reply_on_non_awaiting_spec_does_not_trigger(self, mock_task):
        """A reply on a spec that isn't awaiting answers is a no-op."""
        other_spec = self.make_spec(
            self.board, odin_id="sp_345_other",
            title="Other",
            metadata={"board_plan_status": "complete"},
        )

        SpecComment.objects.create(
            spec=other_spec,
            author_email="alice@test.com",
            content="Looks good",
            comment_type=CommentType.REPLY,
        )

        mock_task.delay.assert_not_called()

    @patch("tasks.board_planner.resume_board_driven_plan")
    def test_reply_via_api_triggers_resume(self, mock_task):
        """POST /specs/:id/comments/ with a human reply triggers the signal."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/comments/",
            {
                "author_email": "bob@test.com",
                "author_label": "Bob",
                "content": "Go ahead with the plan.",
                "comment_type": "reply",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        mock_task.delay.assert_called_once()


class SpecCommentPostTests(APITestCase):
    """POST /specs/:id/comments/ creates SpecComments."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(self.board, odin_id="sp_345_comment", title="Comment Test")

    def test_post_creates_spec_comment(self):
        resp = self.client.post(
            f"/specs/{self.spec.id}/comments/",
            {
                "author_email": "alice@test.com",
                "content": "My reply",
                "comment_type": "reply",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["content"], "My reply")
        self.assertEqual(resp.data["comment_type"], "reply")

        comment = SpecComment.objects.get(id=resp.data["id"])
        self.assertEqual(comment.spec_id, self.spec.id)
        self.assertEqual(comment.author_email, "alice@test.com")

    def test_post_defaults_comment_type(self):
        resp = self.client.post(
            f"/specs/{self.spec.id}/comments/",
            {"author_email": "alice@test.com", "content": "Hello"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["comment_type"], "status_update")

    def test_get_still_works(self):
        SpecComment.objects.create(
            spec=self.spec, author_email="a@b.com", content="hi",
        )
        resp = self.client.get(f"/specs/{self.spec.id}/comments/")
        self.assertEqual(resp.status_code, 200)


class BoardPlanPlannerTests(APITestCase):
    """Celery tasks run the odin plan subprocess with correct arguments."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(
            self.board, odin_id="sp_345_planner",
            title="Planner Test",
            content="# Plan\nDo things.",
            planner_config={"agent": "claude", "quick": True},
        )

    @patch("tasks.board_planner.subprocess.run")
    @patch("tasks.board_planner._resolve_working_dir", return_value="/tmp")
    def test_phase1_runs_odin_plan_board_driven(self, mock_wd, mock_run):
        """Phase 1 builds the correct odin plan --board-driven command."""
        from tasks.board_planner import run_board_driven_plan, _build_board_plan_command

        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")

        # Test command builder
        cmd = _build_board_plan_command(
            "/tmp/spec.md", self.spec.id, self.spec.planner_config, resume=False,
        )
        self.assertIn("--board-driven", cmd)
        self.assertIn("--board-spec-pk", cmd)
        self.assertIn(str(self.spec.id), cmd)
        self.assertIn("--quiet", cmd)
        self.assertIn("--base-agent", cmd)
        self.assertIn("claude", cmd)
        self.assertIn("--quick", cmd)

        # Test Celery task execution
        run_board_driven_plan(self.spec.id)

        mock_run.assert_called_once()
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("--board-driven", called_cmd)

    @patch("tasks.board_planner.subprocess.run")
    @patch("tasks.board_planner._resolve_working_dir", return_value="/tmp")
    def test_phase2_runs_odin_plan_board_resume(self, mock_wd, mock_run):
        """Phase 2 builds the correct odin plan --board-resume command."""
        from tasks.board_planner import resume_board_driven_plan, _build_board_plan_command

        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")

        # Set up spec in awaiting_answers state
        meta = dict(self.spec.metadata or {})
        meta["board_plan_status"] = "awaiting_answers"
        self.spec.metadata = meta
        self.spec.save()

        # Create a reply comment
        reply = SpecComment.objects.create(
            spec=self.spec,
            author_email="alice@test.com",
            content="Use Python 3.12",
            comment_type=CommentType.REPLY,
        )

        # Test command builder
        cmd = _build_board_plan_command(
            "/tmp/spec.md", self.spec.id, self.spec.planner_config,
            resume=True, reply_comment_id=reply.id,
            spec_odin_id=self.spec.odin_id, reply_file="/tmp/reply.txt",
        )
        self.assertIn("--board-resume", cmd)
        self.assertIn("--no-gate", cmd)
        self.assertIn("--board-spec-id", cmd)
        self.assertIn(self.spec.odin_id, cmd)
        self.assertIn("--reply-file", cmd)
        self.assertIn("/tmp/reply.txt", cmd)

        # Test Celery task execution
        resume_board_driven_plan(self.spec.id, reply.id)

        mock_run.assert_called_once()
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("--board-resume", called_cmd)
        self.assertIn("--board-spec-id", called_cmd)

    @patch("tasks.board_planner.subprocess.run")
    @patch("tasks.board_planner._resolve_working_dir", return_value="/tmp")
    def test_phase1_failure_marks_planning_failed(self, mock_wd, mock_run):
        """If Phase 1 subprocess fails, spec is marked planning_failed."""
        from tasks.board_planner import run_board_driven_plan

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        run_board_driven_plan(self.spec.id)

        self.spec.refresh_from_db()
        self.assertEqual(self.spec.status, Spec.STATUS_PLANNING_FAILED)
        self.assertEqual(
            self.spec.metadata.get("board_plan_status"), "error",
        )

        comments = SpecComment.objects.filter(spec=self.spec)
        self.assertTrue(comments.exists())
        self.assertIn("failed", comments.first().content.lower())

    @patch("tasks.board_planner.subprocess.run")
    @patch("tasks.board_planner._resolve_working_dir", return_value="/tmp")
    def test_phase2_skips_if_not_awaiting_answers(self, mock_wd, mock_run):
        """Phase 2 is a no-op if spec is not in awaiting_answers state."""
        from tasks.board_planner import resume_board_driven_plan

        meta = dict(self.spec.metadata or {})
        meta["board_plan_status"] = "complete"
        self.spec.metadata = meta
        self.spec.save()

        reply = SpecComment.objects.create(
            spec=self.spec, author_email="a@b.com", content="reply",
        )

        resume_board_driven_plan(self.spec.id, reply.id)

        mock_run.assert_not_called()


class BoardPlanEndToEndTests(APITestCase):
    """End-to-end: request → questions posted → reply → tasks created.

    Mocks the odin subprocess to simulate what odin would do:
    - Phase 1: posts SpecComment questions, sets awaiting_answers
    - Phase 2: creates Task objects, marks planning complete
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(
            self.board, odin_id="sp_345_e2e",
            title="E2E Test",
            content="# E2E\nA test spec for the full round-trip.",
        )

    def test_full_round_trip(self):
        """Simulates the complete board-driven plan round-trip."""
        from tasks.board_planner import run_board_driven_plan, resume_board_driven_plan

        def phase1_side_effect(cmd, **kwargs):
            """Simulate odin posting gate questions to the spec."""
            # Find the spec PK from the command
            pk_idx = cmd.index("--board-spec-pk") + 1
            spec_pk = int(cmd[pk_idx])

            spec = Spec.objects.get(pk=spec_pk)
            SpecComment.objects.create(
                spec=spec,
                author_email="odin+planner@odin",
                author_label="odin-planner",
                content="**Planning Summary**\n\nThis spec needs 2 tasks.",
                comment_type=CommentType.PLANNING,
            )
            SpecComment.objects.create(
                spec=spec,
                author_email="odin+planner@odin",
                author_label="odin-planner",
                content=(
                    "The planner has questions before breaking down tasks.\n\n"
                    "**Q1:** Which framework?\n\n**Q2:** Any dependencies?"
                ),
                comment_type=CommentType.QUESTION,
            )
            meta = dict(spec.metadata or {})
            meta["board_plan_status"] = "awaiting_answers"
            spec.metadata = meta
            spec.save()

            return MagicMock(returncode=0, stdout="posted", stderr="")

        def phase2_side_effect(cmd, **kwargs):
            """Simulate odin creating tasks on the spec."""
            pk_idx = cmd.index("--board-spec-pk") + 1
            spec_pk = int(cmd[pk_idx])

            spec = Spec.objects.get(pk=spec_pk)
            Task.objects.create(
                board=spec.board,
                spec=spec,
                title="Implement the backend API",
                created_by="odin+planner@odin",
                status=TaskStatus.TODO,
                metadata={"suggested_agent": "claude"},
            )
            Task.objects.create(
                board=spec.board,
                spec=spec,
                title="Build the frontend UI",
                created_by="odin+planner@odin",
                status=TaskStatus.TODO,
                metadata={"suggested_agent": "claude"},
            )
            spec.status = Spec.STATUS_PLANNING_COMPLETE
            meta = dict(spec.metadata or {})
            meta["board_plan_status"] = "complete"
            spec.metadata = meta
            spec.save()

            SpecComment.objects.create(
                spec=spec,
                author_email="odin+planner@odin",
                author_label="odin-planner",
                content="Task breakdown complete. 2 tasks created.",
                comment_type=CommentType.STATUS_UPDATE,
            )

            return MagicMock(returncode=0, stdout="done", stderr="")

        # ── Step 1: Request board plan ────────────────────────────────
        with patch("tasks.board_planner.run_board_driven_plan") as mock_view_task:
            mock_view_task.delay.side_effect = lambda pk: run_board_driven_plan(pk)

            with patch("tasks.board_planner.subprocess.run", side_effect=phase1_side_effect):
                with patch("tasks.board_planner._resolve_working_dir", return_value="/tmp"):
                    resp = self.client.post(
                        f"/specs/{self.spec.id}/request-board-plan/", format="json",
                    )
                    self.assertEqual(resp.status_code, 200)

        # ── Verify: questions posted as SpecComments ─────────────────
        self.spec.refresh_from_db()
        self.assertEqual(
            self.spec.metadata.get("board_plan_status"), "awaiting_answers",
        )
        comments = SpecComment.objects.filter(spec=self.spec)
        self.assertGreaterEqual(comments.count(), 2)
        question_comments = comments.filter(comment_type=CommentType.QUESTION)
        self.assertEqual(question_comments.count(), 1)
        self.assertIn("framework", question_comments.first().content.lower())

        # ── Step 2: Human replies ─────────────────────────────────────
        with patch("tasks.board_planner.resume_board_driven_plan") as mock_signal_task:
            mock_signal_task.delay.side_effect = lambda spk, cid: resume_board_driven_plan(spk, cid)

            with patch("tasks.board_planner.subprocess.run", side_effect=phase2_side_effect):
                with patch("tasks.board_planner._resolve_working_dir", return_value="/tmp"):
                    reply_resp = self.client.post(
                        f"/specs/{self.spec.id}/comments/",
                        {
                            "author_email": "operator@test.com",
                            "author_label": "Operator",
                            "content": "Use FastAPI. No extra dependencies.",
                            "comment_type": "reply",
                        },
                        format="json",
                    )
                    self.assertEqual(reply_resp.status_code, 201)

        # ── Verify: tasks created on the spec ────────────────────────
        self.spec.refresh_from_db()
        self.assertEqual(self.spec.status, Spec.STATUS_PLANNING_COMPLETE)
        self.assertEqual(
            self.spec.metadata.get("board_plan_status"), "complete",
        )
        tasks = Task.objects.filter(spec=self.spec)
        self.assertEqual(tasks.count(), 2)
        titles = {t.title for t in tasks}
        self.assertIn("Implement the backend API", titles)
        self.assertIn("Build the frontend UI", titles)

        # ── Verify: the full comment thread ──────────────────────────
        all_comments = list(SpecComment.objects.filter(spec=self.spec).order_by("created_at"))
        comment_types = [c.comment_type for c in all_comments]
        self.assertIn(CommentType.PLANNING, comment_types)
        self.assertIn(CommentType.QUESTION, comment_types)
        self.assertIn(CommentType.REPLY, comment_types)
