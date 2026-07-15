"""Evidence-generating test: full board-driven plan round-trip.

Runs the complete request → questions → reply → tasks-created flow with a
stubbed odin subprocess (the subprocess simulates what the real odin CLI
would do: post SpecComments in Phase 1, create Tasks in Phase 2). Prints the
spec comment thread and created tasks to stdout so the output can be captured
as proof.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock

from tasks.models import CommentType, Spec, SpecComment, Task, TaskStatus
from tests.base import APITestCase


class BoardPlanProofTest(APITestCase):
    """Full round-trip proof — prints evidence to stdout."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp")
        self.spec = self.make_spec(
            self.board,
            odin_id="sp_345_proof",
            title="Proof Round-Trip Spec",
            content=(
                "# Feature: Markdown Notes\n\n"
                "Add a markdown notes feature to the task board.\n"
                "Users should be able to attach notes to any task."
            ),
        )

    def test_full_round_trip_evidence(self):
        from tasks.board_planner import run_board_driven_plan, resume_board_driven_plan

        print("\n" + "=" * 70)
        print("BOARD-DRIVEN PLAN ROUND-TRIP PROOF")
        print("=" * 70)
        print(f"\nSpec: #{self.spec.id}  odin_id={self.spec.odin_id}")
        print(f"Title: {self.spec.title}")
        print(f"Board: #{self.board.id}  working_dir={self.board.working_dir}")
        print(f"Initial status: {self.spec.status}")

        # ── Phase 1 stub: odin posts gate questions ───────────────────
        def phase1_side_effect(cmd, **kwargs):
            pk_idx = cmd.index("--board-spec-pk") + 1
            spec_pk = int(cmd[pk_idx])
            spec = Spec.objects.get(pk=spec_pk)

            SpecComment.objects.create(
                spec=spec,
                author_email="odin+planner@odin",
                author_label="odin-planner",
                content=(
                    "**Planning Summary**\n\n"
                    "A markdown notes feature with: a Note model, a REST API "
                    "endpoint, and a frontend editor component."
                ),
                comment_type=CommentType.PLANNING,
            )
            SpecComment.objects.create(
                spec=spec,
                author_email="odin+planner@odin",
                author_label="odin-planner",
                content=(
                    "The planner has questions before breaking down tasks. "
                    "Reply on this spec with your answers and planning will "
                    "continue automatically.\n\n"
                    "**Q1:** Should notes support inline images?\n\n"
                    "**Q2:** Is there a max note length?"
                ),
                comment_type=CommentType.QUESTION,
            )
            meta = dict(spec.metadata or {})
            meta["board_plan_status"] = "awaiting_answers"
            spec.metadata = meta
            spec.save()
            return MagicMock(returncode=0, stdout="posted", stderr="")

        # ── Phase 2 stub: odin creates tasks ──────────────────────────
        def phase2_side_effect(cmd, **kwargs):
            pk_idx = cmd.index("--board-spec-pk") + 1
            spec_pk = int(cmd[pk_idx])
            spec = Spec.objects.get(pk=spec_pk)

            Task.objects.create(
                board=spec.board, spec=spec,
                title="Add Note model + migration",
                created_by="odin+planner@odin",
                status=TaskStatus.TODO,
                metadata={"suggested_agent": "claude", "complexity": "low"},
            )
            Task.objects.create(
                board=spec.board, spec=spec,
                title="Add Note REST API (CRUD)",
                created_by="odin+planner@odin",
                status=TaskStatus.TODO,
                metadata={"suggested_agent": "claude", "complexity": "medium"},
            )
            Task.objects.create(
                board=spec.board, spec=spec,
                title="Add markdown editor component",
                created_by="odin+planner@odin",
                status=TaskStatus.TODO,
                depends_on=[],
                metadata={"suggested_agent": "claude", "complexity": "medium"},
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
                content="Task breakdown complete. 3 tasks created.",
                comment_type=CommentType.STATUS_UPDATE,
            )
            return MagicMock(returncode=0, stdout="done", stderr="")

        # ── Step 1: Request board plan via API ────────────────────────
        print("\n--- STEP 1: POST /specs/{}/request-board-plan/ ---".format(self.spec.id))

        with patch("tasks.board_planner.run_board_driven_plan") as mock_view_task:
            mock_view_task.delay.side_effect = lambda pk: run_board_driven_plan(pk)
            with patch("tasks.board_planner.subprocess.run", side_effect=phase1_side_effect):
                with patch("tasks.board_planner._resolve_working_dir", return_value="/tmp"):
                    resp = self.client.post(
                        f"/specs/{self.spec.id}/request-board-plan/", format="json",
                    )
        print(f"  Response: {resp.status_code} → {resp.data}")

        # ── Verify Phase 1 ────────────────────────────────────────────
        self.spec.refresh_from_db()
        print(f"\n  spec.status = {self.spec.status}")
        print(f"  spec.metadata.board_plan_status = {self.spec.metadata.get('board_plan_status')}")

        comments_after_p1 = SpecComment.objects.filter(spec=self.spec).order_by("created_at")
        print(f"\n  SpecComments after Phase 1: {comments_after_p1.count()}")
        for c in comments_after_p1:
            preview = c.content[:80].replace("\n", " ")
            print(f"    [{c.comment_type}] {c.author_label}: {preview}...")

        self.assertEqual(self.spec.metadata.get("board_plan_status"), "awaiting_answers")
        self.assertGreaterEqual(comments_after_p1.count(), 2)

        # ── Step 2: Human replies via API ─────────────────────────────
        print(f"\n--- STEP 2: POST /specs/{self.spec.id}/comments/ (human reply) ---")

        with patch("tasks.board_planner.resume_board_driven_plan") as mock_signal_task:
            mock_signal_task.delay.side_effect = lambda spk, cid: resume_board_driven_plan(spk, cid)
            with patch("tasks.board_planner.subprocess.run", side_effect=phase2_side_effect):
                with patch("tasks.board_planner._resolve_working_dir", return_value="/tmp"):
                    reply_resp = self.client.post(
                        f"/specs/{self.spec.id}/comments/",
                        {
                            "author_email": "operator@test.com",
                            "author_label": "Operator",
                            "content": "Yes, support inline images. Max 10000 chars.",
                            "comment_type": "reply",
                        },
                        format="json",
                    )
        print(f"  Response: {reply_resp.status_code} → comment #{reply_resp.data.get('id')}")

        # ── Verify Phase 2 ────────────────────────────────────────────
        self.spec.refresh_from_db()
        tasks = Task.objects.filter(spec=self.spec).order_by("id")
        print(f"\n  spec.status = {self.spec.status}")
        print(f"  spec.metadata.board_plan_status = {self.spec.metadata.get('board_plan_status')}")
        print(f"\n  Tasks created: {tasks.count()}")
        for t in tasks:
            agent = (t.metadata or {}).get("suggested_agent", "?")
            complexity = (t.metadata or {}).get("complexity", "?")
            print(f"    #{t.id} [{t.status}] {t.title}  (agent={agent}, complexity={complexity})")

        # ── Full comment thread ───────────────────────────────────────
        all_comments = SpecComment.objects.filter(spec=self.spec).order_by("created_at")
        print(f"\n--- FULL SPEC THREAD ({all_comments.count()} comments) ---")
        for c in all_comments:
            print(f"\n  ┌─ [{c.comment_type.upper()}] {c.author_label} <{c.author_email}>")
            for line in c.content.split("\n"):
                print(f"  │ {line}")
            print(f"  └─")

        # ── Assertions ────────────────────────────────────────────────
        self.assertEqual(self.spec.status, Spec.STATUS_PLANNING_COMPLETE)
        self.assertEqual(self.spec.metadata.get("board_plan_status"), "complete")
        self.assertEqual(tasks.count(), 3)

        comment_types = [c.comment_type for c in all_comments]
        self.assertIn(CommentType.PLANNING, comment_types)
        self.assertIn(CommentType.QUESTION, comment_types)
        self.assertIn(CommentType.REPLY, comment_types)
        self.assertIn(CommentType.STATUS_UPDATE, comment_types)

        print("\n" + "=" * 70)
        print("ROUND-TRIP PROOF: ALL ASSERTIONS PASSED")
        print("=" * 70 + "\n")
