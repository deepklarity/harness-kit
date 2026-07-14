"""Tests for DAG executor worktree integration.

Verifies that worktree creation happens when spec has a branch,
merge status metadata is written, and failures degrade gracefully.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from pathlib import Path
from unittest.mock import MagicMock, patch

from tasks.dag_executor import (
    _cleanup_task_worktree,
    _create_task_worktree,
    _merge_task_branch,
    _transition_spec_tasks_to_done,
    merge_task_on_reflection,
    poll_and_execute,
)
from tasks.models import Task, TaskComment, TaskHistory, TaskStatus
from tests.base import APITestCase


class WorktreeCreationTests(APITestCase):
    """Tests for worktree creation during task transition to EXECUTING."""

    def setUp(self):
        super().setUp()
        # F43/F44: tasks without a worktree FAIL by default unless the board
        # opts in. These tests are about the WORKTREE path — opting the board
        # in keeps the EXECUTING path exercised. The new gate is covered by
        # NoWorktreeNoOptinTests below.
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    def _make_spec_task(self, status=TaskStatus.IN_PROGRESS, spec_metadata=None):
        """Create a task with a spec that has branch metadata."""
        spec = self.make_spec(self.board, metadata=spec_metadata or {"branch": "spec/sp_test"})
        task = self.make_task(
            self.board,
            spec=spec,
            status=status,
            assignee=self.user,
        )
        return task

    def _mock_exec_delay(self):
        """Return a mock for execute_single_task that returns serializable async_result."""
        mock_result = MagicMock()
        mock_result.id = "celery-task-id-123"
        mock = MagicMock()
        mock.delay.return_value = mock_result
        return mock

    @patch("tasks.dag_executor._create_task_worktree")
    @patch("tasks.dag_executor.execute_single_task")
    def test_worktree_created_when_spec_has_branch(self, mock_exec, mock_create_wt):
        """poll_and_execute creates worktree when spec has branch metadata."""
        mock_exec.delay.return_value = MagicMock(id="celery-123")
        mock_create_wt.return_value = Path("/tmp/fake/worktree")
        task = self._make_spec_task()

        poll_and_execute()

        task.refresh_from_db()
        assert task.status == TaskStatus.EXECUTING
        # Worktree creation was attempted
        mock_create_wt.assert_called_once()
        # Metadata should have worktree info
        assert task.metadata.get("merge_status") == "pending"
        assert "branch" in task.metadata

    @patch("tasks.dag_executor._create_task_worktree")
    @patch("tasks.dag_executor.execute_single_task")
    def test_no_worktree_without_spec_branch(self, mock_exec, mock_create_wt):
        """Tasks without spec branch don't get worktrees."""
        mock_exec.delay.return_value = MagicMock(id="celery-123")
        spec = self.make_spec(self.board, metadata={})
        self.make_task(
            self.board,
            spec=spec,
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
        )

        poll_and_execute()

        mock_create_wt.assert_not_called()

    @patch("tasks.dag_executor._create_task_worktree")
    @patch("tasks.dag_executor.execute_single_task")
    def test_no_worktree_without_spec(self, mock_exec, mock_create_wt):
        """Standalone tasks (no spec) don't get worktrees."""
        mock_exec.delay.return_value = MagicMock(id="celery-123")
        self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
        )

        poll_and_execute()

        mock_create_wt.assert_not_called()

    @patch("tasks.dag_executor._create_task_worktree", side_effect=Exception("git error"))
    @patch("tasks.dag_executor.execute_single_task")
    def test_worktree_failure_does_not_block_execution(self, mock_exec, mock_create_wt):
        """Task still executes even if worktree creation fails."""
        mock_exec.delay.return_value = MagicMock(id="celery-123")
        task = self._make_spec_task()

        poll_and_execute()

        task.refresh_from_db()
        assert task.status == TaskStatus.EXECUTING
        # Execution was still fired despite worktree failure
        mock_exec.delay.assert_called_once()


class MergeMetadataTests(APITestCase):
    """Tests for merge status metadata written after task completion."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_create_task_worktree_calls_manager(self):
        """_create_task_worktree instantiates WorktreeManager and calls create."""
        spec = self.make_spec(self.board, odin_id="sp_abc", metadata={"branch": "spec/sp_abc"})
        task = self.make_task(self.board, spec=spec, metadata={"working_dir": "/tmp/test"})

        with patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test"):
            with patch("odin.worktree.WorktreeManager") as MockWM:
                mock_wm = MagicMock()
                mock_wm.create_task_worktree.return_value = Path("/tmp/worktree")
                MockWM.return_value = mock_wm

                result = _create_task_worktree(task)
                assert result == Path("/tmp/worktree")
                mock_wm.create_task_worktree.assert_called_once_with("sp_abc", str(task.id))

    def test_merge_task_branch_returns_result(self):
        """_merge_task_branch calls WorktreeManager.merge_task_into_spec."""
        spec = self.make_spec(self.board, odin_id="sp_abc", metadata={"branch": "spec/sp_abc"})
        task = self.make_task(
            self.board, spec=spec, title="Fix bug",
            metadata={"working_dir": "/tmp/test"},
        )

        with patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test"):
            with patch("odin.worktree.WorktreeManager") as MockWM:
                from odin.worktree import MergeResult
                mock_wm = MagicMock()
                mock_wm.merge_task_into_spec.return_value = MergeResult(success=True)
                MockWM.return_value = mock_wm

                result = _merge_task_branch(task)
                assert result.success is True

    def test_cleanup_calls_manager(self):
        """_cleanup_task_worktree calls WorktreeManager.cleanup_task_worktree
        once the branch's work is already merged (task #244's reversibility
        gate classifies merge_status=merged as reversible — proceeds
        autonomously, no behavior change from before the gate existed)."""
        spec = self.make_spec(self.board, odin_id="sp_abc", metadata={"branch": "spec/sp_abc"})
        task = self.make_task(
            self.board, spec=spec,
            metadata={"working_dir": "/tmp/test", "branch": "task/sp_abc/1", "merge_status": "merged"},
        )

        with patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test"):
            with patch("odin.worktree.WorktreeManager") as MockWM:
                mock_wm = MagicMock()
                MockWM.return_value = mock_wm

                _cleanup_task_worktree(task)
                mock_wm.cleanup_task_worktree.assert_called_once()


class HardActionCleanupGateTests(APITestCase):
    """Task #244's reversibility gate: deleting a task branch that still
    carries unmerged work is a HARD action (permanent data loss), so
    ``_cleanup_task_worktree`` must park it for a human instead of running
    ``git branch -D`` autonomously. A branch already merged is REVERSIBLE
    (covered by ``test_cleanup_calls_manager`` above) and proceeds exactly
    as before the gate existed.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_with_merge_status(self, merge_status):
        spec = self.make_spec(self.board, odin_id="sp_hard", metadata={"branch": "spec/sp_hard"})
        metadata = {"working_dir": "/tmp/test", "branch": "task/sp_hard/1"}
        if merge_status is not None:
            metadata["merge_status"] = merge_status
        return self.make_task(self.board, spec=spec, metadata=metadata)

    @patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test")
    def test_unmerged_branch_parks_instead_of_deleting(self, _mock_resolve):
        """No merge_status at all (never merged) — the mocked hard action:
        parks with a QUESTION, and the WorktreeManager is never touched."""
        task = self._task_with_merge_status(None)

        with patch("odin.worktree.WorktreeManager") as MockWM:
            mock_wm = MagicMock()
            MockWM.return_value = mock_wm

            _cleanup_task_worktree(task)

            mock_wm.cleanup_task_worktree.assert_not_called()

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("hard_action_status"), "needs_human")
        self.assertEqual(
            task.metadata.get("hard_action"),
            {"key": "delete_branch_with_unmerged_work", "branch": "task/sp_hard/1"},
        )

        question = TaskComment.objects.filter(task=task, comment_type="question").latest("id")
        self.assertIn("task/sp_hard/1", question.content)
        self.assertIn("irreversible", question.content.lower())
        self.assertIn("?", question.content)

    @patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test")
    def test_conflicted_branch_parks_instead_of_deleting(self, _mock_resolve):
        """merge_status=conflict (still unmerged) also parks — only
        merged/noop are safe to auto-delete."""
        task = self._task_with_merge_status("conflict")

        with patch("odin.worktree.WorktreeManager") as MockWM:
            mock_wm = MagicMock()
            MockWM.return_value = mock_wm

            _cleanup_task_worktree(task)

            mock_wm.cleanup_task_worktree.assert_not_called()

        task.refresh_from_db()
        self.assertEqual(task.metadata.get("hard_action_status"), "needs_human")

    @patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test")
    def test_noop_branch_proceeds_without_parking(self, _mock_resolve):
        """merge_status=noop (branch already fully in the spec branch, no
        diff) is reversible — proceeds exactly like the merged case."""
        task = self._task_with_merge_status("noop")

        with patch("odin.worktree.WorktreeManager") as MockWM:
            mock_wm = MagicMock()
            MockWM.return_value = mock_wm

            _cleanup_task_worktree(task)

            mock_wm.cleanup_task_worktree.assert_called_once()

        task.refresh_from_db()
        self.assertNotIn("hard_action_status", task.metadata)
        self.assertFalse(
            TaskComment.objects.filter(task=task, comment_type="question").exists()
        )


class MergeConflictCommentTests(APITestCase):
    """The status comment posted on a merge conflict must include the
    blocking file list and surface the F24 hint when generated agent
    configs are involved (regression for task #112: bare "Merge
    conflict:" comment left the operator with nothing actionable).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_in_review(self):
        spec = self.make_spec(self.board, odin_id="sp_xyz", metadata={"branch": "spec/sp_xyz"})
        # The merge path in `merge_task_on_reflection` early-returns
        # when no task branch exists; tests must opt in by setting the
        # branch on task metadata so the conflict path actually runs.
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_xyz/42"},
        )

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_conflict_comment_includes_file_list(self, mock_merge_branch, _mock_advance):
        """Board comment names the conflicting files (the bug from #112)."""
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True,
            error="Merge conflict: see git",
            conflicting_files=["opencode.json", "shared.py"],
        )

        # `merge_task_on_reflection` is wrapped by `@shared_task`.  Calling
        # the .apply() shim directly avoids Celery broker glue and lets
        # the in-process mocks land.
        merge_task_on_reflection.__wrapped__(task.id)

        comment = TaskComment.objects.filter(task=task).latest("id")
        assert "opencode.json" in comment.content
        assert "shared.py" in comment.content
        assert "Merge conflict merging" in comment.content

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_conflict_comment_emits_generated_config_hint(
        self, mock_merge_branch, _mock_advance,
    ):
        """When a generated agent config is the blocker, F24 hint fires."""
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True,
            error="Merge conflict",
            conflicting_files=["opencode.json"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        comment = TaskComment.objects.filter(task=task).latest("id")
        assert "F24" in comment.content
        assert "generated agent config" in comment.content.lower()

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_conflict_comment_skips_hint_for_plain_files(
        self, mock_merge_branch, _mock_advance,
    ):
        """A non-config conflict still lists the file but skips the F24 hint."""
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True,
            error="Merge conflict",
            conflicting_files=["src/feature.py"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        comment = TaskComment.objects.filter(task=task).latest("id")
        assert "src/feature.py" in comment.content
        assert "F24" not in comment.content

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_conflict_metadata_marks_status(self, mock_merge_branch, _mock_advance):
        """Task metadata must record merge_status=conflict so downstream
        boards can filter/display the blocked tasks without re-parsing
        the comment thread.
        """
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True,
            error="Merge conflict",
            conflicting_files=["opencode.json"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        assert task.metadata.get("merge_status") == "conflict"


class MergeAgentResolutionTests(APITestCase):
    """Integration tests for the merge-agent conflict resolution flow.

    When the merge agent resolves a mechanical conflict, the task advances
    to TESTING with a rationale comment.  When a conflict is ambiguous,
    a blocking QUESTION comment is posted and merge_status=needs_human.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_in_review(self):
        spec = self.make_spec(self.board, odin_id="sp_xyz", metadata={"branch": "spec/sp_xyz"})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_xyz/42"},
        )

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_mechanical_conflict_auto_resolved(
        self, mock_merge_branch, mock_advance,
    ):
        """Generated-config conflict resolved by the merge agent →
        merge_status=merged, task advances, resolution comment posted.
        """
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=True,
            conflict=False,
            diff_stat=" 1 file changed",
            resolved_files=["opencode.json"],
            resolution_rationale="Generated configs — safe to take spec side.",
        )

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        assert task.metadata.get("merge_status") == "merged"
        assert task.metadata.get("merge_agent_resolved") is True
        assert "opencode.json" in task.metadata.get("merge_agent_files", [])
        mock_advance.assert_called_once()

        comment = TaskComment.objects.filter(task=task).latest("id")
        assert "opencode.json" in comment.content
        assert "auto-resolved" in comment.content.lower()

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_ambiguous_conflict_posts_question(
        self, mock_merge_branch, mock_advance,
    ):
        """Product-code conflict → QUESTION comment posted, no merge,
        task stays in REVIEW.
        """
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False,
            conflict=True,
            needs_human=True,
            error="Merge conflict (needs human): ambiguous",
            conflicting_files=["src/feature.py"],
            ambiguous_files=["src/feature.py"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        assert task.metadata.get("merge_status") == "needs_human"
        mock_advance.assert_not_called()

        question_comment = TaskComment.objects.filter(
            task=task, comment_type="question",
        ).first()
        assert question_comment is not None
        assert "src/feature.py" in question_comment.content
        assert "needs you" in question_comment.content.lower()

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_mixed_conflict_posts_question_with_both_files(
        self, mock_merge_branch, mock_advance,
    ):
        """Generated config + product code → QUESTION lists both file types
        so the operator has full context.
        """
        from odin.worktree import MergeResult
        task = self._task_in_review()

        mock_merge_branch.return_value = MergeResult(
            success=False,
            conflict=True,
            needs_human=True,
            error="needs human",
            conflicting_files=["opencode.json", "src/handlers.py"],
            ambiguous_files=["src/handlers.py"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        question_comment = TaskComment.objects.filter(
            task=task, comment_type="question",
        ).first()
        assert question_comment is not None
        assert "src/handlers.py" in question_comment.content
        assert "opencode.json" in question_comment.content


class MigrationCollisionNeedsHumanTests(APITestCase):
    """Replay task 225's migration leaf collision (W5.3): the gate must
    park with ``merge_status=needs_human`` (not ``conflict``) so the W5.11
    reply-resume signal fires — no operator flag flipping.

    The invariant (one flag, one meaning): ``needs_human`` means "a human
    comment could resolve this". The migration leaf collision satisfies it
    (a human reply triggers the bounded ``makemigrations --merge`` fix),
    so the setter parks it there. A genuinely non-human-answerable failure
    (e.g. push timeout) keeps its own status and the auto-requeue path owns
    it — it must NOT set needs_human.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_in_review(self):
        spec = self.make_spec(self.board, odin_id="sp_225", metadata={"branch": "spec/sp_225"})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_225/225"},
        )

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_migration_collision_parks_needs_human(self, mock_merge_branch, _mock_advance):
        """225 replay: migration leaf collision parks needs_human and posts a
        QUESTION naming both leaves + the two fixes (renumber / merge
        migration). The reply-resume listener only fires on needs_human, so
        this is the gate that unblocks the human-in-the-loop path."""
        from odin.worktree import MergeResult
        task = self._task_in_review()
        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=True,
            needs_human=True, migration_conflict=True,
            error=(
                "Django migration leaf collision in tasks: 2 leaves with no "
                "merge migration — 0049_a, 0049_b. Fix: (1) renumber one of "
                "the leaves so they don't share a migration prefix, or (2) "
                "commit a merge migration via `python3 manage.py makemigrations "
                "--merge` inside taskit/taskit-backend/ before retrying the merge."
            ),
            conflicting_files=["0049_a", "0049_b"],
        )

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        assert task.metadata.get("merge_status") == "needs_human"
        _mock_advance.assert_not_called()

        question = TaskComment.objects.filter(
            task=task, comment_type="question",
        ).first()
        assert question is not None
        assert "0049_a" in question.content
        assert "0049_b" in question.content
        assert "merge migration" in question.content.lower()

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_retryable_failure_does_not_set_needs_human(self, mock_merge_branch, _mock_advance):
        """A genuinely non-human-answerable failure (push timeout) keeps its
        own status — the auto-requeue path owns it, not the human
        reply-resume listener. needs_human must stay reserved for
        human-answerable failures."""
        from odin.worktree import MergeResult
        task = self._task_in_review()
        mock_merge_branch.return_value = MergeResult(
            success=False, conflict=False,
            error="push failed: network timeout",
        )

        merge_task_on_reflection.__wrapped__(task.id)

        task.refresh_from_db()
        assert task.metadata.get("merge_status") == "error"
        assert task.metadata.get("merge_status") != "needs_human"


class DoneWorktreeCleanupTests(APITestCase):
    """When a task transitions to DONE (spec finalization), its worktree is
    removed to reclaim disk while the task branch is preserved. Removal
    failures must never block the transition.

    Addresses: worktrees accumulating until spec finalization, filling the
    disk and taking the loop down. REVIEW/TESTING/FAILED worktrees are kept
    (rework/inspection needs them); only DONE reclaims.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_in_testing(self, metadata):
        spec = self.make_spec(self.board, odin_id="sp_done", metadata={"branch": "spec/sp_done"})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING, metadata=metadata,
        )

    @patch("tasks.dag_executor._remove_task_worktree")
    def test_done_transition_removes_worktree_and_clears_path(self, mock_remove):
        """DONE transition removes the task worktree and clears
        metadata.worktree_path; the branch is preserved."""
        wt_path = "/tmp/.odin/worktrees/sp_done/42"
        task = self._task_in_testing({
            "worktree_path": wt_path,
            "working_dir": wt_path,
            "branch": "task/sp_done/42",
        })

        _transition_spec_tasks_to_done(task.spec, pr_url="https://example.com/pr/1")

        task.refresh_from_db()
        assert task.status == TaskStatus.DONE
        mock_remove.assert_called_once_with(task)
        # worktree bookkeeping cleared, branch kept
        assert "worktree_path" not in task.metadata
        assert "working_dir" not in task.metadata
        assert task.metadata.get("branch") == "task/sp_done/42"

    @patch("tasks.dag_executor._remove_task_worktree")
    def test_done_without_worktree_path_skips_removal(self, mock_remove):
        """A task that never had a worktree (no metadata.worktree_path) is
        not sent through the removal path at all."""
        spec = self.make_spec(self.board, odin_id="sp_done", metadata={"branch": "spec/sp_done"})
        self.make_task(self.board, spec=spec, status=TaskStatus.TESTING, metadata={})

        _transition_spec_tasks_to_done(spec, pr_url="https://example.com/pr/1")

        mock_remove.assert_not_called()
        assert Task.objects.filter(spec=spec, status=TaskStatus.DONE).exists()

    @patch("tasks.dag_executor._remove_task_worktree", side_effect=RuntimeError("git boom"))
    def test_removal_error_does_not_block_transition(self, mock_remove):
        """A worktree removal failure must not block the DONE transition:
        status still moves to DONE, history is recorded, a comment surfaces
        the failure, and the worktree_path is kept so the operator can find
        the leftover worktree to clean up manually."""
        wt_path = "/tmp/.odin/worktrees/sp_done/42"
        task = self._task_in_testing({
            "worktree_path": wt_path,
            "branch": "task/sp_done/42",
        })

        _transition_spec_tasks_to_done(task.spec, pr_url="https://example.com/pr/1")

        task.refresh_from_db()
        assert task.status == TaskStatus.DONE
        assert TaskHistory.objects.filter(
            task=task, new_value=TaskStatus.DONE,
        ).exists()
        # path kept for discoverability
        assert task.metadata.get("worktree_path") == wt_path
        cleanup_comment = TaskComment.objects.filter(
            task=task, content__icontains="could not remove worktree",
        ).first()
        assert cleanup_comment is not None
        assert wt_path in cleanup_comment.content

    @patch("tasks.dag_executor._remove_task_worktree")
    def test_review_testing_failed_worktrees_not_touched(self, mock_remove):
        """Only TESTING tasks are moved to DONE on finalize; REVIEW/FAILED
        tasks keep their worktrees for inspection/rework."""
        spec = self.make_spec(self.board, odin_id="sp_done", metadata={"branch": "spec/sp_done"})
        self.make_task(
            self.board, spec=spec, status=TaskStatus.TESTING,
            metadata={"worktree_path": "/tmp/wt/testing", "branch": "task/sp_done/1"},
        )
        self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"worktree_path": "/tmp/wt/review", "branch": "task/sp_done/2"},
        )
        self.make_task(
            self.board, spec=spec, status=TaskStatus.FAILED,
            metadata={"worktree_path": "/tmp/wt/failed", "branch": "task/sp_done/3"},
        )

        _transition_spec_tasks_to_done(spec, pr_url="https://example.com/pr/1")

        # Only the TESTING task's worktree was removed.
        assert mock_remove.call_count == 1
        assert Task.objects.filter(spec=spec, status=TaskStatus.DONE).count() == 1
        assert Task.objects.filter(spec=spec, status=TaskStatus.REVIEW).exists()
        assert Task.objects.filter(spec=spec, status=TaskStatus.FAILED).exists()


class StaleWorktreeRecoveryTests(APITestCase):
    """Task 236: on retry after a failed run, a stale worktree (no live
    TaskRun) is auto-removed so dispatch succeeds without operator action.
    A worktree owned by a RUNNING run is never removed — the guard that
    matters.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(
            self.board, odin_id="sp_236", metadata={"branch": "spec/sp_236"},
        )

    def _make_task(self):
        return self.make_task(
            self.board, spec=self.spec, status=TaskStatus.IN_PROGRESS,
        )

    @patch("tasks.dag_executor._get_worktree_manager")
    @patch("tasks.dag_executor.task_runs")
    def test_stale_worktree_removed_when_no_live_run(self, mock_task_runs, mock_get_wm):
        """Replay 234/236: worktree exists from a dead run, no RUNNING
        TaskRun — stale worktree is removed (branch preserved) and a fresh
        one created.  Dispatch succeeds without operator action."""
        mock_task_runs.current_running_run.return_value = None
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_wm.get_worktree_path.return_value = mock_path
        mock_wm.create_task_worktree.return_value = Path("/tmp/fresh_wt")

        task = self._make_task()
        result = _create_task_worktree(task)

        assert result == Path("/tmp/fresh_wt")
        mock_wm.remove_task_worktree.assert_called_once_with("sp_236", str(task.id))
        mock_wm.create_task_worktree.assert_called_once_with("sp_236", str(task.id))

    @patch("tasks.dag_executor._get_worktree_manager")
    @patch("tasks.dag_executor.task_runs")
    def test_live_run_owns_worktree_refuses_loudly(self, mock_task_runs, mock_get_wm):
        """A worktree owned by a RUNNING run is never removed.  The guard
        prevents clobbering a live execution's working directory."""
        live_run = MagicMock()
        live_run.run_token = "abc12345def"
        mock_task_runs.current_running_run.return_value = live_run
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_wm.get_worktree_path.return_value = mock_path

        task = self._make_task()
        result = _create_task_worktree(task)

        assert result is None
        mock_wm.remove_task_worktree.assert_not_called()
        mock_wm.create_task_worktree.assert_not_called()

    @patch("tasks.dag_executor._get_worktree_manager")
    @patch("tasks.dag_executor.task_runs")
    def test_no_existing_worktree_creates_normally(self, mock_task_runs, mock_get_wm):
        """No stale worktree on disk — normal creation path, no removal."""
        mock_wm = MagicMock()
        mock_get_wm.return_value = mock_wm
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_wm.get_worktree_path.return_value = mock_path
        mock_wm.create_task_worktree.return_value = Path("/tmp/wt")

        task = self._make_task()
        result = _create_task_worktree(task)

        assert result == Path("/tmp/wt")
        mock_wm.remove_task_worktree.assert_not_called()
        mock_wm.create_task_worktree.assert_called_once_with("sp_236", str(task.id))



class WorktreeCreationRetryTests(APITestCase):
    """Task 285 failed missing_worktree on first concurrent fork of a fresh
    spec branch and succeeded on immediate redispatch — classic git lock
    contention. Creation now retries transient lock errors with backoff."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_retries_on_lock_error_then_succeeds(self):
        from unittest.mock import patch
        from tasks import dag_executor

        calls = {"n": 0}

        def flaky(task):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("fatal: Unable to create '.git/index.lock': could not lock")
            return "/tmp/wt"

        with patch.object(dag_executor, "_create_task_worktree", side_effect=flaky):
            out = dag_executor._create_task_worktree_with_retry(self.task, backoff_ms=1)
        self.assertEqual(out, "/tmp/wt")
        self.assertEqual(calls["n"], 2)

    def test_non_transient_error_fails_fast(self):
        from unittest.mock import patch
        from tasks import dag_executor

        calls = {"n": 0}

        def broken(task):
            calls["n"] += 1
            raise RuntimeError("spec branch does not exist")

        with patch.object(dag_executor, "_create_task_worktree", side_effect=broken):
            with self.assertRaises(RuntimeError):
                dag_executor._create_task_worktree_with_retry(self.task, backoff_ms=1)
        self.assertEqual(calls["n"], 1)
