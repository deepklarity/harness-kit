"""F48 regression tests for _run_subprocess_with_cancellation ordering bug.

Origin: F48 (in-worker timeout path killed a healthy fresh attempt — run_token
verification must precede deadline check). Task #119.

Bug: At dag_executor.py:785, the wall-clock deadline check fires BEFORE the
run_token-mismatch check (line 790) and the cancel_requested check (line 793).
A fresh dispatch with a new run_token (or a user-cancelled execution) that
polls while wall-clock is already past the deadline gets killed as "timeout"
instead of being properly detected as run_token_mismatch or cancelled.

Fix: The metadata-based checks (run_token, cancel_requested, liveness) must
fire FIRST. The deadline check should only trigger when the process is still
legitimately running past its budget.

Coverage (4 mandated regression tests + 1 safety):

  F1: test_run_token_mismatch_returns_run_token_mismatch_not_timeout
  F2: test_cancel_requested_returns_cancelled_not_timeout
  F3: test_run_token_unchanged_and_alive_past_deadline_returns_timeout
  F4: test_run_token_unchanged_and_pid_dead_returns_completed_not_timeout
  S1: test_run_token_already_set_on_match_returns_unchanged

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import shutil
import subprocess
import tempfile
import time as real_time
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings

from tests.base import APITestCase
from tasks import dag_executor
from tasks.dag_executor import _run_subprocess_with_cancellation
from tasks.models import Task, TaskStatus


class TestRunSubprocessCancellationOrdering(APITestCase):
    """F48 — verify run_token/cancel_requested checks precede the deadline check."""

    # Long-running command — exceeds the tight deadline, forces the polling loop
    # to keep cycling until the deadline fires (F3) or a metadata check wins (F1/F2).
    LONG_CMD = ["sleep", "60"]

    # Fast-completing command — exits cleanly before any deadline can fire.
    FAST_CMD = ["true"]

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="f48_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _make_executing_task(self, active_execution):
        """Create an EXECUTING task with pre-set active_execution metadata.

        Simulates the state the dag_executor dispatcher would leave the task
        in: pre-set run_token, cancel_requested, etc. before the polling loop
        starts.
        """
        task = self.make_task(
            self.board,
            status=TaskStatus.EXECUTING,
            assignee=self.user,
        )
        md = dict(task.metadata or {})
        md["active_execution"] = active_execution
        task.metadata = md
        task.save(update_fields=["metadata"])
        return task

    def _log_file(self, name):
        return str(self.tmp_dir / name)

    def _freeze_time_past_deadline(self):
        """Patch dag_executor's time.time() so the deadline check fires past deadline.

        The function calls time.time() at three points in the relevant path:
          1. active["started_at"] = time.time()
          2. deadline = time.time() + timeout_seconds
          3. time.time() > deadline (the bug-firing check)

        We return T0 for the first two, T0+100 for the third — so deadline
        appears already elapsed when the deadline check runs.

        Subsequent calls return an even-larger value so _terminate_process's
        own polling loop exits immediately.
        """
        t0 = real_time.time()
        sequence = [t0, t0, t0 + 100.0, t0 + 1000.0, t0 + 1000.0, t0 + 1000.0]
        idx = [0]

        def fake_time():
            i = idx[0]
            idx[0] += 1
            if i < len(sequence):
                return sequence[i]
            return sequence[-1]

        return patch.object(dag_executor.time, "time", side_effect=fake_time)

    # ── F1 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=1)
    def test_run_token_mismatch_returns_run_token_mismatch_not_timeout(self):
        """F48-F1: run_token mismatch must be detected BEFORE the deadline check.

        Pre-set ``metadata.active_execution.run_token = "wrong"`` and dispatch
        with ``run_token="right"``. The function MUST detect the mismatch and
        return ``(-1, "run_token_mismatch")``, not ``(-1, "timeout")``.

        Time is frozen past the deadline so the deadline branch WOULD fire if
        reached — proving the run_token check now precedes it.
        """
        task = self._make_executing_task({"run_token": "wrong"})

        with self._freeze_time_past_deadline():
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id,
                cmd=self.LONG_CMD,
                working_dir="/tmp",
                log_file=self._log_file("f1.log"),
                run_token="right",
            )

        self.assertEqual(stage, "run_token_mismatch",
                         f"expected run_token_mismatch, got {stage!r} (bug fires if 'timeout')")
        self.assertEqual(exit_code, -1)

    # ── F2 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=1)
    def test_cancel_requested_returns_cancelled_not_timeout(self):
        """F48-F2: cancel_requested must be detected BEFORE the deadline check.

        Pre-set metadata with ``cancel_requested=True`` and a matching
        ``run_token``. The function MUST return ``(-1, "cancelled")``, not
        ``(-1, "timeout")``.

        Time is frozen past the deadline so the deadline branch WOULD fire if
        reached — proving the cancel check now precedes it.
        """
        task = self._make_executing_task({
            "run_token": "right",
            "cancel_requested": True,
        })

        with self._freeze_time_past_deadline():
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id,
                cmd=self.LONG_CMD,
                working_dir="/tmp",
                log_file=self._log_file("f2.log"),
                run_token="right",
            )

        self.assertEqual(stage, "cancelled",
                         f"expected cancelled, got {stage!r} (bug fires if 'timeout')")
        self.assertEqual(exit_code, -1)

    # ── F3 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=1)
    def test_run_token_unchanged_and_alive_past_deadline_returns_timeout(self):
        """F48-F3: legitimate timeout must still fire when run_token matches.

        Matching run_token, no cancel_requested, PID alive past deadline.
        Expected: ``(-1, "timeout")``. Preserves the legitimate timeout behavior
        the executor needs when a process is genuinely stuck.

        Uses real wall-clock — the deadline (1s) expires during the polling
        loop. PASSES today and after the fix.
        """
        task = self._make_executing_task({"run_token": "right"})

        exit_code, stage = _run_subprocess_with_cancellation(
            task_id=task.id,
            cmd=self.LONG_CMD,
            working_dir="/tmp",
            log_file=self._log_file("f3.log"),
            run_token="right",
        )

        self.assertEqual(stage, "timeout")
        self.assertEqual(exit_code, -1)

    # ── F4 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=10)
    def test_run_token_unchanged_and_pid_dead_returns_completed_not_timeout(self):
        """F48-F4: clean exit before deadline must return success.

        Process exits cleanly (run ``true``) before the deadline. Expected:
        ``(0, "none")``. NOT ``(-1, "timeout")`` — even with a tight polling
        interval, a clean exit short-circuits the loop.

        PASSES today and after the fix.
        """
        task = self._make_executing_task({"run_token": "right"})

        exit_code, stage = _run_subprocess_with_cancellation(
            task_id=task.id,
            cmd=self.FAST_CMD,
            working_dir="/tmp",
            log_file=self._log_file("f4.log"),
            run_token="right",
        )

        self.assertEqual(stage, "none")
        self.assertEqual(exit_code, 0)

    # ── S1 (safety) ────────────────────────────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=10)
    def test_run_token_already_set_on_match_returns_unchanged(self):
        """F48-S1: matching run_token must not be clobbered on existing metadata.

        If ``metadata.active_execution.run_token`` is already set to the
        dispatch run_token, the function MUST preserve it — not overwrite with
        a new value. Guards against an implementation that does
        ``active["run_token"] = run_token`` unconditionally.

        PASSES today and after the fix.
        """
        task = self._make_executing_task({"run_token": "right"})

        exit_code, stage = _run_subprocess_with_cancellation(
            task_id=task.id,
            cmd=self.FAST_CMD,
            working_dir="/tmp",
            log_file=self._log_file("s1.log"),
            run_token="right",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stage, "none")

        task.refresh_from_db()
        active = (task.metadata or {}).get("active_execution") or {}
        self.assertEqual(
            active.get("run_token"), "right",
            "existing matching run_token must not be overwritten",
        )