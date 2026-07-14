"""Tests for the TaskRun-lease reconciler (task #211).

Trust bucket: a crashed celery worker must never leave a sandbox VM running
with no supervisor. This reconciler is the single periodic owner of "the
runner is gone" detection over TaskRun rows (the fencing/heartbeat records
added in task #210), independent of the DAG dispatch cadence so it covers
every execution strategy.

Coverage:
  T1: expired lease + dead pid -> TaskRun EXPIRED, task FAILED -> auto-requeued
  T2: expired lease + live verified pid -> pid group killed, sandbox swept,
      TaskRun KILLED, task auto-requeued
  T3: healthy run (fresh heartbeat) -> untouched
  T4: worker-boot hook reaps a pre-existing orphan immediately (no waiting
      for the first periodic Beat pass)
  T5: the requeue comment trail explains what happened and why
  T6: the reconciler is registered on the Beat schedule
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import shutil
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from tests.base import APITestCase
from tasks import task_runs
from tasks.dag_executor import reconcile_task_runs
from tasks.models import Board, Task, TaskComment, TaskRun, TaskRunState, TaskStatus


def _pid_alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TaskRunLeaseReconcilerTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = Board.objects.create(name="lease board", working_dir="/tmp/lease")
        self.user = self.make_user(name="Runner", email="runner@test.com")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="lease_reconciler_"))
        self._spawned = []

    def tearDown(self):
        for proc in self._spawned:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _spawn_odin_exec_lookalike(self, task_id):
        """Spawn a real process whose argv is [<path>/odin, exec, <task_id>]
        (same technique as test_dag_executor_orphan_adopt.py) so cmdline
        verification behaves exactly as it would for a real orphaned run."""
        odin_bin = self.tmp_dir / "odin"
        odin_bin.write_text("#!/bin/sh\nsleep 60\n")
        odin_bin.chmod(0o755)
        proc = subprocess.Popen(
            [str(odin_bin), "exec", str(task_id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(proc)
        return proc.pid

    def _executing_task_with_run(self, pid, stale_seconds=None):
        task = Task.objects.create(
            board=self.board,
            title="lease task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
        )
        run = task_runs.start_run(task, f"lease-token-{task.id}", pid=pid)
        if stale_seconds is not None:
            TaskRun.objects.filter(pk=run.pk).update(
                last_heartbeat=timezone.now() - timedelta(seconds=stale_seconds),
            )
            run.refresh_from_db()
        return task, run

    # ── T1: expired lease, dead pid -> EXPIRED + auto-requeue ───────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180)
    def test_expired_lease_dead_pid_marks_expired_and_requeues(self):
        task, run = self._executing_task_with_run(pid=2**22 + 5, stale_seconds=200)

        reconcile_task_runs()

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.EXPIRED)
        self.assertIsNotNone(run.finished_at)

        task.refresh_from_db()
        # stale_execution is an auto-requeue class (W4.4/W4.10) -> back to IN_PROGRESS
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        history = task.metadata.get("auto_redispatch_history") or []
        self.assertTrue(any(h.get("class") == "stale_execution" for h in history))

    # ── T2: expired lease, live verified pid -> killed + swept ──────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180)
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_expired_lease_live_pid_is_killed_and_marked_killed(self, mock_sweep):
        mock_sweep.return_value = []
        task = Task.objects.create(
            board=self.board,
            title="live lease task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
        )
        live_pid = self._spawn_odin_exec_lookalike(task.id)
        proc = self._spawned[-1]
        run = task_runs.start_run(task, "lease-token-live", pid=live_pid)
        TaskRun.objects.filter(pk=run.pk).update(
            last_heartbeat=timezone.now() - timedelta(seconds=200),
        )

        self.assertTrue(_pid_alive(live_pid))
        reconcile_task_runs()

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.fail("reconciler must kill the live orphan's process group")

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.KILLED)
        mock_sweep.assert_called_once()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    # ── T3: healthy run untouched ────────────────────────────────────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180)
    def test_healthy_run_is_untouched(self):
        task, run = self._executing_task_with_run(pid=2**22 + 6)  # fresh heartbeat

        reconcile_task_runs()

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.RUNNING)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)

    # ── T4: worker-boot hook reaps immediately ──────────────────────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180)
    def test_worker_boot_hook_reaps_pre_existing_orphan_immediately(self):
        """Task #211 scope item 3: a restart must not leave an orphan waiting
        for the first periodic Beat pass — the worker_ready hook reconciles
        immediately at boot."""
        task, run = self._executing_task_with_run(pid=2**22 + 7, stale_seconds=200)

        from celery.signals import worker_ready
        worker_ready.send(sender=None)

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.EXPIRED)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    # ── T5: comment trail explains what + why ───────────────────────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180)
    def test_requeue_comment_explains_what_and_why(self):
        task, run = self._executing_task_with_run(pid=2**22 + 8, stale_seconds=200)

        reconcile_task_runs()

        combined = "\n".join(
            c.content for c in TaskComment.objects.filter(task=task).order_by("id")
        )
        self.assertIn("heartbeat", combined.lower())
        self.assertIn("stale_execution", combined)


class TaskRunReconcilerScheduleTests(APITestCase):
    def test_beat_schedule_registers_reconciler(self):
        from django.conf import settings

        tasks_scheduled = {v.get("task") for v in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("tasks.dag_executor.reconcile_task_runs", tasks_scheduled)


class TaskRunProgressLivenessTests(APITestCase):
    """Trust bucket (task #235): a run is alive only if it is MAKING PROGRESS.

    The heartbeat-only lease check cannot tell a live supervisor apart from a
    live supervisor supervising a *dead* agent — a stuck/hung sandbox process
    keeps heartbeating while the agent inside emits zero output (the exact
    'host slept, process up, zero output for hours' case). These tests pin the
    trace-mtime progress signal: a RUNNING run whose trace file is idle past
    the progress window is reaped even with a fresh heartbeat and a live pid.

    Coverage:
      P1: fresh trace -> untouched
      P2: live verified pid + fresh heartbeat + stale trace -> killed + requeued
          (the 231/232 zombie case — the lease check must NOT fire here; only
          the progress check catches it)
      P3: no trace file -> falls back to pid/heartbeat liveness (untouched)
      P4: stale trace but task already advanced -> run closed, no requeue
    """

    def setUp(self):
        super().setUp()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="progress_liveness_"))
        self.board = Board.objects.create(
            name="progress board", working_dir=str(self.tmp_dir)
        )
        self.user = self.make_user(name="Agent", email="agent@test.com")
        self._spawned = []

    def tearDown(self):
        for proc in self._spawned:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _spawn_odin_exec_lookalike(self, task_id):
        odin_bin = self.tmp_dir / "odin"
        odin_bin.write_text("#!/bin/sh\nsleep 60\n")
        odin_bin.chmod(0o755)
        proc = subprocess.Popen(
            [str(odin_bin), "exec", str(task_id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(proc)
        return proc.pid, proc

    def _make_trace_file(self, idle_seconds):
        """Create a real trace JSONL whose mtime is `idle_seconds` in the past."""
        trace = self.tmp_dir / "task.trace.jsonl"
        trace.write_text('{"event":"start"}\n')
        old = time.time() - idle_seconds
        os.utime(trace, (old, old))
        return trace

    def _executing_task_with_run(self, *, pid=None, trace_path=None):
        metadata = {}
        if trace_path is not None:
            metadata["trace_file"] = str(trace_path)
        task = Task.objects.create(
            board=self.board,
            title="progress task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            metadata=metadata,
        )
        run = task_runs.start_run(task, f"prog-token-{task.id}", pid=pid)
        return task, run

    # ── P1: fresh trace -> untouched ────────────────────────────────────

    @override_settings(TASK_RUN_LEASE_SECONDS=180, TASK_RUN_PROGRESS_WINDOW_SECONDS=600)
    def test_fresh_trace_run_is_untouched(self):
        trace = self._make_trace_file(idle_seconds=5)  # fresh
        task, run = self._executing_task_with_run(trace_path=trace)

        reconcile_task_runs()

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.RUNNING)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)

    # ── P2: live pid + fresh heartbeat + stale trace -> killed + requeued

    @override_settings(TASK_RUN_LEASE_SECONDS=180, TASK_RUN_PROGRESS_WINDOW_SECONDS=600)
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_live_pid_stale_trace_is_killed_and_requeued(self, mock_sweep):
        mock_sweep.return_value = []
        trace = self._make_trace_file(idle_seconds=1800)  # 30 min idle -> zombie
        task = Task.objects.create(
            board=self.board,
            title="zombie task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            metadata={"trace_file": str(trace)},
        )
        live_pid, proc = self._spawn_odin_exec_lookalike(task.id)
        run = task_runs.start_run(task, "zombie-token", pid=live_pid)
        # Heartbeat is FRESH (no backdate) -> the lease check must NOT fire;
        # only the progress check should catch this run.
        run_no = TaskRun.objects.filter(pk=run.pk)
        self.assertTrue(_pid_alive(live_pid))

        reconcile_task_runs()

        # The live orphan's process group was torn down.
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.fail("progress reaper must kill the live zombie's process group")

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.KILLED)  # pid was alive -> killed
        mock_sweep.assert_called_once()

        task.refresh_from_db()
        # stale_execution is an auto-requeue class -> back to IN_PROGRESS
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        history = task.metadata.get("auto_redispatch_history") or []
        self.assertTrue(any(h.get("class") == "stale_execution" for h in history))

        combined = "\n".join(
            c.content for c in TaskComment.objects.filter(task=task).order_by("id")
        )
        self.assertIn("progress", combined.lower())
        self.assertIn("stale_execution", combined)

    # ── P3: no trace file -> falls back to pid/heartbeat (untouched) ────

    @override_settings(TASK_RUN_LEASE_SECONDS=180, TASK_RUN_PROGRESS_WINDOW_SECONDS=600)
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_no_trace_file_falls_back_to_pid(self, mock_sweep):
        mock_sweep.return_value = []
        task = Task.objects.create(
            board=self.board,
            title="no-trace task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
        )
        live_pid, proc = self._spawn_odin_exec_lookalike(task.id)
        run = task_runs.start_run(task, "no-trace-token", pid=live_pid)
        # No trace_file metadata and nothing on disk -> progress check must skip.
        self.assertTrue(_pid_alive(live_pid))

        reconcile_task_runs()

        # Live pid + fresh heartbeat + no trace -> governed by pid/heartbeat,
        # which says alive -> untouched.
        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.RUNNING)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertTrue(_pid_alive(live_pid), "no-trace live run must not be killed")
        mock_sweep.assert_not_called()

    # ── P4: stale trace but task already advanced -> run closed, no requeue

    @override_settings(TASK_RUN_LEASE_SECONDS=180, TASK_RUN_PROGRESS_WINDOW_SECONDS=600)
    def test_stale_trace_task_already_advanced_just_closes_run(self):
        trace = self._make_trace_file(idle_seconds=1800)
        # Task already moved on to REVIEW (reflection finished it) — the
        # progress reaper must close the orphaned run row, not requeue.
        task = Task.objects.create(
            board=self.board,
            title="advanced task",
            status=TaskStatus.REVIEW,
            created_by="op@test.com",
            assignee=self.user,
            metadata={"trace_file": str(trace)},
        )
        run = task_runs.start_run(task, "advanced-token", pid=2**22 + 9)

        reconcile_task_runs()

        run.refresh_from_db()
        self.assertIn(run.state, (TaskRunState.EXPIRED, TaskRunState.KILLED))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVIEW)  # not failed/requeued
        self.assertFalse(
            TaskComment.objects.filter(task=task).exists(),
            "an already-advanced task must not get a requeue comment",
        )
