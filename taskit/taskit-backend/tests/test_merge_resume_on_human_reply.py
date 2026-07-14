"""Tests for human-reply-triggered merge resumption (task #207).

A merge conflict the agent can't classify parks the task in REVIEW with
``metadata.merge_status = "needs_human"`` and posts a blocking QUESTION
comment (task #206). This covers the other half of that flow: once a
human replies, the merge is re-attempted with the reply as guidance —
no operator worktree surgery required.

Three layers:

1. ``resume_merge_on_human_reply`` (tasks/signals.py) — the detection
   signal. Only a genuine human (non-agent, non-system) comment on a
   ``needs_human`` task should dispatch a resume.
2. ``resume_merge_with_guidance`` (tasks/dag_executor.py) — the celery
   task that re-attempts the merge and reports what happened.
3. End-to-end: conflict → explain comment → human reply (via the real
   comment-create API) → merged → TESTING → (human) DONE.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tasks.dag_executor import resume_merge_with_guidance
from tasks.models import CommentType, Task, TaskComment, TaskStatus
from tests.base import APITestCase


class _NeedsHumanTaskMixin:
    def _task_in_review_needs_human(self, extra_metadata=None):
        spec = self.make_spec(self.board, odin_id="sp_207", metadata={"branch": "spec/sp_207"})
        metadata = {"branch": "task/sp_207/99", "merge_status": "needs_human"}
        metadata.update(extra_metadata or {})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW, metadata=metadata,
        )


# ─────────────────────────────────────────────────────────────────────
# Detection: the TaskComment post_save signal
# ─────────────────────────────────────────────────────────────────────

class ResumeMergeSignalTests(_NeedsHumanTaskMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_human_reply_triggers_resume(self, mock_delay):
        task = self._task_in_review_needs_human()

        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="keep the task side", comment_type=CommentType.REPLY,
        )

        mock_delay.assert_called_once_with(task.id, comment.id)

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_agent_comment_does_not_trigger(self, mock_delay):
        task = self._task_in_review_needs_human()

        TaskComment.objects.create(
            task=task, author_email="claude+model@odin.agent",
            content="keep the task side",
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_system_comment_does_not_trigger(self, mock_delay):
        task = self._task_in_review_needs_human()

        TaskComment.objects.create(
            task=task, author_email="system@taskit",
            content="Merge reconciled",
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_merge_agents_own_reask_does_not_loop(self, mock_delay):
        """The merge agent's own comments (its explain comment, its
        follow-up re-ask) must never re-trigger themselves."""
        task = self._task_in_review_needs_human()

        TaskComment.objects.create(
            task=task, author_email="merge-agent@odin",
            content="Merge conflict — needs a human decision",
            comment_type=CommentType.QUESTION,
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_human_comment_on_non_needs_human_task_does_not_trigger(self, mock_delay):
        spec = self.make_spec(self.board, odin_id="sp_other", metadata={"branch": "spec/sp_other"})
        task = self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_other/1", "merge_status": "merged"},
        )

        TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="nice work",
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_human_comment_via_comments_api_triggers_resume(self, mock_delay):
        """The generic POST /tasks/:id/comments/ endpoint (a plain
        top-level reply, not the dedicated Reply action) must also
        trigger resumption — humans don't always use Reply."""
        task = self._task_in_review_needs_human()

        resp = self.client.post(f"/tasks/{task.id}/comments/", {
            "author_email": "alice@test.com",
            "content": "keep both",
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        mock_delay.assert_called_once()
        args, _ = mock_delay.call_args
        self.assertEqual(args[0], task.id)

    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_human_comment_via_reply_api_triggers_resume(self, mock_delay):
        """The dedicated Reply action (answering the QUESTION comment)
        must also trigger resumption."""
        task = self._task_in_review_needs_human()
        question = TaskComment.objects.create(
            task=task, author_email="merge-agent@odin",
            content="Merge conflict — needs a human decision",
            comment_type=CommentType.QUESTION,
        )
        mock_delay.reset_mock()

        resp = self.client.post(
            f"/tasks/{task.id}/comments/{question.id}/reply/",
            {"author_email": "alice@test.com", "content": "accept the task side"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        mock_delay.assert_called_once()


# ─────────────────────────────────────────────────────────────────────
# resume_merge_with_guidance — the celery task
# ─────────────────────────────────────────────────────────────────────

class ResumeMergeWithGuidanceTests(_NeedsHumanTaskMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor._get_worktree_manager", return_value=None)
    @patch("tasks.dag_executor._merge_task_branch")
    def test_successful_guided_merge_advances_to_testing(self, mock_merge_branch, mock_wt):
        from odin.worktree import MergeResult
        task = self._task_in_review_needs_human()
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="keep the task side",
        )
        mock_merge_branch.return_value = MergeResult(
            success=True,
            resolved_files=["src/feature.py"],
            resolution_rationale="`src/feature.py`: task side (per your guidance)",
        )

        resume_merge_with_guidance.__wrapped__(task.id, comment.id)

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("merge_status"), "merged")
        self.assertEqual(task.status, TaskStatus.TESTING)
        self.assertEqual(task.metadata.get("merge_resolution_guidance"), "keep the task side")

        # _merge_task_branch was called with the comment content as guidance.
        mock_merge_branch.assert_called_once_with(task, resolution_guidance="keep the task side")

        latest = TaskComment.objects.filter(task=task).latest("id")
        self.assertIn("keep the task side", latest.content)
        self.assertEqual(latest.comment_type, CommentType.STATUS_UPDATE)

    @patch("tasks.dag_executor._merge_task_branch")
    def test_insufficient_guidance_reasks_and_stays_in_review(self, mock_merge_branch):
        from odin.worktree import MergeResult
        task = self._task_in_review_needs_human()
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="not sure, let me think",
        )
        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True, needs_human=True,
            error="Merge conflict (needs human): guidance insufficient",
            conflicting_files=["src/feature.py"],
            ambiguous_files=["src/feature.py"],
        )

        resume_merge_with_guidance.__wrapped__(task.id, comment.id)

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("merge_status"), "needs_human")
        self.assertEqual(task.status, TaskStatus.REVIEW)

        question = TaskComment.objects.filter(task=task, comment_type=CommentType.QUESTION).latest("id")
        self.assertIn("src/feature.py", question.content)
        self.assertIn("not sure, let me think", question.content)

    @patch("tasks.dag_executor._merge_task_branch")
    def test_skips_if_merge_status_no_longer_needs_human(self, mock_merge_branch):
        """Race guard: if the flag was already cleared (e.g. by the
        watchdog reconciler) before this ran, do nothing."""
        task = self._task_in_review_needs_human(extra_metadata={"merge_status": "merged"})
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="keep both",
        )

        resume_merge_with_guidance.__wrapped__(task.id, comment.id)

        mock_merge_branch.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVIEW)

    @patch("tasks.dag_executor._merge_task_branch")
    def test_missing_comment_is_a_noop(self, mock_merge_branch):
        task = self._task_in_review_needs_human()

        resume_merge_with_guidance.__wrapped__(task.id, 999999)

        mock_merge_branch.assert_not_called()

    @patch("tasks.dag_executor._get_worktree_manager", return_value=None)
    @patch("tasks.dag_executor._merge_task_branch")
    def test_migration_collision_guided_resume_completes_merge(
        self, mock_merge_branch, mock_wt,
    ):
        """Replay 225: a human reply on a migration-collision needs_human
        task drives the bounded ``makemigrations --merge`` fix inside the
        re-attempted merge. The merge completes (merged + TESTING) — no
        operator flag flipping."""
        from odin.worktree import MergeResult
        task = self._task_in_review_needs_human(extra_metadata={
            "merge_error": (
                "Django migration leaf collision in tasks: 2 leaves with no "
                "merge migration — 0049_a, 0049_b."
            ),
        })
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="yes, create the merge migration",
        )
        # The re-attempted merge runs the merge-migration fix and succeeds.
        mock_merge_branch.return_value = MergeResult(
            success=True,
            resolution_rationale=(
                "Created merge migration tying together 0049_a, 0049_b in "
                "tasks via makemigrations --merge."
            ),
        )

        resume_merge_with_guidance.__wrapped__(task.id, comment.id)

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("merge_status"), "merged")
        self.assertEqual(task.status, TaskStatus.TESTING)
        mock_merge_branch.assert_called_once_with(
            task, resolution_guidance="yes, create the merge migration",
        )


# ─────────────────────────────────────────────────────────────────────
# End-to-end (mocked git): conflict → explain → reply → merged → DONE
# ─────────────────────────────────────────────────────────────────────

class EndToEndResumeFlowTests(_NeedsHumanTaskMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor._get_worktree_manager", return_value=None)
    @patch("tasks.dag_executor._merge_task_branch")
    @patch("tasks.dag_executor.resume_merge_with_guidance.delay")
    def test_conflict_explain_reply_merge_done_no_git_commands(
        self, mock_delay, mock_merge_branch, mock_wt,
    ):
        from odin.worktree import MergeResult

        # 1. Task parked needs_human with the merge agent's explain comment
        #    (task #206's flow — reproduced here as the starting state).
        task = self._task_in_review_needs_human()
        TaskComment.objects.create(
            task=task, author_email="merge-agent@odin", comment_type=CommentType.QUESTION,
            content=(
                "Merge conflict — needs a human decision:\n\n"
                "- `src/feature.py`: accept the task side, keep the spec "
                "side, or merge both by hand?"
            ),
        )

        # The re-dispatch call is captured (not sent to a real broker);
        # drive it synchronously to simulate the worker picking it up.
        mock_delay.side_effect = lambda tid, cid: resume_merge_with_guidance.__wrapped__(tid, cid)
        mock_merge_branch.return_value = MergeResult(
            success=True,
            resolved_files=["src/feature.py"],
            resolution_rationale="`src/feature.py`: task side (per your guidance)",
        )

        # 2. Human replies via the real comment-create API — no operator
        #    git/worktree commands anywhere in this test.
        resp = self.client.post(f"/tasks/{task.id}/comments/", {
            "author_email": "alice@test.com",
            "content": "accept the task side",
        }, format="json")
        self.assertEqual(resp.status_code, 201)

        # 3. Merge landed, needs_human cleared, task advanced to TESTING.
        task.refresh_from_db()
        self.assertEqual(task.metadata.get("merge_status"), "merged")
        self.assertEqual(task.status, TaskStatus.TESTING)
        mock_merge_branch.assert_called_once_with(task, resolution_guidance="accept the task side")

        status_comment = TaskComment.objects.filter(
            task=task, comment_type=CommentType.STATUS_UPDATE,
        ).latest("id")
        self.assertIn("accept the task side", status_comment.content)

        # 4. The human's optional housekeeping flip — TESTING → DONE.
        #    The system never sets DONE itself (W5, task #205).
        resp = self.client.put(f"/tasks/{task.id}/", {
            "status": "DONE",
            "updated_by": "alice@test.com",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "DONE")
