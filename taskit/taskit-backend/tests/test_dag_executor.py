"""Tests for the DAG executor Celery tasks and centralized dependency module."""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock

from tests.base import APITestCase
from tasks.models import Task, TaskComment, TaskHistory, TaskStatus
from tasks.dependencies import DepStatus, check_deps, get_failed_deps, get_unmet_deps, get_ready_tasks
from tasks.dag_executor import poll_and_execute, execute_single_task


class CheckDepsTests(APITestCase):
    """Tests for the centralized check_deps function."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_no_deps_always_ready(self):
        task = self.make_task(self.board, depends_on=[])
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_all_deps_done(self):
        dep_a = self.make_task(self.board, title="Dep A", status=TaskStatus.DONE)
        dep_b = self.make_task(self.board, title="Dep B", status=TaskStatus.DONE)
        task = self.make_task(self.board, depends_on=[dep_a.id, dep_b.id])
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_review_counts_as_satisfied(self):
        """REVIEW means the agent finished — downstream can start."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.REVIEW)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_partial_deps_waiting(self):
        dep_done = self.make_task(self.board, title="Done", status=TaskStatus.DONE)
        dep_wip = self.make_task(self.board, title="WIP", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(self.board, depends_on=[dep_done.id, dep_wip.id])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_deps_in_todo_waiting(self):
        dep = self.make_task(self.board, title="Todo", status=TaskStatus.TODO)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_deps_executing_waiting(self):
        dep = self.make_task(self.board, title="Executing", status=TaskStatus.EXECUTING)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_failed_dep_blocked(self):
        dep = self.make_task(self.board, title="Failed", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)

    def test_mixed_failed_and_done_still_blocked(self):
        """One failed dep + one done dep = BLOCKED (failed takes priority)."""
        dep_done = self.make_task(self.board, title="Done", status=TaskStatus.DONE)
        dep_fail = self.make_task(self.board, title="Failed", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep_done.id, dep_fail.id])
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)

    # --- Recovery scenarios ---

    def test_failed_dep_fixed_to_done_unblocks(self):
        """Human fixes a failed dep by marking DONE → dependent becomes READY."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)

        # Human fixes the dep
        dep.status = TaskStatus.DONE
        dep.save(update_fields=["status"])

        # Re-check: should be READY now (runtime query, not cached)
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_failed_dep_retried_to_in_progress_stays_waiting(self):
        """Human retries a failed dep (→ IN_PROGRESS) → dependent stays WAITING."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)

        dep.status = TaskStatus.IN_PROGRESS
        dep.save(update_fields=["status"])

        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_failed_dep_reset_to_todo_stays_waiting(self):
        """Dep goes FAILED → TODO (reset for re-execution) → dependent stays WAITING."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)

        dep.status = TaskStatus.TODO
        dep.save(update_fields=["status"])

        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_three_deps_complete_in_different_orders(self):
        """Task depends on 3 tasks: they complete in different orders → waits for all."""
        dep_a = self.make_task(self.board, title="A", status=TaskStatus.IN_PROGRESS)
        dep_b = self.make_task(self.board, title="B", status=TaskStatus.TODO)
        dep_c = self.make_task(self.board, title="C", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(self.board, depends_on=[dep_a.id, dep_b.id, dep_c.id])

        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # C finishes first
        dep_c.status = TaskStatus.DONE
        dep_c.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # A finishes second
        dep_a.status = TaskStatus.REVIEW
        dep_a.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # B finishes last
        dep_b.status = TaskStatus.DONE
        dep_b.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_three_deps_one_fails_one_done_one_running_blocked(self):
        """One failed + one done + one running = BLOCKED."""
        dep_done = self.make_task(self.board, title="Done", status=TaskStatus.DONE)
        dep_fail = self.make_task(self.board, title="Failed", status=TaskStatus.FAILED)
        dep_wip = self.make_task(self.board, title="WIP", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(
            self.board,
            depends_on=[dep_done.id, dep_fail.id, dep_wip.id],
        )
        self.assertEqual(check_deps(task), DepStatus.BLOCKED)


class GetFailedDepsTests(APITestCase):
    """Tests for get_failed_deps."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_no_deps(self):
        task = self.make_task(self.board, depends_on=[])
        self.assertEqual(get_failed_deps(task), [])

    def test_returns_failed_deps(self):
        dep_ok = self.make_task(self.board, title="OK", status=TaskStatus.DONE)
        dep_fail = self.make_task(self.board, title="Failed", status=TaskStatus.FAILED)
        task = self.make_task(self.board, depends_on=[dep_ok.id, dep_fail.id])
        failed = get_failed_deps(task)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].id, dep_fail.id)


class GetUnmetDepsTests(APITestCase):
    """Tests for get_unmet_deps."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_no_deps(self):
        task = self.make_task(self.board, depends_on=[])
        self.assertEqual(get_unmet_deps(task), [])

    def test_returns_unmet_deps(self):
        dep_done = self.make_task(self.board, title="Done", status=TaskStatus.DONE)
        dep_wip = self.make_task(self.board, title="WIP", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(self.board, depends_on=[dep_done.id, dep_wip.id])
        unmet = get_unmet_deps(task)
        self.assertEqual(len(unmet), 1)
        self.assertEqual(unmet[0].id, dep_wip.id)


class GetReadyTasksTests(APITestCase):
    """Tests for get_ready_tasks queryset helper."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_task_with_no_deps_is_ready(self):
        task = self.make_task(self.board, depends_on=[], assignee=self.user)
        ready = get_ready_tasks(Task.objects.all())
        self.assertIn(task.id, [t.id for t in ready])

    def test_task_with_satisfied_deps_is_ready(self):
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.DONE)
        task = self.make_task(self.board, depends_on=[dep.id], assignee=self.user)
        ready = get_ready_tasks(Task.objects.filter(id=task.id))
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, task.id)

    def test_task_with_failed_dep_not_ready(self):
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.FAILED)
        self.make_task(self.board, depends_on=[dep.id], assignee=self.user)
        ready = get_ready_tasks(Task.objects.all())
        # Only the dep itself should appear (no deps = ready), not the blocked task
        self.assertTrue(all(t.depends_on == [] for t in ready if t.depends_on != [dep.id]))

    def test_max_count_limits_results(self):
        for i in range(5):
            self.make_task(self.board, title=f"Task {i}", depends_on=[], assignee=self.user)
        ready = get_ready_tasks(Task.objects.all(), max_count=2)
        self.assertEqual(len(ready), 2)


class PollAndExecuteTests(APITestCase):
    """Tests for the poll_and_execute Celery task."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_transitions_ready_task_to_executing(self, mock_exec):
        """A ready IN_PROGRESS task with satisfied deps moves to EXECUTING."""
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[],
        )
        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        mock_exec.delay.assert_called_once()
        args = mock_exec.delay.call_args[0]
        self.assertEqual(args[0], task.id)
        self.assertTrue(args[1])

        # Verify history was recorded
        history = TaskHistory.objects.filter(task=task, field_name="status")
        self.assertTrue(history.exists())
        latest = history.order_by("-changed_at").first()
        self.assertEqual(latest.new_value, TaskStatus.EXECUTING)

    @patch("tasks.dag_executor.execute_single_task")
    def test_skips_unassigned_tasks(self, mock_exec):
        """Tasks without an assignee are skipped."""
        self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=None, depends_on=[],
        )
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_skips_unsatisfied_deps(self, mock_exec):
        """Tasks with unsatisfied deps are skipped."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.IN_PROGRESS)
        self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[dep.id],
        )
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_skips_failed_deps(self, mock_exec):
        """Tasks with failed deps are skipped (block-don't-fail)."""
        dep = self.make_task(self.board, title="Failed Dep", status=TaskStatus.FAILED)
        self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[dep.id],
        )
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    @patch("tasks.dag_executor.settings")
    def test_respects_concurrency_limit(self, mock_settings, mock_exec):
        """Only fires up to max_concurrency - executing_count tasks."""
        mock_settings.DAG_EXECUTOR_MAX_CONCURRENCY = 2

        # One task already executing
        self.make_task(self.board, title="Already Executing", status=TaskStatus.EXECUTING)

        # Three ready tasks
        for i in range(3):
            self.make_task(
                self.board, title=f"Ready {i}",
                status=TaskStatus.IN_PROGRESS,
                assignee=self.user, depends_on=[],
            )

        poll_and_execute()

        # Only 1 slot available (max 2 - 1 executing = 1)
        self.assertEqual(mock_exec.delay.call_count, 1)

    # --- TODO tasks are never touched ---

    @patch("tasks.dag_executor.execute_single_task")
    def test_poll_never_touches_todo_tasks(self, mock_exec):
        """TODO tasks stay in TODO regardless of assignee or deps.

        The DAG executor only acts on IN_PROGRESS tasks. Moving a task to
        IN_PROGRESS is an explicit human or odin action.
        """
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.DONE)
        task = self.make_task(
            self.board, status=TaskStatus.TODO,
            assignee=self.user, depends_on=[dep.id],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TODO)
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_poll_skips_unassigned_todo_tasks(self, mock_exec):
        """TODO tasks without assignees are not picked up."""
        self.make_task(self.board, status=TaskStatus.TODO, assignee=None)
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_todo_requires_explicit_queue_to_execute(self, mock_exec):
        """TODO → IN_PROGRESS must be done explicitly; then the poll picks it up."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.DONE)
        task = self.make_task(
            self.board, status=TaskStatus.TODO,
            assignee=self.user, depends_on=[dep.id],
        )

        # Poll 1: task is TODO → nothing happens
        poll_and_execute()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TODO)
        mock_exec.delay.assert_not_called()

        # Human (or odin) explicitly moves to IN_PROGRESS
        task.status = TaskStatus.IN_PROGRESS
        task.save(update_fields=["status"])

        # Poll 2: task is IN_PROGRESS with satisfied deps → EXECUTING
        poll_and_execute()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        mock_exec.delay.assert_called_once()
        args = mock_exec.delay.call_args[0]
        self.assertEqual(args[0], task.id)
        self.assertTrue(args[1])


class ExecuteSingleTaskTests(APITestCase):
    """Tests for the execute_single_task Celery task."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor.subprocess.run")
    def test_success_transitions_to_review(self, mock_run):
        """Successful execution moves task to REVIEW."""
        mock_run.return_value = MagicMock(returncode=0)
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVIEW)

    @patch("tasks.dag_executor.subprocess.run")
    def test_failure_transitions_to_failed(self, mock_run):
        """Failed execution moves task to FAILED."""
        mock_run.return_value = MagicMock(returncode=1)
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    @patch("tasks.dag_executor.subprocess.run")
    def test_failure_fallback_posts_diagnostic_comment(self, mock_run):
        """Fallback FAILED path posts an explicit failure reason comment."""
        mock_run.return_value = MagicMock(returncode=7)
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata.get("last_failure_origin"), "taskit_dag_executor")

        comment = TaskComment.objects.filter(task=task, author_email="odin+dag-executor@system").first()
        self.assertIsNotNone(comment)
        self.assertIn("Failure type:", comment.content)
        self.assertIn("Reason:", comment.content)
        self.assertIn("Origin: taskit_dag_executor", comment.content)

    @patch("tasks.dag_executor.subprocess.run")
    def test_failure_fallback_classifies_backend_auth_failure_from_log(self, mock_run):
        """Auth-style failures in odin output should be classified precisely."""
        def side_effect(*args, **kwargs):
            stdout = kwargs.get("stdout")
            stdout.write(
                "Authentication error: TaskIt returned 401 Unauthorized for "
                "http://localhost:8000/tasks/?board_id=1\n"
            )
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata.get("last_failure_type"), "backend_auth_failure")
        self.assertIn("401 Unauthorized", task.metadata.get("last_failure_reason", ""))
        self.assertEqual(task.metadata.get("last_failure_origin"), "taskit_dag_executor")

        comment = TaskComment.objects.filter(task=task, author_email="odin+dag-executor@system").first()
        self.assertIsNotNone(comment)
        self.assertIn("Failure type: backend_auth_failure", comment.content)
        self.assertIn("Reason:", comment.content)

    @patch("tasks.dag_executor.subprocess.run")
    def test_failure_fallback_strips_ansi_from_debug_excerpt(self, mock_run):
        """ANSI color/control codes should not leak into failure debug excerpts."""
        def side_effect(*args, **kwargs):
            stdout = kwargs.get("stdout")
            stdout.write("\x1b[31mAuthentication error:\x1b[0m bad token\n")
            return MagicMock(returncode=2)

        mock_run.side_effect = side_effect
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        comment = TaskComment.objects.filter(task=task, author_email="odin+dag-executor@system").first()
        self.assertIsNotNone(comment)
        self.assertIn("Debug:", comment.content)
        self.assertNotIn("\x1b[31m", comment.content)
        self.assertNotIn("\x1b[0m", comment.content)

    @patch("tasks.dag_executor.subprocess.run")
    def test_timeout_transitions_to_failed(self, mock_run):
        """Timed-out execution moves task to FAILED."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["odin"], timeout=600)
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    @patch("tasks.dag_executor.subprocess.run")
    def test_skips_non_executing_task(self, mock_run):
        """If task is not EXECUTING, skip it."""
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user,
        )

        execute_single_task(task.id)
        mock_run.assert_not_called()

    @patch("tasks.dag_executor.subprocess.run")
    def test_respects_odin_status_update(self, mock_run):
        """If odin already updated the status, don't overwrite it."""
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        def side_effect(*args, **kwargs):
            # Simulate odin updating the task status during execution
            Task.objects.filter(id=task.id).update(status=TaskStatus.DONE)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect

        execute_single_task(task.id)

        task.refresh_from_db()
        # Should remain DONE (odin's update), not overwritten to REVIEW
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_nonexistent_task_handled(self):
        """Passing a nonexistent task ID doesn't crash."""
        execute_single_task(99999)  # Should log error and return
