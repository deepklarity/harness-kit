"""Tests for human-reply-triggered hard-action resumption (task #244).

Task #244's reversibility gate: deleting a task branch that still carries
unmerged work is classified HARD (``odin.reversibility``) and parks with
``metadata.hard_action_status = "needs_human"`` instead of running
``git branch -D`` autonomously (see ``_cleanup_task_worktree`` in
``tasks/dag_executor.py``). This covers the round trip — park → human
reply → resume — reusing the exact same TaskComment-signal mechanism the
merge flow's ``resume_merge_on_human_reply`` already uses (task #207).

Three layers, mirroring ``test_merge_resume_on_human_reply.py``:

1. ``resume_hard_action_on_human_reply`` (tasks/signals.py) — detection.
   Only a genuine human (non-agent, non-system) comment on a
   ``hard_action_status=needs_human`` task dispatches a resume.
2. ``resume_hard_action_with_reply`` (tasks/dag_executor.py) — the celery
   task that carries out (or cancels) the parked action.
3. End-to-end: park → explain comment → human reply → branch deleted.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tasks.dag_executor import _cleanup_task_worktree, resume_hard_action_with_reply
from tasks.models import CommentType, Task, TaskComment, TaskStatus
from tests.base import APITestCase


class _ParkedHardActionTaskMixin:
    def _task_parked_needs_human(self, extra_metadata=None):
        spec = self.make_spec(self.board, odin_id="sp_244", metadata={"branch": "spec/sp_244"})
        metadata = {
            "branch": "task/sp_244/7",
            "hard_action_status": "needs_human",
            "hard_action": {
                "key": "delete_branch_with_unmerged_work",
                "branch": "task/sp_244/7",
            },
        }
        metadata.update(extra_metadata or {})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING, metadata=metadata,
        )


# ─────────────────────────────────────────────────────────────────────
# Detection: the TaskComment post_save signal
# ─────────────────────────────────────────────────────────────────────

class ResumeHardActionSignalTests(_ParkedHardActionTaskMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_human_reply_triggers_resume(self, mock_delay):
        task = self._task_parked_needs_human()

        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="delete it", comment_type=CommentType.REPLY,
        )

        mock_delay.assert_called_once_with(task.id, comment.id)

    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_agent_comment_does_not_trigger(self, mock_delay):
        task = self._task_parked_needs_human()

        TaskComment.objects.create(
            task=task, author_email="claude+model@odin.agent", content="delete it",
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_merge_agents_own_question_does_not_loop(self, mock_delay):
        """The parking comment itself (posted by merge-agent@odin) must
        never re-trigger the resume it's explaining."""
        task = self._task_parked_needs_human()

        TaskComment.objects.create(
            task=task, author_email="merge-agent@odin",
            content="Hard action parked — needs a human decision",
            comment_type=CommentType.QUESTION,
        )

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_human_comment_on_non_parked_task_does_not_trigger(self, mock_delay):
        spec = self.make_spec(self.board, odin_id="sp_other", metadata={"branch": "spec/sp_other"})
        task = self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING,
            metadata={"branch": "task/sp_other/1"},
        )

        TaskComment.objects.create(task=task, author_email="alice@test.com", content="ok")

        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_human_comment_via_comments_api_triggers_resume(self, mock_delay):
        task = self._task_parked_needs_human()

        resp = self.client.post(f"/tasks/{task.id}/comments/", {
            "author_email": "alice@test.com",
            "content": "keep it for now",
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        mock_delay.assert_called_once()
        args, _ = mock_delay.call_args
        self.assertEqual(args[0], task.id)


# ─────────────────────────────────────────────────────────────────────
# resume_hard_action_with_reply — the celery task
# ─────────────────────────────────────────────────────────────────────

class ResumeHardActionWithReplyTests(_ParkedHardActionTaskMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor._get_worktree_manager")
    def test_proceed_reply_deletes_branch_and_clears_park(self, mock_get_wm):
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        task = self._task_parked_needs_human()
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="delete",
        )

        resume_hard_action_with_reply.__wrapped__(task.id, comment.id)

        mock_wm.cleanup_task_worktree.assert_called_once_with("sp_244", str(task.id))

        task.refresh_from_db()
        self.assertNotIn("hard_action_status", task.metadata)
        self.assertNotIn("hard_action", task.metadata)

        latest = TaskComment.objects.filter(task=task).latest("id")
        self.assertIn("deleted branch", latest.content)
        self.assertEqual(latest.comment_type, CommentType.STATUS_UPDATE)

    @patch("tasks.dag_executor._get_worktree_manager")
    def test_keep_reply_leaves_branch_untouched(self, mock_get_wm):
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        task = self._task_parked_needs_human()
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="keep it, I need it",
        )

        resume_hard_action_with_reply.__wrapped__(task.id, comment.id)

        mock_wm.cleanup_task_worktree.assert_not_called()

        task.refresh_from_db()
        self.assertNotIn("hard_action_status", task.metadata)

        latest = TaskComment.objects.filter(task=task).latest("id")
        self.assertIn("nothing was deleted", latest.content)

    @patch("tasks.dag_executor._get_worktree_manager")
    def test_skips_if_no_longer_parked(self, mock_get_wm):
        """Race guard: if the flag was already cleared before this ran, do
        nothing (mirrors the merge resume's needs_human race guard)."""
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        spec = self.make_spec(self.board, odin_id="sp_244b", metadata={"branch": "spec/sp_244b"})
        task = self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING,
            metadata={"branch": "task/sp_244b/1"},
        )
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com", content="delete",
        )

        resume_hard_action_with_reply.__wrapped__(task.id, comment.id)

        mock_wm.cleanup_task_worktree.assert_not_called()

    def test_missing_comment_is_a_noop(self):
        task = self._task_parked_needs_human()

        resume_hard_action_with_reply.__wrapped__(task.id, 999999)

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("hard_action_status"), "needs_human")


# ─────────────────────────────────────────────────────────────────────
# End-to-end: park → explain → reply → proceed
# ─────────────────────────────────────────────────────────────────────

class EndToEndHardActionResumeTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test")
    @patch("tasks.dag_executor.resume_hard_action_with_reply.delay")
    def test_park_reply_proceed_round_trip(self, mock_delay, _mock_resolve):
        """The acceptance-criteria round trip: a mocked hard action parks,
        a human reply resumes it via the real comment-create API, and the
        branch deletion is finally carried out — no operator git surgery."""
        spec = self.make_spec(self.board, odin_id="sp_e2e", metadata={"branch": "spec/sp_e2e"})
        task = self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING,
            metadata={"working_dir": "/tmp/test", "branch": "task/sp_e2e/1"},
        )

        # 1. The hard action is attempted (no merge_status → unmerged) and
        #    parks instead of deleting.
        with patch("odin.worktree.WorktreeManager") as MockWM:
            mock_wm_during_park = MagicMock()
            MockWM.return_value = mock_wm_during_park
            _cleanup_task_worktree(task)
            mock_wm_during_park.cleanup_task_worktree.assert_not_called()

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("hard_action_status"), "needs_human")
        question = TaskComment.objects.filter(task=task, comment_type=CommentType.QUESTION).latest("id")
        self.assertIn("irreversible", question.content.lower())

        # 2. The re-dispatch call is captured (not sent to a real broker);
        #    drive it synchronously to simulate the worker picking it up.
        mock_delay.side_effect = lambda tid, cid: resume_hard_action_with_reply.__wrapped__(tid, cid)

        with patch("tasks.dag_executor._get_worktree_manager") as mock_get_wm:
            mock_wm = MagicMock()
            mock_get_wm.return_value = mock_wm

            # 3. Human replies via the real comment-create API.
            resp = self.client.post(f"/tasks/{task.id}/comments/", {
                "author_email": "alice@test.com",
                "content": "yes, proceed",
            }, format="json")
            self.assertEqual(resp.status_code, 201)

            # 4. The action was carried out and the park cleared.
            mock_wm.cleanup_task_worktree.assert_called_once_with("sp_e2e", str(task.id))

        task.refresh_from_db()
        self.assertNotIn("hard_action_status", task.metadata)
        status_comment = TaskComment.objects.filter(
            task=task, comment_type=CommentType.STATUS_UPDATE,
        ).latest("id")
        self.assertIn("deleted branch", status_comment.content)
