"""Tests for _auto_start_planned_tasks in tasks.consumers.

The function does NOT exist yet — every test here must fail with an
ImportError or AttributeError when run. This file establishes the
behavioral contract that the implementation must satisfy.

Behavioral contract:
  1. Flag is False (default): complete no-op. No status changes, no history,
     no trigger calls.
  2. Flag is True, all tasks assigned: TODO tasks → IN_PROGRESS, history
     written, trigger() called once per task.
  3. Flag is True, some unassigned: unassigned tasks still transition + get
     history, but trigger() is NOT called for them. Assigned tasks get
     full treatment.
  4. Flag is True, mixed statuses: only TODO tasks are touched. IN_PROGRESS,
     DONE, BACKLOG, etc. are left untouched.
  5. Flag is True, scoping: only tasks belonging to the given spec are
     affected. Tasks on the same board under other specs (or no spec) are
     untouched.
  6. Error isolation: if trigger() raises for one task, other tasks still
     complete their transition + history. The exception must not propagate.
"""

from unittest.mock import MagicMock, call, patch

from tasks.consumers import _auto_start_planned_tasks
from tasks.models import Task, TaskHistory, TaskStatus

from .base import APITestCase

SYSTEM_ACTOR = "system@taskit"


class AutoStartFlagFalseTests(APITestCase):
    """Case 1: auto_start_planned_tasks=False → complete no-op."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=False)
        self.spec = self.make_spec(self.board)
        self.user = self.make_user()

    @patch("tasks.execution.get_strategy")
    def test_flag_false_leaves_tasks_in_todo(self, mock_get_strategy):
        """When flag is False, TODO tasks stay TODO."""
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TODO)

    @patch("tasks.execution.get_strategy")
    def test_flag_false_creates_no_history(self, mock_get_strategy):
        """When flag is False, no TaskHistory records are created."""
        self.make_task(self.board, spec=self.spec, status=TaskStatus.TODO, assignee=self.user)
        before_count = TaskHistory.objects.count()

        _auto_start_planned_tasks(self.spec)

        self.assertEqual(TaskHistory.objects.count(), before_count)

    @patch("tasks.execution.get_strategy")
    def test_flag_false_never_calls_strategy(self, mock_get_strategy):
        """When flag is False, the execution strategy is never consulted."""
        self.make_task(self.board, spec=self.spec, status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        mock_get_strategy.assert_not_called()


class AutoStartAllAssignedTests(APITestCase):
    """Case 2: flag True, all tasks assigned → full transition + history + trigger."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=True)
        self.spec = self.make_spec(self.board)
        self.user = self.make_user()

    @patch("tasks.execution.get_strategy")
    def test_todo_tasks_transition_to_in_progress(self, mock_get_strategy):
        """All TODO tasks for the spec move to IN_PROGRESS."""
        mock_get_strategy.return_value = MagicMock()

        task_a = self.make_task(self.board, title="Task A", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)
        task_b = self.make_task(self.board, title="Task B", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        task_a.refresh_from_db()
        task_b.refresh_from_db()
        self.assertEqual(task_a.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task_b.status, TaskStatus.IN_PROGRESS)

    @patch("tasks.execution.get_strategy")
    def test_history_records_created_for_each_task(self, mock_get_strategy):
        """A TaskHistory row is written for each transitioned task."""
        mock_get_strategy.return_value = MagicMock()

        task_a = self.make_task(self.board, title="Task A", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)
        task_b = self.make_task(self.board, title="Task B", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        for task in (task_a, task_b):
            history = TaskHistory.objects.filter(task=task, field_name="status").first()
            self.assertIsNotNone(history, f"No history for task {task.title}")
            self.assertEqual(history.old_value, TaskStatus.TODO)
            self.assertEqual(history.new_value, TaskStatus.IN_PROGRESS)
            self.assertEqual(history.changed_by, SYSTEM_ACTOR)

    @patch("tasks.execution.get_strategy")
    def test_strategy_trigger_called_once_per_task(self, mock_get_strategy):
        """trigger() is called exactly once for each transitioned task."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        task_a = self.make_task(self.board, title="Task A", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)
        task_b = self.make_task(self.board, title="Task B", spec=self.spec,
                                status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        self.assertEqual(mock_strategy.trigger.call_count, 2)
        triggered_ids = {c.args[0].id for c in mock_strategy.trigger.call_args_list}
        self.assertEqual(triggered_ids, {task_a.id, task_b.id})


class AutoStartSomeUnassignedTests(APITestCase):
    """Case 3: flag True, mixed assignee presence."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=True)
        self.spec = self.make_spec(self.board)
        self.user = self.make_user()

    @patch("tasks.execution.get_strategy")
    def test_unassigned_task_still_transitions(self, mock_get_strategy):
        """Unassigned tasks still move to IN_PROGRESS (don't lie about kanban state)."""
        mock_get_strategy.return_value = MagicMock()
        unassigned = self.make_task(self.board, title="Unassigned", spec=self.spec,
                                    status=TaskStatus.TODO, assignee=None)

        _auto_start_planned_tasks(self.spec)

        unassigned.refresh_from_db()
        self.assertEqual(unassigned.status, TaskStatus.IN_PROGRESS)

    @patch("tasks.execution.get_strategy")
    def test_unassigned_task_gets_history(self, mock_get_strategy):
        """Unassigned tasks still get a TaskHistory record."""
        mock_get_strategy.return_value = MagicMock()
        unassigned = self.make_task(self.board, title="Unassigned", spec=self.spec,
                                    status=TaskStatus.TODO, assignee=None)

        _auto_start_planned_tasks(self.spec)

        history = TaskHistory.objects.filter(task=unassigned, field_name="status").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.TODO)
        self.assertEqual(history.new_value, TaskStatus.IN_PROGRESS)
        self.assertEqual(history.changed_by, SYSTEM_ACTOR)

    @patch("tasks.execution.get_strategy")
    def test_trigger_not_called_for_unassigned_task(self, mock_get_strategy):
        """trigger() is NOT called for unassigned tasks — no agent to dispatch to."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        unassigned = self.make_task(self.board, title="Unassigned", spec=self.spec,
                                    status=TaskStatus.TODO, assignee=None)

        _auto_start_planned_tasks(self.spec)

        # trigger was never called (no assigned tasks at all)
        mock_strategy.trigger.assert_not_called()

    @patch("tasks.execution.get_strategy")
    def test_assigned_task_is_triggered_unassigned_is_not(self, mock_get_strategy):
        """Assigned task gets trigger(), unassigned task does not — both transition."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        assigned = self.make_task(self.board, title="Assigned", spec=self.spec,
                                  status=TaskStatus.TODO, assignee=self.user)
        unassigned = self.make_task(self.board, title="Unassigned", spec=self.spec,
                                    status=TaskStatus.TODO, assignee=None)

        _auto_start_planned_tasks(self.spec)

        # Both transitioned
        assigned.refresh_from_db()
        unassigned.refresh_from_db()
        self.assertEqual(assigned.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(unassigned.status, TaskStatus.IN_PROGRESS)

        # Only assigned was triggered
        self.assertEqual(mock_strategy.trigger.call_count, 1)
        self.assertEqual(mock_strategy.trigger.call_args.args[0].id, assigned.id)


class AutoStartMixedStatusTests(APITestCase):
    """Case 4: only TODO tasks are touched; other statuses are left alone."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=True)
        self.spec = self.make_spec(self.board)
        self.user = self.make_user()

    @patch("tasks.execution.get_strategy")
    def test_only_todo_tasks_are_transitioned(self, mock_get_strategy):
        """IN_PROGRESS, DONE, BACKLOG, FAILED tasks are untouched; only TODO moves."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        todo = self.make_task(self.board, title="TODO task", spec=self.spec,
                              status=TaskStatus.TODO, assignee=self.user)
        already_in_progress = self.make_task(self.board, title="Already in progress",
                                             spec=self.spec, status=TaskStatus.IN_PROGRESS,
                                             assignee=self.user)
        done_task = self.make_task(self.board, title="Done", spec=self.spec,
                                   status=TaskStatus.DONE, assignee=self.user)
        backlog_task = self.make_task(self.board, title="Backlog", spec=self.spec,
                                     status=TaskStatus.BACKLOG, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        todo.refresh_from_db()
        already_in_progress.refresh_from_db()
        done_task.refresh_from_db()
        backlog_task.refresh_from_db()

        self.assertEqual(todo.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(already_in_progress.status, TaskStatus.IN_PROGRESS)  # unchanged
        self.assertEqual(done_task.status, TaskStatus.DONE)                   # unchanged
        self.assertEqual(backlog_task.status, TaskStatus.BACKLOG)              # unchanged

        # trigger called only for the TODO task
        self.assertEqual(mock_strategy.trigger.call_count, 1)
        self.assertEqual(mock_strategy.trigger.call_args.args[0].id, todo.id)

    @patch("tasks.execution.get_strategy")
    def test_no_history_for_non_todo_tasks(self, mock_get_strategy):
        """No TaskHistory is written for tasks that were not in TODO."""
        mock_get_strategy.return_value = MagicMock()

        in_progress = self.make_task(self.board, title="WIP", spec=self.spec,
                                     status=TaskStatus.IN_PROGRESS, assignee=self.user)
        # Clear any history that existed before calling the function
        TaskHistory.objects.filter(task=in_progress).delete()

        _auto_start_planned_tasks(self.spec)

        # No new history for the IN_PROGRESS task
        self.assertFalse(
            TaskHistory.objects.filter(task=in_progress, field_name="status").exists()
        )


class AutoStartScopingTests(APITestCase):
    """Case 5: only tasks under the given spec are affected."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=True)
        self.spec = self.make_spec(self.board, odin_id="sp_target", title="Target Spec")
        self.other_spec = self.make_spec(self.board, odin_id="sp_other", title="Other Spec")
        self.user = self.make_user()

    @patch("tasks.execution.get_strategy")
    def test_tasks_under_other_spec_are_not_touched(self, mock_get_strategy):
        """Tasks belonging to a different spec are not transitioned."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        target_task = self.make_task(self.board, title="Target", spec=self.spec,
                                     status=TaskStatus.TODO, assignee=self.user)
        other_task = self.make_task(self.board, title="Other spec task", spec=self.other_spec,
                                    status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        target_task.refresh_from_db()
        other_task.refresh_from_db()

        self.assertEqual(target_task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(other_task.status, TaskStatus.TODO)  # untouched

    @patch("tasks.execution.get_strategy")
    def test_tasks_with_no_spec_are_not_touched(self, mock_get_strategy):
        """Tasks with no spec association on the same board are not touched."""
        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        target_task = self.make_task(self.board, title="Target", spec=self.spec,
                                     status=TaskStatus.TODO, assignee=self.user)
        specless_task = self.make_task(self.board, title="No spec", spec=None,
                                       status=TaskStatus.TODO, assignee=self.user)

        _auto_start_planned_tasks(self.spec)

        target_task.refresh_from_db()
        specless_task.refresh_from_db()

        self.assertEqual(target_task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(specless_task.status, TaskStatus.TODO)  # untouched

        # trigger called only for the spec's task
        self.assertEqual(mock_strategy.trigger.call_count, 1)
        self.assertEqual(mock_strategy.trigger.call_args.args[0].id, target_task.id)


class AutoStartErrorIsolationTests(APITestCase):
    """Case 6: trigger() exception for one task must not abort the others."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(auto_start_planned_tasks=True)
        self.spec = self.make_spec(self.board)
        self.user_a = self.make_user(name="Alice", email="alice@test.com")
        self.user_b = self.make_user(name="Bob", email="bob@test.com")

    @patch("tasks.execution.get_strategy")
    def test_exception_in_trigger_does_not_propagate(self, mock_get_strategy):
        """An exception from trigger() must be caught; the function must return normally."""
        mock_strategy = MagicMock()
        mock_strategy.trigger.side_effect = RuntimeError("trigger exploded")
        mock_get_strategy.return_value = mock_strategy

        self.make_task(self.board, title="Task A", spec=self.spec,
                       status=TaskStatus.TODO, assignee=self.user_a)

        # Must not raise
        try:
            _auto_start_planned_tasks(self.spec)
        except Exception as exc:
            self.fail(f"_auto_start_planned_tasks raised unexpectedly: {exc!r}")

    @patch("tasks.execution.get_strategy")
    def test_exception_on_one_task_does_not_prevent_others_transitioning(self, mock_get_strategy):
        """When trigger() raises for task A, task B still transitions and is triggered."""
        task_a = self.make_task(self.board, title="Task A (will fail trigger)",
                                spec=self.spec, status=TaskStatus.TODO, assignee=self.user_a)
        task_b = self.make_task(self.board, title="Task B (must succeed)",
                                spec=self.spec, status=TaskStatus.TODO, assignee=self.user_b)

        def trigger_side_effect(task):
            if task.id == task_a.id:
                raise RuntimeError("trigger failed for task A")

        mock_strategy = MagicMock()
        mock_strategy.trigger.side_effect = trigger_side_effect
        mock_get_strategy.return_value = mock_strategy

        _auto_start_planned_tasks(self.spec)

        # Both tasks must have transitioned
        task_a.refresh_from_db()
        task_b.refresh_from_db()
        self.assertEqual(task_a.status, TaskStatus.IN_PROGRESS,
                         "Task A should still transition even when trigger raised")
        self.assertEqual(task_b.status, TaskStatus.IN_PROGRESS,
                         "Task B should transition regardless of Task A's trigger failure")

    @patch("tasks.execution.get_strategy")
    def test_exception_on_one_task_does_not_prevent_history_for_others(self, mock_get_strategy):
        """History records for all tasks are created even when trigger() raises for one."""
        task_a = self.make_task(self.board, title="Task A (trigger fails)",
                                spec=self.spec, status=TaskStatus.TODO, assignee=self.user_a)
        task_b = self.make_task(self.board, title="Task B",
                                spec=self.spec, status=TaskStatus.TODO, assignee=self.user_b)

        def trigger_side_effect(task):
            if task.id == task_a.id:
                raise RuntimeError("trigger failed for task A")

        mock_strategy = MagicMock()
        mock_strategy.trigger.side_effect = trigger_side_effect
        mock_get_strategy.return_value = mock_strategy

        _auto_start_planned_tasks(self.spec)

        # History must exist for both tasks
        for task in (task_a, task_b):
            history = TaskHistory.objects.filter(task=task, field_name="status").first()
            self.assertIsNotNone(history, f"Missing history for {task.title}")
            self.assertEqual(history.new_value, TaskStatus.IN_PROGRESS)
            self.assertEqual(history.changed_by, SYSTEM_ACTOR)
