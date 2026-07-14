"""Tests for the TaskRun model and run-token write fencing (task #210).

Coverage:
  T1: poll_and_execute (celery_dag strategy) creates a RUNNING TaskRun on spawn
  T2: LocalOdinStrategy.trigger (local strategy) creates a RUNNING TaskRun on spawn
  T3: the subprocess-monitoring loop's heartbeat updates last_heartbeat (+ pid)
  T4: a redispatch supersedes (expires) the prior RUNNING TaskRun
  T5: execution_result with a stale/zombie run_token is rejected 409 stale_run_token
  T6: execution_result with the current run_token is accepted and finishes the run
  T7: _recover_stale_executions marks the abandoned run EXPIRED
  T8: the explicit stop_execution flow marks the run KILLED
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import time
from unittest.mock import patch

from tests.base import APITestCase
from tasks import task_runs
from tasks.dag_executor import _recover_stale_executions, poll_and_execute
from tasks.execution.local import LocalOdinStrategy
from tasks.models import Task, TaskRun, TaskRunState, TaskStatus


class TaskRunSpawnTests(APITestCase):
    """T1/T2 — a TaskRun row is created at spawn, for both strategies."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_poll_and_execute_creates_running_taskrun(self, mock_exec):
        mock_exec.delay.return_value.id = "fake-celery-id"
        task = self.make_task(
            self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user, depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        run_token = mock_exec.delay.call_args[0][1]

        run = TaskRun.objects.get(task=task)
        self.assertEqual(run.state, TaskRunState.RUNNING)
        self.assertEqual(run.run_token, run_token)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.last_heartbeat)
        self.assertIsNone(run.finished_at)

    @patch("tasks.execution.local.subprocess.Popen")
    def test_local_strategy_trigger_creates_running_taskrun(self, mock_popen):
        mock_popen.return_value.pid = 4242
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS, assignee=self.user)

        LocalOdinStrategy().trigger(task)

        task.refresh_from_db()
        run_token = (task.metadata or {}).get("active_execution", {}).get("run_token")
        self.assertTrue(run_token)

        run = TaskRun.objects.get(task=task)
        self.assertEqual(run.state, TaskRunState.RUNNING)
        self.assertEqual(run.run_token, run_token)
        self.assertEqual(run.pid, 4242)


class TaskRunSupersessionTests(APITestCase):
    """T4 — a new dispatch expires the task's prior RUNNING TaskRun."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.EXECUTING)

    def test_start_run_expires_prior_running_row(self):
        old_run = task_runs.start_run(self.task, "old-token")
        self.assertEqual(old_run.state, TaskRunState.RUNNING)

        new_run = task_runs.start_run(self.task, "new-token")

        old_run.refresh_from_db()
        self.assertEqual(old_run.state, TaskRunState.EXPIRED)
        self.assertIsNotNone(old_run.finished_at)
        self.assertEqual(new_run.state, TaskRunState.RUNNING)

        current = task_runs.current_running_run(self.task)
        self.assertEqual(current.run_token, "new-token")


class TaskRunHeartbeatTests(APITestCase):
    """T3 — heartbeat_run touches last_heartbeat (and pid when given)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.EXECUTING)

    def test_heartbeat_updates_timestamp_and_pid(self):
        run = task_runs.start_run(self.task, "tok-1")
        original_heartbeat = run.last_heartbeat

        time.sleep(0.01)
        task_runs.heartbeat_run("tok-1", pid=555)

        run.refresh_from_db()
        self.assertGreater(run.last_heartbeat, original_heartbeat)
        self.assertEqual(run.pid, 555)

    def test_heartbeat_is_noop_for_finished_run(self):
        run = task_runs.start_run(self.task, "tok-2")
        task_runs.finish_run("tok-2", state=TaskRunState.FINISHED)
        run.refresh_from_db()
        finished_at = run.finished_at

        task_runs.heartbeat_run("tok-2", pid=999)

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.FINISHED)
        self.assertIsNone(run.pid)
        self.assertEqual(run.finished_at, finished_at)


class ExecutionResultFencingTests(APITestCase):
    """T5/T6 — write fencing at the execution_result API boundary."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.EXECUTING)

    def _post_execution_result(self, run_token, new_status="REVIEW"):
        return self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "done",
                    "duration_ms": 100.0,
                    "agent": "claude",
                    "metadata": {"taskit_run_token": run_token},
                },
                "status": new_status,
                "updated_by": "claude+claude-sonnet-4-5@odin.agent",
            },
            format="json",
        )

    def test_stale_run_token_rejected_409(self):
        """A zombie holding an old run_token cannot flip status or post results."""
        task_runs.start_run(self.task, "old-token")
        # Task got redispatched: a new run supersedes the old one.
        task_runs.start_run(self.task, "new-token")

        resp = self._post_execution_result("old-token")

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "stale_run_token")

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.EXECUTING)

    def test_current_run_token_accepted(self):
        """The live run's write is accepted and finishes the TaskRun."""
        task_runs.start_run(self.task, "new-token")

        resp = self._post_execution_result("new-token")

        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        # REVIEW auto-advances to TESTING via the post-merge hook when no
        # reviewer agent is configured (test environment has no AGENT users)
        # — the important assertion is that it left EXECUTING, not the exact
        # downstream QA-gate status.
        self.assertNotEqual(self.task.status, TaskStatus.EXECUTING)

        run = TaskRun.objects.get(task=self.task, run_token="new-token")
        self.assertEqual(run.state, TaskRunState.FINISHED)
        self.assertIsNotNone(run.finished_at)

    def test_untracked_task_skips_fencing(self):
        """No TaskRun row for this task means nothing to fence against."""
        resp = self._post_execution_result("any-token")
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertNotEqual(self.task.status, TaskStatus.EXECUTING)


class StaleRecoveryExpiryTests(APITestCase):
    """T7 — the stale-execution watchdog marks the abandoned run EXPIRED."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_recover_stale_executions_marks_run_expired(self):
        task = Task.objects.create(
            board=self.board,
            title="stale run",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            metadata={"active_execution": {
                "pid": 2**22 + 5, "queued_at": time.time(), "run_token": "dead-token",
            }},
        )
        run = task_runs.start_run(task, "dead-token")

        _recover_stale_executions()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.EXPIRED)
        self.assertIsNotNone(run.finished_at)


class StopExecutionKillsRunTests(APITestCase):
    """T8 — an operator stop marks the TaskRun KILLED, fencing future writes."""

    @patch("tasks.execution.get_strategy")
    def test_stop_execution_marks_run_killed(self, mock_get_strategy):
        mock_strategy = mock_get_strategy.return_value
        mock_strategy.stop.return_value = {"ok": True, "engine": "local"}

        board = self.make_board()
        task = self.make_task(
            board,
            status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"run_token": "stop-me", "pid": 1234}},
        )
        task_runs.start_run(task, "stop-me")

        resp = self.client.post(f"/tasks/{task.id}/stop_execution/", {
            "updated_by": "alice@test.com",
            "target_status": "TODO",
            "reason": "user_drag_stop_confirm",
        }, format="json")
        self.assertEqual(resp.status_code, 200)

        run = TaskRun.objects.get(task=task, run_token="stop-me")
        self.assertEqual(run.state, TaskRunState.KILLED)
        self.assertIsNotNone(run.finished_at)
