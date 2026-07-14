"""Sleep-resilient timeout tests for _run_subprocess_with_cancellation.

Origin: an overnight host sleep burned a run — the in-worker watchdog used
wall-clock (time.time()) for its deadline, so sleep time counted toward the
timeout budget; the watchdog failed the task hours later, and by then the VM
and worktree registration were gone.

Fix:
  1. The deadline is now a SleepAwareDeadline — a monotonic clock with host
     sleep-gap detection: a wall delta between two polls that is far larger
     than the expected poll interval is treated as host sleep and absorbed
     (the deadline is pushed out by the gap), not charged to the budget.
  2. A caffeinate -i assertion is held while any task is EXECUTING (macOS),
     released on exit — prevents the host from sleeping mid-run.
  3. Before any timeout kill, process liveness is re-verified (dovetails with
     the merged adopt-live-pids fix): a process that just exited is reaped
     with its real exit code, not mislabelled "timeout".

Coverage:
  SleepAwareDeadline (unit, injected clock):
    D1  normal polling expires at the timeout boundary, no false gap
    D2  a host-sleep gap extends the deadline (NOT expired after the gap)
    D3  total_sleep_excluded accumulates the absorbed gap
  Watchdog (integration):
    W1  a run spanning a fake sleep gap completes — NOT timed out  [acceptance]
    W2  a legitimate run past the deadline (no gap) still times out
    W3  deadline fires but the process already exited -> real code, not timeout
  caffeinate assertion:
    C1  caffeinate -i held during execution on darwin and released after
    C2  no caffeinate spawned on linux
    C3  missing caffeinate binary -> execution proceeds (best-effort)

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import override_settings

from tests.base import APITestCase
from tasks import dag_executor
from tasks.dag_executor import (
    SleepAwareDeadline,
    _run_subprocess_with_cancellation,
)
from tasks.models import TaskStatus


class _SeqClock:
    """A clock that returns scripted values in order, repeating the last."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self):
        if not self._values:
            return 0.0
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


class _FakeProc:
    """subprocess.Popen stand-in for the watchdog's wait/poll loop."""

    def __init__(self, *, raises=0, returncode=0, pid=2 ** 22 + 5,
                 exit_after_raises=False):
        self.pid = pid
        self.returncode = returncode
        self._raises_left = raises
        self._exit_after = exit_after_raises
        self._exited = False

    def wait(self, timeout=None):
        if self._raises_left > 0:
            self._raises_left -= 1
            if self._exit_after and self._raises_left == 0:
                # Process exits right as this timed-out wait would return.
                self._exited = True
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self._exited = True
        return self.returncode

    def poll(self):
        return self.returncode if self._exited else None


class _PopenRouter:
    """Routes Popen calls: ``caffeinate`` -> mock caf proc; else -> task proc."""

    def __init__(self, task_proc):
        self.task_proc = task_proc
        self.caf_procs = []
        self.all_calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.all_calls.append(list(cmd))
        if cmd and cmd[0] == "caffeinate":
            caf = MagicMock()
            caf.poll.return_value = None  # alive
            caf.terminate = MagicMock()
            caf.wait = MagicMock(return_value=0)
            caf.kill = MagicMock()
            self.caf_procs.append(caf)
            return caf
        return self.task_proc


class TestSleepAwareDeadlineUnit(APITestCase):
    """D1-D3: pure clock arithmetic, no subprocess."""

    # ── D1 ─────────────────────────────────────────────────────────────
    def test_normal_polling_expires_at_timeout_no_false_gap(self):
        """Without a sleep gap, the deadline expires at the timeout boundary.

        Poll deltas under the gap threshold must never be misread as sleep.
        """
        clock = _SeqClock([
            0.0,                 # init: start=0, deadline=2.0
            0.5, 0.5,            # poll1: sample(0.1?), expired -> not yet
            1.0, 1.0,            # poll2: sample, expired -> not yet
            2.5, 2.5,            # poll3: sample, expired -> 2.5 > 2.0 -> expired
        ])
        d = SleepAwareDeadline(2.0, clock=clock)
        d.sample(0.5)
        self.assertFalse(d.expired())
        d.sample(0.5)
        self.assertFalse(d.expired())
        d.sample(0.5)
        self.assertTrue(d.expired())
        self.assertEqual(d.total_sleep_excluded, 0.0,
                         "no gap -> nothing excluded")

    # ── D2 ─────────────────────────────────────────────────────────────
    def test_host_sleep_gap_extends_deadline(self):
        """A large inter-poll delta (host sleep) must NOT expire the deadline."""
        clock = _SeqClock([
            0.0,                 # init: deadline = 2.0
            0.5, 0.5,            # poll1: normal
            1800.5, 1800.5,      # poll2: 1800s "sleep" absorbed
            1800.6, 1800.6,      # poll3: still not expired
        ])
        d = SleepAwareDeadline(2.0, clock=clock)
        d.sample(0.5)
        self.assertFalse(d.expired())
        d.sample(0.5)  # sleep gap absorbed here
        self.assertFalse(d.expired(),
                         "a host-sleep gap must not expire the deadline")
        d.sample(0.5)
        self.assertFalse(d.expired(), "deadline stays extended after the gap")

    # ── D3 ─────────────────────────────────────────────────────────────
    def test_total_sleep_excluded_accumulates_gap(self):
        """The absorbed gap is recorded for observability."""
        clock = _SeqClock([
            0.0,                 # init
            0.5,                 # poll1 sample (normal)
            1800.5,              # poll2 sample: gap detected
        ])
        d = SleepAwareDeadline(2.0, clock=clock)
        d.sample(0.5)
        d.sample(0.5)
        # gap = 1800.0 (delta) - 0.5 (expected interval) = 1799.5
        self.assertAlmostEqual(d.total_sleep_excluded, 1799.5, places=1)


class TestWatchdogSleepResilience(APITestCase):
    """W1-W3: _run_subprocess_with_cancellation end-to-end with fakes."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="sleep_resilient_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _make_executing_task(self):
        task = self.make_task(self.board, status=TaskStatus.EXECUTING,
                              assignee=self.user)
        task.metadata = {"active_execution": {}}
        task.save(update_fields=["metadata"])
        return task

    def _log_file(self, name):
        return str(self.tmp_dir / name)

    # ── W1 ── acceptance: sleep gap must NOT time out ──────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=False)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=2)
    def test_run_spanning_sleep_gap_completes_not_timed_out(self, _macos):
        """A run that spans a fake host-sleep gap must complete normally.

        Simulates the overnight incident: monotonic clock jumps ~1800s mid-run
        (host slept). The sleep-aware deadline absorbs the gap; the process
        finishes afterwards and returns success — NOT (-1, "timeout").
        """
        task = self._make_executing_task()
        proc = _FakeProc(raises=3, returncode=0)  # exits after 3 polls
        # Clock: init(0.0); poll1 normal; poll2 GAP(+1800s); poll3 normal.
        clock = _SeqClock([0.0, 0.1, 0.1, 1800.2, 1800.2, 1800.3, 1800.3])

        with (patch("subprocess.Popen", return_value=proc),
              patch("tasks.dag_executor._default_deadline_clock", clock)):
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("w1.log"), run_token="r",
            )

        self.assertEqual((exit_code, stage), (0, "none"),
                         "a run spanning a sleep gap must not be timed out")

    # ── W2 ── legitimate timeout still fires ───────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=False)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=2)
    def test_legitimate_run_past_deadline_still_times_out(self, _macos):
        """Without a sleep gap, a genuinely stuck process must still time out.

        Guards against an implementation that absorbs ALL deltas (never fires).
        """
        task = self._make_executing_task()
        proc = _FakeProc(raises=99, returncode=0)  # never exits on its own
        # Clock: init(0.0); poll1 sample(0.1), expired(3.0) -> 3.0 > 2.0 -> fire.
        clock = _SeqClock([0.0, 0.1, 3.0])

        with (patch("subprocess.Popen", return_value=proc),
              patch("tasks.dag_executor._default_deadline_clock", clock)):
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("w2.log"), run_token="r",
            )

        self.assertEqual(stage, "timeout",
                         "a genuine stall past the deadline must time out")
        self.assertEqual(exit_code, -1)

    # ── W3 ── liveness re-check before kill ────────────────────────────

    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=False)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=2)
    def test_deadline_fires_but_proc_exited_returns_real_code(self, _macos):
        """If the process exited at the deadline boundary, reap it cleanly.

        The deadline fires, but a final proc.poll() shows the process already
        exited (race between the timed-out wait and natural exit). Must return
        the real exit code, not mislabel it "timeout".
        """
        task = self._make_executing_task()
        # Exits right as the 3rd timed-out wait raises (deadline fires same poll).
        proc = _FakeProc(raises=3, returncode=0, exit_after_raises=True)
        clock = _SeqClock([0.0, 0.1, 0.1, 0.2, 0.2, 3.0, 3.0])

        with (patch("subprocess.Popen", return_value=proc),
              patch("tasks.dag_executor._default_deadline_clock", clock)):
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("w3.log"), run_token="r",
            )

        self.assertEqual((exit_code, stage), (0, "none"),
                         "a process that exited must not be labelled timeout")


class TestCaffeinateAssertion(APITestCase):
    """C1-C3: caffeinate -i held while EXECUTING (macOS), no-op elsewhere."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="caffeinate_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _make_executing_task(self):
        task = self.make_task(self.board, status=TaskStatus.EXECUTING,
                              assignee=self.user)
        task.metadata = {"active_execution": {}}
        task.save(update_fields=["metadata"])
        return task

    def _log_file(self, name):
        return str(self.tmp_dir / name)

    # ── C1 ─────────────────────────────────────────────────────────────
    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=True)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=10)
    def test_caffeinate_held_during_execution_and_released_after(self, _macos):
        """On macOS, caffeinate -i is spawned before the task and released after."""
        task = self._make_executing_task()
        proc = _FakeProc(raises=0, returncode=0)  # task exits immediately
        router = _PopenRouter(proc)

        with patch("subprocess.Popen", side_effect=router):
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("c1.log"), run_token="r",
            )

        self.assertEqual((exit_code, stage), (0, "none"))
        # caffeinate spawned BEFORE the task command (held during execution)
        self.assertEqual(router.all_calls[0], ["caffeinate", "-i"],
                         "caffeinate -i must be asserted before the task runs")
        self.assertTrue(router.caf_procs, "a caffeinate proc must be tracked")
        # caffeinate released after execution
        router.caf_procs[0].terminate.assert_called_with()
        router.caf_procs[0].wait.assert_called_with(timeout=2)

    # ── C2 ─────────────────────────────────────────────────────────────
    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=False)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=10)
    def test_no_caffeinate_spawned_on_linux(self, _macos):
        """On non-macOS, no caffeinate process is spawned at all."""
        task = self._make_executing_task()
        proc = _FakeProc(raises=0, returncode=0)
        router = _PopenRouter(proc)

        with patch("subprocess.Popen", side_effect=router):
            _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("c2.log"), run_token="r",
            )

        caffeinate_calls = [c for c in router.all_calls
                            if c[:1] == ["caffeinate"]]
        self.assertEqual(caffeinate_calls, [],
                         "caffeinate must not be spawned on non-macOS")

    # ── C3 ─────────────────────────────────────────────────────────────
    @patch("tasks.dag_executor.CANCEL_POLL_INTERVAL_SECONDS", 0.1)
    @patch("tasks.dag_executor._is_macos", return_value=True)
    @override_settings(DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=10)
    def test_missing_caffeinate_binary_proceeds(self, _macos):
        """If caffeinate is unavailable, execution still proceeds (best-effort)."""
        task = self._make_executing_task()
        task_proc = _FakeProc(raises=0, returncode=0)

        def router(cmd, *args, **kwargs):
            if cmd and cmd[0] == "caffeinate":
                raise FileNotFoundError("[Errno 2] No such file: 'caffeinate'")
            return task_proc

        with patch("subprocess.Popen", side_effect=router):
            exit_code, stage = _run_subprocess_with_cancellation(
                task_id=task.id, cmd=["fake"], working_dir="/tmp",
                log_file=self._log_file("c3.log"), run_token="r",
            )

        self.assertEqual((exit_code, stage), (0, "none"),
                         "missing caffeinate must not fail the task")
