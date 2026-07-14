"""Tests for the CANCELED Task status (fable task 192).

The Task lifecycle is governed by *which code path* writes the status
(see docs/breadcrumb_analysis/task-state-machine-celery-automation/FLOW.md).
CANCELED is the new terminal-neutral status: an operator decides a work
item is no longer needed and parks it out of the active pipeline.

Acceptance contract for the CANCELED gate:

  * Allowed transitions to CANCELED come from BACKLOG/TODO/IN_PROGRESS/
    FAILED/REVIEW/TESTING. The serializer/view enforces this and emits
    a 400 with a human-readable reason.
  * From EXECUTING — only via the ``stop_execution`` endpoint's
    ``target_status`` field. A direct PATCH that sets ``status=CANCELED``
    on an EXECUTING task is rejected; the operator must go through the
    stop flow so the running process is actually terminated.
  * From DONE — never. 400 with a clear reason.
  * Once CANCELED: the dag_executor never picks it, the stale-watchdog
    never fails it, scheduling skips re-releases against it, and
    diagnostic scripts treat it as its own bucket (not DONE, not FAILED).

These tests pin every edge in the matrix above; they ALSO guard against
silent regressions in the dag_executor (a CANCELED task must not be
counted toward concurrency or dispatched).
"""

from unittest.mock import patch

from .base import APITestCase
from tasks.dag_executor import poll_and_execute, _recover_stale_executions
from tasks.dependencies import DepStatus, check_deps, get_unmet_deps
from tasks.models import (
    ScheduleRunStatus,
    Task,
    TaskHistory,
    TaskSchedule,
    TaskScheduleRun,
    TaskStatus,
)
from tasks.state_transitions import (
    CANCEL_ALLOWED_VIA_API_FROM,
    CANCEL_ALLOWED_VIA_STOP_FROM,
    FULLY_TERMINAL_STATUSES,
    TERMINAL_NEUTRAL_STATUSES,
    is_cancel_transition_allowed,
)
from tasks.scheduling import maybe_finalize_schedule_run


# ──────────────────────────────────────────────────────────────────────
# 1. Transition matrix: pure helper
# ──────────────────────────────────────────────────────────────────────


class CancelTransitionMatrixTests(APITestCase):
    """Pure unit tests for the transition matrix helper.

    These pin the policy without involving the API/dag_executor paths.
    They MUST pass on top of any implementation that wires the helper
    into the request lifecycle.
    """

    def test_matrix_includes_all_non_done_non_executing_states(self):
        self.assertEqual(
            CANCEL_ALLOWED_VIA_API_FROM,
            {
                TaskStatus.BACKLOG,
                TaskStatus.TODO,
                TaskStatus.IN_PROGRESS,
                TaskStatus.REVIEW,
                TaskStatus.TESTING,
                TaskStatus.FAILED,
            },
        )

    def test_stop_flow_expands_origin_set_with_executing(self):
        self.assertIn(TaskStatus.EXECUTING, CANCEL_ALLOWED_VIA_STOP_FROM)
        self.assertTrue(
            CANCEL_ALLOWED_VIA_STOP_FROM > CANCEL_ALLOWED_VIA_API_FROM
            or CANCEL_ALLOWED_VIA_STOP_FROM
            != CANCEL_ALLOWED_VIA_API_FROM
        )

    def test_helper_passes_through_non_cancel_targets(self):
        # Anything that's not CANCELED is outside this helper's domain.
        self.assertTrue(is_cancel_transition_allowed("TODO", "IN_PROGRESS"))
        self.assertTrue(is_cancel_transition_allowed("FAILED", "TODO"))
        self.assertTrue(is_cancel_transition_allowed("DONE", "FAILED"))

    def test_helper_allows_api_cancel_from_each_listed_origin(self):
        for origin in CANCEL_ALLOWED_VIA_API_FROM:
            self.assertTrue(
                is_cancel_transition_allowed(origin, TaskStatus.CANCELED),
                f"API cancel should be allowed from {origin}",
            )

    def test_helper_blocks_api_cancel_from_done(self):
        # DONE is fully terminal — a finished task cannot be retroactively
        # canceled (it's already shipped).
        self.assertFalse(
            is_cancel_transition_allowed(TaskStatus.DONE, TaskStatus.CANCELED)
        )

    def test_helper_blocks_api_cancel_from_executing(self):
        # EXECUTING needs the stop flow — a direct PATCH would leak the
        # running process. Only stop_execution's target_status may land
        # there.
        self.assertFalse(
            is_cancel_transition_allowed(TaskStatus.EXECUTING, TaskStatus.CANCELED)
        )

    def test_helper_allows_stop_cancel_from_executing(self):
        self.assertTrue(is_cancel_transition_allowed(
            TaskStatus.EXECUTING, TaskStatus.CANCELED, via_stop_execution=True,
        ))

    def test_helper_still_blocks_done_via_stop_flow(self):
        # Even via stop_execution, DONE is immutable.
        self.assertFalse(is_cancel_transition_allowed(
            TaskStatus.DONE, TaskStatus.CANCELED, via_stop_execution=True,
        ))


# ──────────────────────────────────────────────────────────────────────
# 2. API behavior: PATCH /tasks/:id/ with status=CANCELED
# ──────────────────────────────────────────────────────────────────────


class CancelApiTransitionTests(APITestCase):
    """Integration tests for status transitions TO CANCELED via the API."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def _patch_to_cancel(self, task, *, updated_by="alice@test.com"):
        return self.client.put(
            f"/tasks/{task.id}/",
            {"status": "CANCELED", "updated_by": updated_by},
            format="json",
        )

    def test_patch_cancel_from_backlog(self):
        task = self.make_task(self.board, status=TaskStatus.BACKLOG)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)
        history = TaskHistory.objects.filter(
            task=task, field_name="status",
        ).order_by("-changed_at").first()
        self.assertEqual(history.new_value, TaskStatus.CANCELED)

    def test_patch_cancel_from_todo(self):
        task = self.make_task(self.board, status=TaskStatus.TODO)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_patch_cancel_from_in_progress(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_patch_cancel_from_failed(self):
        task = self.make_task(self.board, status=TaskStatus.FAILED)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_patch_cancel_from_review(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_patch_cancel_from_testing(self):
        task = self.make_task(self.board, status=TaskStatus.TESTING)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_patch_cancel_from_done_rejected(self):
        """DONE is fully terminal — cancel must be rejected with a clear error."""
        task = self.make_task(self.board, status=TaskStatus.DONE)
        resp = self._patch_to_cancel(task)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("CANCELED", str(resp.data).upper())
        # Status MUST be unchanged.
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        # No spurious history row.
        history = TaskHistory.objects.filter(
            task=task, field_name="status", new_value=TaskStatus.CANCELED,
        )
        self.assertFalse(history.exists())

    def test_patch_cancel_from_executing_rejected(self):
        """An EXECUTING task must go through stop_execution to land in CANCELED.

        A direct PATCH that sets status=CANCELED on EXECUTING would leave
        the running process alive — the operator gets silent state drift
        between the subprocess and the DB row. The stop flow writes
        metadata guards first and tears down the agent; only then may the
        row transition to CANCELED.
        """
        task = self.make_task(
            self.board,
            status=TaskStatus.EXECUTING,
            assignee=self.user,
            metadata={"active_execution": {"run_token": "run_x", "pid": 9999}},
        )
        resp = self._patch_to_cancel(task)
        # 400 = explicit transition validator; 409 = EXECUTING_LOCK (the
        # executing-mutation guard already rejects in-flight changes with
        # a 409). Both are valid rejections — what matters is that the
        # API never silently flips an EXECUTING task to CANCELED.
        self.assertIn(resp.status_code, (400, 409), resp.data)
        # Status untouched.
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)

    def test_stop_execution_can_cancel_running_task(self):
        """The stop_execution endpoint IS allowed to land an EXECUTING task in CANCELED."""
        task = self.make_task(
            self.board,
            status=TaskStatus.EXECUTING,
            assignee=self.user,
            metadata={"active_execution": {"run_token": "run_x", "pid": 9999}},
        )
        with patch("tasks.views._attempt_odin_stop",
                   return_value={"ok": True, "engine": "mock"}):
            resp = self.client.post(
                f"/tasks/{task.id}/stop_execution/",
                {
                    "updated_by": "alice@test.com",
                    "target_status": "CANCELED",
                    "reason": "spec_abandoned",
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)

    def test_terminal_categories_partition_correctly(self):
        """Sanity: DONE is fully terminal, CANCELED is terminal-neutral."""
        self.assertIn(TaskStatus.DONE, FULLY_TERMINAL_STATUSES)
        self.assertNotIn(TaskStatus.CANCELED, FULLY_TERMINAL_STATUSES)
        self.assertIn(TaskStatus.CANCELED, TERMINAL_NEUTRAL_STATUSES)


# ──────────────────────────────────────────────────────────────────────
# 3. Serializer validation: status choice + transition guard
# ──────────────────────────────────────────────────────────────────────


class CancelSerializerTests(APITestCase):
    """The serializer-level guard — surfaces transitions even before the view."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_update_serializer_accepts_canceled_choice(self):
        """UpdateTaskSerializer must include CANCELED as a valid choice."""
        from tasks.serializers import UpdateTaskSerializer

        ser = UpdateTaskSerializer(data={
            "status": "CANCELED", "updated_by": "alice@test.com",
        })
        self.assertTrue(ser.is_valid(), ser.errors)

    def test_create_serializer_accepts_canceled_choice(self):
        """CreateTaskSerializer must accept CANCELED as an initial status."""
        from tasks.serializers import CreateTaskSerializer

        ser = CreateTaskSerializer(data={
            "board_id": self.board.id,
            "title": "Already-canceled",
            "status": "CANCELED",
            "created_by": "alice@test.com",
        })
        self.assertTrue(ser.is_valid(), ser.errors)


# ──────────────────────────────────────────────────────────────────────
# 4. dag_executor: CANCELED must never be dispatched
# ──────────────────────────────────────────────────────────────────────


class ExecutorSkipsCanceledTests(APITestCase):
    """The polling executor and stale-recovery watchdog must ignore CANCELED."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_poll_never_picks_canceled_task(self, mock_exec):
        """Even with satisfied deps and an assignee, a CANCELED task is invisible to poll."""
        mock_exec.delay.return_value.id = "fake-celery-cancel"
        task = self.make_task(
            self.board,
            status=TaskStatus.CANCELED,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        # Status unchanged — the executor never touches CANCELED.
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)
        mock_exec.delay.assert_not_called()
        # No dispatch_blocked_reason stamp either — CANCELED is not even
        # *considered*, never mind skipped.
        self.assertNotIn("dispatch_blocked_reason", task.metadata or {})

    @patch("tasks.dag_executor.execute_single_task")
    def test_canceled_task_does_not_reduce_concurrency(self, mock_exec):
        """A CANCELED task must NOT subtract from available executor slots.

        The slot budget is ``max - EXECUTING_count``. If a CANCELED task
        were counted as an active execution, the poller would silently
        under-utilize the worker pool.
        """
        mock_exec.delay.return_value.id = "fake-celery-slot"
        with patch("tasks.dag_executor.settings") as mock_settings:
            mock_settings.DAG_EXECUTOR_MAX_CONCURRENCY = 2

            # A CANCELED task — must NOT count toward active executions.
            self.make_task(
                self.board,
                title="Canceled",
                status=TaskStatus.CANCELED,
                assignee=self.user,
                depends_on=[],
            )
            # A ready IN_PROGRESS task — must dispatch in the same poll.
            ready = self.make_task(
                self.board,
                title="Ready",
                status=TaskStatus.IN_PROGRESS,
                assignee=self.user,
                depends_on=[],
            )
            poll_and_execute()

        ready.refresh_from_db()
        self.assertEqual(ready.status, TaskStatus.EXECUTING)
        mock_exec.delay.assert_called_once()
        args = mock_exec.delay.call_args[0]
        self.assertEqual(args[0], ready.id)

    def test_stale_recovery_watchdog_skips_canceled(self):
        """_recover_stale_executions must never reassign a CANCELED task to FAILED."""
        task = self.make_task(
            self.board,
            status=TaskStatus.CANCELED,
            assignee=self.user,
        )
        _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELED)


# ──────────────────────────────────────────────────────────────────────
# 5. Dependency gating: CANCELED dep keeps dependent WAITING (not BLOCKED)
# ──────────────────────────────────────────────────────────────────────


class CanceledDependencyGateTests(APITestCase):
    """A CANCELED dependency is its own bucket: not complete, not failed.

    The dependent stays WAITING (deps_not_complete) so the operator sees
    the unchanged board, but no false "FAILED dep" stamp is written. The
    operator decides whether to drop the dep, swap it, or cancel the
    downstream too.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_canceled_dep_keeps_dependent_waiting(self):
        dep = self.make_task(self.board, status=TaskStatus.CANCELED)
        task = self.make_task(self.board, depends_on=[dep.id])
        # Dependent stays WAITING — CANCELED is not in COMPLETED, so the
        # dep isn't satisfied; CANCELED is also not FAILED, so the
        # dependent isn't BLOCKED-failed.
        self.assertEqual(check_deps(task), DepStatus.WAITING)

    def test_canceled_dep_is_listed_in_unmet_deps(self):
        dep = self.make_task(self.board, status=TaskStatus.CANCELED)
        task = self.make_task(self.board, depends_on=[dep.id])
        unmet = get_unmet_deps(task)
        self.assertEqual(len(unmet), 1)
        self.assertEqual(unmet[0].id, dep.id)


# ──────────────────────────────────────────────────────────────────────
# 6. Schedule finalize: CANCELED is its own schedule-run terminal
# ──────────────────────────────────────────────────────────────────────


class CanceledScheduleFinalizeTests(APITestCase):
    """When a scheduled task lands in CANCELED, the run is finalized correctly."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_maybe_finalize_schedule_run_marks_canceled(self):
        from datetime import timedelta
        from django.utils import timezone as dj_timezone

        task = self.make_task(self.board, status=TaskStatus.CANCELED)
        starts_at = dj_timezone.now() + timedelta(hours=1)
        schedule = TaskSchedule.objects.create(
            board=self.board,
            kind="ONE_TIME",
            status="COMPLETED",
            timezone="UTC",
            template_title="recurring cancelled",
            template_priority="MEDIUM",
            starts_at_local=starts_at,
            starts_at_utc=starts_at,
            created_by="alice@test.com",
        )
        run = TaskScheduleRun.objects.create(
            schedule=schedule, run_number=1, task=task,
            scheduled_for_utc=starts_at, status=ScheduleRunStatus.RELEASED,
        )
        task.current_schedule_run = run
        task.save(update_fields=["current_schedule_run"])

        maybe_finalize_schedule_run(task, TaskStatus.CANCELED)

        run.refresh_from_db()
        self.assertEqual(run.status, ScheduleRunStatus.CANCELED)
        self.assertEqual(run.terminal_task_status, TaskStatus.CANCELED)
        self.assertIsNotNone(run.finished_at_utc)
