"""Tests for DAG executor worktree integration.

Verifies that worktree creation happens when spec has a branch,
merge status metadata is written, and failures degrade gracefully.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock
from pathlib import Path

from tests.base import APITestCase
from tasks.models import Task, TaskStatus
from tasks.dag_executor import (
    poll_and_execute,
    _create_task_worktree,
    _merge_task_branch,
    _cleanup_task_worktree,
)


class WorktreeCreationTests(APITestCase):
    """Tests for worktree creation during task transition to EXECUTING."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
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
        task = self.make_task(
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
        task = self.make_task(
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
        task = self.make_task(self.board, spec=spec, title="Fix bug", metadata={"working_dir": "/tmp/test"})

        with patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test"):
            with patch("odin.worktree.WorktreeManager") as MockWM:
                from odin.worktree import MergeResult
                mock_wm = MagicMock()
                mock_wm.merge_task_into_spec.return_value = MergeResult(success=True)
                MockWM.return_value = mock_wm

                result = _merge_task_branch(task)
                assert result.success is True

    def test_cleanup_calls_manager(self):
        """_cleanup_task_worktree calls WorktreeManager.cleanup_task_worktree."""
        spec = self.make_spec(self.board, odin_id="sp_abc", metadata={"branch": "spec/sp_abc"})
        task = self.make_task(self.board, spec=spec, metadata={"working_dir": "/tmp/test"})

        with patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp/test"):
            with patch("odin.worktree.WorktreeManager") as MockWM:
                mock_wm = MagicMock()
                MockWM.return_value = mock_wm

                _cleanup_task_worktree(task)
                mock_wm.cleanup_task_worktree.assert_called_once()
