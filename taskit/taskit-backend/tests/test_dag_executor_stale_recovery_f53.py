"""F53 regression tests for _recover_stale_executions queue-backlog race.

Origin: F53 (2026-07-06). At wave concurrency the celery worker pool is
saturated by long-running executions, so a freshly dispatched
execute_single_task job legitimately WAITS IN THE QUEUE for minutes before a
worker picks it up and records active_execution.pid. The no-pid recovery
branch used a 120s deadline, so the stale sweep repeatedly killed healthy
queued dispatches ("Task was marked EXECUTING but no Odin runner process was
recorded") — tasks #127 (rework), #130, #135 each lost an attempt to it.

Fix: the no-pid deadline (DAG_EXECUTOR_QUEUED_STALE_SECONDS) defaults to
1800s — it must cover worst-case queue wait, not "should have started by
now". Genuinely lost dispatches still surface, one task-duration later.

Coverage:
  T1: queued task younger than the deadline is NOT touched
  T2: queued task older than the deadline IS failed with the no-runner reason
  T3: dead-pid task is failed regardless of the queued deadline
  T4: the settings default is >= one full task timeout (1800s)
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import time

from django.conf import settings
from django.test import override_settings

from tests.base import APITestCase
from tasks.dag_executor import _recover_stale_executions
from tasks.models import Board, Task, TaskStatus


class StaleRecoveryQueueBacklogTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = Board.objects.create(name="F53 board", working_dir="/tmp/f53")

    def _executing_task(self, active_execution):
        return Task.objects.create(
            board=self.board,
            title="f53 task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            metadata={"active_execution": active_execution},
        )

    def test_queued_within_deadline_is_untouched(self):
        task = self._executing_task({"queued_at": time.time() - 300})
        with override_settings(DAG_EXECUTOR_QUEUED_STALE_SECONDS=1800):
            _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)

    def test_queued_past_deadline_is_failed_with_no_runner_reason(self):
        task = self._executing_task({"queued_at": time.time() - 2000})
        with override_settings(DAG_EXECUTOR_QUEUED_STALE_SECONDS=1800):
            _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("no Odin runner process", task.metadata["last_failure_reason"])
        self.assertEqual(task.metadata["last_failure_type"], "stale_execution")

    def test_dead_pid_is_failed_regardless_of_queued_deadline(self):
        # PID 2**22+5 is far above macOS/Linux pid ranges — reliably dead.
        task = self._executing_task({"pid": 2**22 + 5, "queued_at": time.time()})
        with override_settings(DAG_EXECUTOR_QUEUED_STALE_SECONDS=1800):
            _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("no longer running", task.metadata["last_failure_reason"])

    def test_default_deadline_covers_a_full_task_timeout(self):
        self.assertGreaterEqual(
            settings.DAG_EXECUTOR_QUEUED_STALE_SECONDS,
            settings.DAG_EXECUTOR_TASK_TIMEOUT_SECONDS,
        )
