"""Orphan-adoption tests for _recover_stale_executions (duplicate-fire bug).

Origin: after a backend/celery restart, a surviving ``odin exec <task_id>``
child kept running in its own session while the new executor re-fired the
same task — two live ``odin exec`` processes then shared one worktree and an
operator had to kill the orphan by hand. The recovery path
(``_recover_stale_executions``) adopted a recorded PID based solely on
``os.kill(pid, 0)`` liveness, with no check that the PID still belonged to an
``odin exec`` for THIS task. PID reuse (or a timeout firing on a legitimately
running orphan) led to a FAIL -> re-dispatch while the orphan was still alive.

Fix: in the recovery path, if ``metadata.active_execution.pid`` is alive AND
its cmdline matches ``odin exec <task_id>``, ADOPT it (keep tracking, do not
re-fire) — even past the wall-clock timeout, since the in-worker deadline no
longer applies once the worker is gone. Only re-dispatch (fail -> redispatch)
when the pid is dead OR its cmdline does not match.

Coverage (real processes, /proc-backed introspection):
  T1 (adopt):   live process with cmdline ``<odin> exec <task_id>`` past the
                timeout -> stays EXECUTING (adopted, no re-fire)
  T2 (dead):    dead pid -> clean re-dispatch (task FAILED, active_execution cleared)
  T3 (mismatch):live process whose cmdline is NOT ``odin exec`` (pid recycled)
                -> FAILED (re-dispatch), not silently adopted

Non-visual - no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from django.test import override_settings

from tests.base import APITestCase
from tasks.dag_executor import _pid_is_alive, _recover_stale_executions
from tasks.models import Board, Task, TaskStatus


class OrphanAdoptRecoveryTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = Board.objects.create(name="orphan-adopt board", working_dir="/tmp/orphan")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="orphan_adopt_"))
        self._spawned = []  # Popen objects to clean up

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
        """Spawn a real process whose argv is [<path>/odin, exec, <task_id>].

        Writes a shell script named ``odin`` that sleeps, so ``/proc/<pid>/cmdline``
        reads back as the same tokens a real ``odin exec`` would carry. Returns
        the live PID.
        """
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

    def _spawn_foreign_process(self):
        """Spawn a real process whose cmdline is NOT ``odin exec`` (pid reuse)."""
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(proc)
        return proc.pid

    def _executing_task(self, active_execution):
        return Task.objects.create(
            board=self.board,
            title="orphan task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            metadata={"active_execution": active_execution},
        )

    # ── T1: live, verified orphan is adopted even past the timeout ──────

    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=1800)
    def test_live_verified_pid_is_adopted_no_refire(self):
        """A surviving ``odin exec`` orphan must be adopted, not re-fired.

        Simulates a restart: the worker died but the ``odin exec`` child
        survived (own session). The recorded PID is alive AND its cmdline
        matches ``odin exec <task_id>``. Even though ``started_at`` is far
        past the wall-clock timeout, recovery must KEEP the task EXECUTING
        (adopt) rather than fail+redispatch (which would create a duplicate).
        """
        task = self._executing_task({})  # no pid yet
        live_pid = self._spawn_odin_exec_lookalike(task.id)
        self.assertTrue(_pid_is_alive(live_pid))

        task.metadata["active_execution"] = {
            "pid": live_pid,
            "started_at": time.time() - 3600,  # well past the 1800s timeout
            "run_token": "run_orphan",
        }
        task.save(update_fields=["metadata"])

        with override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=1800):
            _recover_stale_executions()

        task.refresh_from_db()
        self.assertEqual(
            task.status,
            TaskStatus.EXECUTING,
            "live verified odin exec orphan must be adopted, not re-fired",
        )
        # active_execution is preserved (we are still tracking it)
        active = (task.metadata or {}).get("active_execution") or {}
        self.assertEqual(active.get("pid"), live_pid)

    # ── T2: dead pid -> clean re-dispatch ───────────────────────────────

    def test_dead_pid_is_cleanly_redispatched(self):
        """A dead PID means the orphan is gone -> recover as FAILED (re-dispatch).

        PID 2**22+5 is far above real pid ranges -> reliably dead.
        """
        task = self._executing_task({
            "pid": 2**22 + 5,
            "started_at": time.time(),
            "queued_at": time.time(),
            "run_token": "run_dead",
        })

        _recover_stale_executions()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn("active_execution", task.metadata or {})
        self.assertIn("no longer running", task.metadata.get("last_failure_reason", ""))

    # ── T3: live pid but mismatched cmdline -> re-dispatch (pid reuse) ──

    def test_live_pid_with_mismatched_cmdline_is_redispatched(self):
        """A live PID that is NOT ``odin exec <task_id>`` was recycled -> re-dispatch.

        The recorded PID still exists but now belongs to an unrelated process
        (classic pid reuse after the orphan died). Recovery must NOT adopt a
        foreign process; it fails the task so a fresh dispatch can start.
        """
        foreign_pid = self._spawn_foreign_process()
        self.assertTrue(_pid_is_alive(foreign_pid))

        task = self._executing_task({
            "pid": foreign_pid,
            "started_at": time.time(),  # recent -> isolates the mismatch path
            "queued_at": time.time(),
            "run_token": "run_reuse",
        })

        _recover_stale_executions()

        task.refresh_from_db()
        self.assertEqual(
            task.status,
            TaskStatus.FAILED,
            "a live but mismatched (recycled) pid must not be adopted",
        )
        self.assertNotIn("active_execution", task.metadata or {})
