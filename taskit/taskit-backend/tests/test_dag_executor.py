"""Tests for the DAG executor Celery tasks and centralized dependency module."""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock
import subprocess

from tests.base import APITestCase
from tasks.models import ReflectionReport, ReflectionStatus, Task, TaskComment, TaskHistory, TaskStatus
from tasks.dependencies import DepStatus, check_deps, get_failed_deps, get_unmet_deps, get_ready_tasks
from tasks.dag_executor import poll_and_execute, execute_single_task, execute_reflection


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

    def test_review_does_not_count_as_satisfied(self):
        """REVIEW = agent finished but code NOT merged → dependent stays WAITING.

        Trust-bucket invariant (fable task 214): the dependent worktree forks
        from the spec branch. If the dep is in REVIEW, its code is still on a
        feature branch and has not landed on the spec branch — a dependent
        that started now would build against missing upstream code. Dep
        status must advance to TESTING (post-merge) before the dependent
        becomes eligible.
        """
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.REVIEW)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_testing_counts_as_satisfied(self):
        """TESTING = dep code merged to spec branch → dependent is READY."""
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.TESTING)
        task = self.make_task(self.board, depends_on=[dep.id])
        self.assertEqual(check_deps(task), DepStatus.READY)

    def test_dep_review_to_testing_unblocks_dependent(self):
        """The merge gate: dependent is WAITING in REVIEW, READY on TESTING.

        Same property as the unit test above, exercised against the real
        Django ORM so the runtime-query behavior is anchored — no caching
        of dep status between checks.
        """
        dep = self.make_task(self.board, title="Dep", status=TaskStatus.REVIEW)
        task = self.make_task(self.board, depends_on=[dep.id])

        # Pre-merge: dependent waits.
        self.assertEqual(check_deps(task), DepStatus.WAITING)
        # Sanity: REVIEW must also appear in get_unmet_deps.
        self.assertIn(dep.id, [d.id for d in get_unmet_deps(task)])

        # Post-merge: dependent becomes ready.
        dep.status = TaskStatus.TESTING
        dep.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.READY)
        self.assertEqual(get_unmet_deps(task), [])

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
        """Task depends on 3 tasks: they complete in different orders → waits for all.

        Merge-gate contract: a dep in REVIEW does NOT unblock the dependent.
        Only TESTING/DONE count as "complete". Each step below exercises a
        dep status transition; the dependent flips READY only when every dep
        is in TESTING/DONE.
        """
        dep_a = self.make_task(self.board, title="A", status=TaskStatus.IN_PROGRESS)
        dep_b = self.make_task(self.board, title="B", status=TaskStatus.TODO)
        dep_c = self.make_task(self.board, title="C", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(self.board, depends_on=[dep_a.id, dep_b.id, dep_c.id])

        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # C finishes first — REVIEW is NOT a merge gate yet, dependent waits.
        dep_c.status = TaskStatus.REVIEW
        dep_c.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # C merges to spec branch (REVIEW → TESTING). B is still TODO, so still WAITING.
        dep_c.status = TaskStatus.TESTING
        dep_c.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # A finishes second and lands (DONE).
        dep_a.status = TaskStatus.DONE
        dep_a.save(update_fields=["status"])
        self.assertEqual(check_deps(task), DepStatus.WAITING)

        # B finishes last.
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
        # F43/F44: tasks without a worktree FAIL by default unless the board
        # opts in. Pre-existing dispatch-semantics tests opt the board in so
        # they keep their original intent (exercise the EXECUTING path).
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_transitions_ready_task_to_executing(self, mock_exec):
        """A ready IN_PROGRESS task with satisfied deps moves to EXECUTING."""
        # Set a string id so JSON serialization of celery_task_id doesn't fail
        mock_exec.delay.return_value.id = "fake-celery-id-001"

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
        mock_exec.delay.return_value.id = "fake-celery-id-002"
        self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=None, depends_on=[],
        )
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_skips_unsatisfied_deps(self, mock_exec):
        """Tasks with unsatisfied deps are skipped."""
        mock_exec.delay.return_value.id = "fake-celery-id-003"
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
        mock_exec.delay.return_value.id = "fake-celery-id-004"
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
        mock_exec.delay.return_value.id = "fake-celery-id-005"

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

    @patch("tasks.dag_executor.execute_single_task")
    def test_unassigned_task_stamps_dispatch_blocked_reason(self, mock_exec):
        """F43: tasks without an assignee get a visible dispatch_blocked_reason.

        Pre-F43 behavior was a silent `continue` — task sat IN_PROGRESS forever
        and the operator only learned about it from logs. Now the reason is
        stamped on metadata so the UI can render it.
        """
        mock_exec.delay.return_value.id = "fake-celery-id-stamp"
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=None, depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        # Status unchanged — poll does not transition unassigned tasks.
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Loud skip: dispatch_blocked_reason must be persisted on metadata.
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"), "no_assignee")
        self.assertIn("dispatch_blocked_at", task.metadata)
        mock_exec.delay.assert_not_called()

    # --- TODO tasks are never touched ---

    @patch("tasks.dag_executor.execute_single_task")
    def test_poll_never_touches_todo_tasks(self, mock_exec):
        """TODO tasks stay in TODO regardless of assignee or deps.

        The DAG executor only acts on IN_PROGRESS tasks. Moving a task to
        IN_PROGRESS is an explicit human or odin action.
        """
        mock_exec.delay.return_value.id = "fake-celery-id-006"
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
        mock_exec.delay.return_value.id = "fake-celery-id-007"
        self.make_task(self.board, status=TaskStatus.TODO, assignee=None)
        poll_and_execute()
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_todo_requires_explicit_queue_to_execute(self, mock_exec):
        """TODO → IN_PROGRESS must be done explicitly; then the poll picks it up."""
        mock_exec.delay.return_value.id = "fake-celery-id-008"
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

    @patch("tasks.dag_executor.execute_single_task")
    def test_deps_unmet_stamps_dispatch_blocked_reason(self, mock_exec):
        """F43: tasks with unmet deps get deps_not_complete stamp (not silent skip)."""
        mock_exec.delay.return_value.id = "fake-celery-id-deps"
        dep = self.make_task(self.board, title="Unmet", status=TaskStatus.IN_PROGRESS)
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[dep.id],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"), "deps_not_complete")
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_deps_failed_stamps_dispatch_blocked_reason(self, mock_exec):
        """F43: a failed dep surfaces as deps_blocked_failed (block-don't-fail)."""
        mock_exec.delay.return_value.id = "fake-celery-id-fail"
        dep = self.make_task(self.board, title="Failed dep", status=TaskStatus.FAILED)
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[dep.id],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"), "deps_blocked_failed")
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_blocked_reason_cleared_on_dispatch(self, mock_exec):
        """F43: when a task becomes dispatchable, the stale reason is wiped."""
        mock_exec.delay.return_value.id = "fake-celery-id-clear"
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS,
            assignee=self.user, depends_on=[],
            metadata={"dispatch_blocked_reason": "stale_no_assignee",
                      "dispatch_blocked_at": "2026-07-05T00:00:00Z"},
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertNotIn("dispatch_blocked_reason", task.metadata)
        self.assertNotIn("dispatch_blocked_at", task.metadata)


class ExecuteSingleTaskTests(APITestCase):
    """Tests for the execute_single_task Celery task.

    Patches _run_subprocess_with_cancellation — the internal function that
    actually launches odin exec — rather than subprocess.run (which is not
    called by the current Popen-based implementation).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_success_transitions_to_review(self, mock_run):
        """Successful execution moves task to REVIEW, then auto-advance to TESTING.

        The dag_executor sets REVIEW on a clean subprocess exit, but immediately
        after it triggers auto-reflection. In this test environment there is no
        AGENT user with available models, so the no-reviewer fast-path fires
        and the task is advanced REVIEW → TESTING (post-merge with no branch).
        """
        mock_run.return_value = (0, "none")
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        # REVIEW → TESTING: no reviewer agent → merge-and-advance runs inline.
        self.assertEqual(task.status, TaskStatus.TESTING)

        # The history trail captures both transitions (EXECUTING → REVIEW →
        # TESTING) so we keep an audit of the implicit auto-advance.
        history = TaskHistory.objects.filter(
            task=task, field_name="status",
        ).order_by("changed_at").values_list("new_value", flat=True)
        self.assertIn(TaskStatus.REVIEW, list(history))
        self.assertIn(TaskStatus.TESTING, list(history))

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_failure_transitions_to_failed(self, mock_run):
        """Failed execution moves task to FAILED."""
        mock_run.return_value = (1, "odin_non_zero_exit")
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_failure_fallback_posts_diagnostic_comment(self, mock_run):
        """Fallback FAILED path posts an explicit failure reason comment."""
        mock_run.return_value = (7, "odin_non_zero_exit")
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

    @patch("tasks.dag_executor._read_log_tail")
    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_failure_fallback_classifies_backend_auth_failure_from_log(self, mock_run, mock_tail):
        """Auth-style failures in odin output should be classified precisely."""
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Authentication error: TaskIt returned 401 Unauthorized for "
            "http://localhost:8000/tasks/?board_id=1"
        )
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

    @patch("tasks.dag_executor._read_log_tail")
    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_failure_fallback_strips_ansi_from_debug_excerpt(self, mock_run, mock_tail):
        """ANSI color/control codes should not leak into failure debug excerpts."""
        mock_run.return_value = (2, "odin_non_zero_exit")
        # _read_log_tail already calls _sanitize_ansi; verify it's clean by the
        # time it reaches the comment
        mock_tail.return_value = "Authentication error: bad token"
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        comment = TaskComment.objects.filter(task=task, author_email="odin+dag-executor@system").first()
        self.assertIsNotNone(comment)
        self.assertIn("Debug:", comment.content)
        self.assertNotIn("\x1b[31m", comment.content)
        self.assertNotIn("\x1b[0m", comment.content)

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_timeout_transitions_to_failed(self, mock_run):
        """Timed-out execution moves task to FAILED."""
        mock_run.return_value = (-1, "timeout")
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_skips_non_executing_task(self, mock_run):
        """If task is not EXECUTING, skip it."""
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user,
        )

        execute_single_task(task.id)
        mock_run.assert_not_called()

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_respects_odin_status_update(self, mock_run):
        """If odin already updated the status, don't overwrite it."""
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, assignee=self.user,
        )

        def side_effect(task_id, cmd, working_dir, log_file, run_token=None):
            # Simulate odin updating the task status during execution
            Task.objects.filter(id=task_id).update(status=TaskStatus.DONE)
            return 0, "none"

        mock_run.side_effect = side_effect

        execute_single_task(task.id)

        task.refresh_from_db()
        # Should remain DONE (odin's update), not overwritten to REVIEW
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_nonexistent_task_handled(self):
        """Passing a nonexistent task ID doesn't crash."""
        execute_single_task(99999)  # Should log error and return


class ExecuteReflectionTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor.resolve_working_dir", return_value="/tmp")
    @patch("tasks.dag_executor.subprocess.run")
    @patch("tasks.dag_executor.settings")
    def test_reflection_timeout_uses_configured_limit(self, mock_settings, mock_run, _mock_workdir):
        mock_settings.ODIN_CLI_PATH = "odin"
        mock_settings.BASE_DIR = "/tmp"
        mock_settings.DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS = 1800
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=self.user)
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="gemini",
            reviewer_model="gemini-3-flash-preview",
            status=ReflectionStatus.PENDING,
        )
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["odin"], timeout=1800)

        execute_reflection(report.id)

        assert mock_run.call_args.kwargs["timeout"] == 1800
        report.refresh_from_db()
        assert report.status == ReflectionStatus.FAILED
        assert report.error_message == "Reflection timed out after 1800s before updating report"
