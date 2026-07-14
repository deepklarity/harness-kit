"""Tests for API-driven DONE transitions triggering worktree cleanup.

Tags: [mock] — uses Django SQLite + mocked _remove_task_worktree.

The existing `_cleanup_done_task_worktree` only fires when the spec
finalization path transitions TESTING tasks to DONE.  The operator's
real flow is `PATCH /tasks/:id/ {"status":"DONE"}` after spot-checking.
That API path must also fire worktree cleanup, otherwise API promotions
leak worktrees indefinitely.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tasks.models import Task, TaskComment, TaskHistory, TaskStatus
from tests.base import APITestCase


class ApiDoneCleanupTests(APITestCase):
    """PATCHing a task to DONE via the API must run the worktree cleanup
    helper, mirroring what the spec-finalization path already does."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.spec = self.make_spec(
            self.board, odin_id="sp_apidon", metadata={"branch": "spec/sp_apidon"},
        )

    def _task_in_testing(self, metadata):
        return self.make_task(
            self.board,
            spec=self.spec,
            status=TaskStatus.TESTING,
            metadata=metadata,
            assignee=self.user,
        )

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_api_done_triggers_worktree_cleanup(self, mock_cleanup):
        """PATCH /tasks/:id/ with status=DONE must call
        _cleanup_done_task_worktree exactly once.  This is the API path
        the operator uses for promotion — it must reclaim the worktree
        just like the spec-finalization path already does."""
        wt_path = "/tmp/.odin/worktrees/sp_apidon/42"
        task = self._task_in_testing({
            "worktree_path": wt_path,
            "working_dir": wt_path,
            "branch": "task/sp_apidon/42",
        })

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data={"status": "DONE", "updated_by": "operator@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        mock_cleanup.assert_called_once()
        # Called with the task instance itself (matches spec-finalization).
        called_task = mock_cleanup.call_args.args[0]
        self.assertEqual(called_task.id, task.id)

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_api_done_without_worktree_skips_cleanup_gracefully(self, mock_cleanup):
        """A task that never had a worktree (no metadata.worktree_path)
        still transitions to DONE, and cleanup is called but does
        nothing (the helper short-circuits internally)."""
        task = self._task_in_testing(metadata={})

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data={"status": "DONE", "updated_by": "operator@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        mock_cleanup.assert_called_once()

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_api_done_records_history(self, mock_cleanup):
        """The DONE transition via API still records a TaskHistory row
        (existing behaviour, regression guard)."""
        task = self._task_in_testing({
            "worktree_path": "/tmp/.odin/worktrees/sp_apidon/42",
        })

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data={"status": "DONE", "updated_by": "operator@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        history = TaskHistory.objects.filter(
            task=task, field_name="status", new_value=TaskStatus.DONE,
        )
        self.assertTrue(history.exists())

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_api_review_to_done_triggers_cleanup(self, mock_cleanup):
        """An operator can promote from REVIEW or any pre-DONE state
        directly to DONE — the cleanup must fire regardless of the
        previous status."""
        for prev_status in (TaskStatus.REVIEW, TaskStatus.TESTING):
            task = self._task_in_testing({
                "worktree_path": f"/tmp/wt/{prev_status}",
                "branch": f"task/sp_apidon/{task.id if False else 99}",
            })
            task.status = prev_status
            task.save(update_fields=["status"])

            mock_cleanup.reset_mock()
            resp = self.client.patch(
                f"/tasks/{task.id}/",
                data={"status": "DONE", "updated_by": "operator@odin.agent"},
                format="json",
            )
            self.assertEqual(resp.status_code, 200, resp.content)
            task.refresh_from_db()
            self.assertEqual(task.status, TaskStatus.DONE)
            mock_cleanup.assert_called_once()

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_non_done_status_does_not_trigger_cleanup(self, mock_cleanup):
        """Cleanup must NOT fire when status moves to anything other than
        DONE (e.g. TESTING, REVIEW)."""
        task = self._task_in_testing({
            "worktree_path": "/tmp/wt/no-cleanup",
        })
        task.status = TaskStatus.REVIEW
        task.save(update_fields=["status"])

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data={"status": "TESTING", "updated_by": "operator@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        mock_cleanup.assert_not_called()

    @patch("tasks.dag_executor._cleanup_done_task_worktree")
    def test_cleanup_failure_does_not_block_done(self, mock_cleanup):
        """If the cleanup helper raises, the DONE transition must still
        succeed.  Worktree removal failures must never block the
        transition (matches spec-finalization contract)."""
        mock_cleanup.side_effect = RuntimeError("git boom")

        task = self._task_in_testing({
            "worktree_path": "/tmp/wt/boom",
        })

        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data={"status": "DONE", "updated_by": "operator@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_remove_task_worktree_handles_specless_task(self):
        """Regression: _remove_task_worktree used to crash with
        ``AttributeError: 'NoneType' object has no attribute 'odin_id'``
        when the task had no ``spec``.  The cleanup must fall back to
        removing the worktree at the metadata-recorded path so that
        spec-less tasks (e.g. demo boards or operators that skip the
        spec machinery) still reclaim disk on DONE.
        """
        import tempfile, subprocess
        from pathlib import Path
        from tasks.dag_executor import _remove_task_worktree

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            wt = Path(tmp) / "wt"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-q", "-m", "init"],
                cwd=str(repo), check=True, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                                                "PATH": os.environ["PATH"]},
            )
            subprocess.run(["git", "worktree", "add", "-B", "demo", str(wt)], cwd=str(repo), check=True)
            self.assertTrue(wt.exists())

            task = self.make_task(
                self.board,
                spec=None,
                status=TaskStatus.DONE,
                metadata={"worktree_path": str(wt), "working_dir": str(wt)},
            )

            # Should not raise AttributeError anymore.
            _remove_task_worktree(task)
            self.assertFalse(wt.exists())
