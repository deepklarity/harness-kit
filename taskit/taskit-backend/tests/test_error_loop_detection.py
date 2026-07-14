"""Tests for error-loop detection in the run reconciler (task #262).

Trust bucket: a run whose trace tail is dominated by repeated errors of
the same signature is looping — the CLI retries a failing provider call
forever, the trace keeps growing (so W6.15 progress-liveness, which
measures WRITES not WORK, passes), but no work happens. Task 252 sat
EXECUTING for an hour in a minimax stream-error loop this way; the human
caught it only via their quota dashboard. These tests pin the detector.

Coverage:
  Unit (detect_error_loop + helpers):
    U1: quota loop trace -> detected (dominant signature, ratio >= threshold)
    U2: healthy chatty trace -> not detected (zero error lines)
    U3: too few lines -> None (insufficient data)
    U4: mixed signatures below threshold -> not looping
    U5: JSON error event without keyword -> transport_error
    U6: provider backoff for quota/transport, 0 otherwise
  Integration (reconcile_task_runs):
    L1: loop trace -> run killed, task FAILED -> requeued with backoff + comment
    L2: healthy trace -> untouched
    L3: provider backoff delay honored (next_retry_after ~ 10 min)
    L4: parked after max attempts (cap reached -> FAILED, no requeue)
    L5: ErrorEvent recorded per detection
    L6: reassign OFF by default (same agent requeued)
    L7: reassign ON + provider loop -> fallback agent
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import shutil
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
from tasks.error_loop import (
    DEFAULT_PROVIDER_BACKOFF,
    PROVIDER_LOOP_CLASSES,
    LoopVerdict,
    classify_trace_line,
    detect_error_loop,
    is_provider_loop,
    provider_backoff_seconds,
)
from tasks.models import (
    Board,
    ErrorEvent,
    Task,
    TaskComment,
    TaskRun,
    TaskRunState,
    TaskStatus,
    User,
    UserRole,
)

# ── Fixtures ──────────────────────────────────────────────────────────

# A minimax-style stream-error / quota loop: the CLI retries, each retry
# emits a provider error. 45/50 lines are the same quota signature.
_LOOP_LINE = (
    '{"type":"error","error":{"type":"rate_limit_error",'
    '"message":"rate limit exceeded: quota exhausted (minimax)"}}'
)
_HEALTHY_LINE = (
    '{"type":"content_block_delta","delta":{"text":"Reading the file now."}}'
)
_TRANSPORT_LINE = (
    '{"type":"error","error":{"type":"connection_reset",'
    '"message":"connection reset by peer (stream interrupted)"}}'
)


def _make_trace(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    # Fresh mtime so the progress check does not reap it.
    now = time.time()
    os.utime(path, (now, now))
    return path


def _loop_trace(path, n_errors=45, n_healthy=5):
    return _make_trace(path, [_LOOP_LINE] * n_errors + [_HEALTHY_LINE] * n_healthy)


def _healthy_trace(path, n=50):
    return _make_trace(path, [_HEALTHY_LINE] * n)


# ── Unit tests ────────────────────────────────────────────────────────


class ErrorLoopDetectionUnitTests(APITestCase):
    """Pure-logic tests on the detection module — no DB, no reconciler."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="eloop_unit_"))

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    # U1: quota loop detected
    def test_quota_loop_trace_detected(self):
        trace = _loop_trace(self.tmp / "task_1.trace.jsonl")
        v = detect_error_loop(trace, agent="minimax", model="minimax-m3")
        self.assertIsNotNone(v)
        self.assertTrue(v.is_looping)
        self.assertGreaterEqual(v.error_ratio, 0.8)
        self.assertEqual(v.dominant_class, "quota_exhaustion")
        self.assertIn("minimax", v.dominant_signature)
        self.assertIn("execution", v.dominant_signature)
        self.assertGreater(v.error_lines, 0)

    # U2: healthy trace not detected
    def test_healthy_chatty_trace_not_detected(self):
        trace = _healthy_trace(self.tmp / "task_2.trace.jsonl")
        v = detect_error_loop(trace, agent="claude", model="claude-sonnet")
        self.assertIsNotNone(v)
        self.assertFalse(v.is_looping)
        self.assertEqual(v.error_lines, 0)

    # U3: too few lines -> None
    def test_too_few_lines_returns_none(self):
        trace = _make_trace(self.tmp / "task_3.trace.jsonl", [_LOOP_LINE] * 5)
        v = detect_error_loop(trace, min_lines=10)
        self.assertIsNone(v)

    # U4: mixed signatures below threshold -> not looping
    def test_mixed_signatures_below_threshold_not_looping(self):
        # 25 quota + 25 transport = no single signature >= 80%
        trace = _make_trace(
            self.tmp / "task_4.trace.jsonl",
            [_LOOP_LINE] * 25 + [_TRANSPORT_LINE] * 25,
        )
        v = detect_error_loop(trace, agent="minimax", model="minimax-m3")
        self.assertIsNotNone(v)
        self.assertFalse(v.is_looping)

    # U5: JSON error event without keyword -> transport_error
    def test_json_error_event_without_keyword_classified(self):
        cls = classify_trace_line(
            '{"type":"error","error":{"type":"stream_error","message":"upstream fault"}}'
        )
        self.assertEqual(cls, "transport_error")

    def test_normal_content_line_not_error(self):
        self.assertIsNone(classify_trace_line(_HEALTHY_LINE))
        self.assertIsNone(classify_trace_line(""))

    def test_transport_loop_detected(self):
        trace = _make_trace(
            self.tmp / "task_5.trace.jsonl",
            [_TRANSPORT_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )
        v = detect_error_loop(trace, agent="glm", model="glm-5.2")
        self.assertTrue(v.is_looping)
        self.assertEqual(v.dominant_class, "transport_error")
        self.assertIn("glm", v.dominant_signature)

    # U6: provider backoff
    def test_provider_backoff_for_quota_class(self):
        v = LoopVerdict(
            is_looping=True, error_ratio=0.9,
            dominant_class="quota_exhaustion",
            dominant_signature="quota_loop / minimax / execution",
            error_lines=45, total_lines=50, sample="x",
        )
        self.assertEqual(provider_backoff_seconds(v), DEFAULT_PROVIDER_BACKOFF)
        self.assertTrue(is_provider_loop(v))

    def test_no_backoff_for_non_provider_class(self):
        v = LoopVerdict(
            is_looping=True, error_ratio=0.9,
            dominant_class="lock_race",
            dominant_signature="database_locked / glm / execution",
            error_lines=45, total_lines=50, sample="x",
        )
        self.assertEqual(provider_backoff_seconds(v), 0)
        self.assertFalse(is_provider_loop(v))

    def test_provider_backoff_custom_delay(self):
        v = LoopVerdict(
            is_looping=True, error_ratio=0.9,
            dominant_class="transport_error",
            dominant_signature="stream_error_loop / glm / execution",
            error_lines=45, total_lines=50, sample="x",
        )
        self.assertEqual(
            provider_backoff_seconds(v, provider_delay=300, default=10), 300
        )


# ── Integration tests ─────────────────────────────────────────────────


class ErrorLoopReconcilerTests(APITestCase):
    """Integration tests through ``reconcile_task_runs``."""

    def setUp(self):
        super().setUp()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="eloop_reconciler_"))
        self.board = Board.objects.create(
            name="eloop board", working_dir=str(self.tmp_dir)
        )
        self.user = self.make_user(name="Minimax", email="minimax@test.com")
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
        import subprocess
        proc = subprocess.Popen(
            [str(odin_bin), "exec", str(task_id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(proc)
        return proc.pid, proc

    def _executing_task_with_trace(self, *, trace_lines, pid=None):
        trace = self.tmp_dir / f".odin/logs/task.trace.jsonl"
        _make_trace(trace, trace_lines)
        task = Task.objects.create(
            board=self.board,
            title="loop task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            model_name="minimax-m3",
            metadata={"trace_file": str(trace)},
        )
        run = task_runs.start_run(task, f"loop-token-{task.id}", pid=pid)
        return task, run, trace

    # ── L1: loop trace -> killed + requeued with comment ───────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
        TASK_RUN_ERROR_LOOP_THRESHOLD=0.8,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_loop_trace_killed_and_requeued(self, mock_sweep):
        mock_sweep.return_value = []
        task = Task.objects.create(
            board=self.board,
            title="loop task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            model_name="minimax-m3",
        )
        live_pid, proc = self._spawn_odin_exec_lookalike(task.id)
        trace = self.tmp_dir / f".odin/logs/task.trace.jsonl"
        _loop_trace(trace)
        task.metadata = {"trace_file": str(trace)}
        task.save(update_fields=["metadata"])
        run = task_runs.start_run(task, "loop-token", pid=live_pid)

        reconcile_task_runs()

        # The looping run was killed.
        try:
            proc.wait(timeout=3)
        except Exception:
            self.fail("error-loop reaper must kill the looping process group")
        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.KILLED)

        # The task was FAILED then auto-requeued -> IN_PROGRESS.
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        history = task.metadata.get("auto_redispatch_history") or []
        self.assertTrue(any(h.get("class") == "error_loop" for h in history))

        # The comment trail names the loop + signature.
        combined = "\n".join(
            c.content for c in TaskComment.objects.filter(task=task).order_by("id")
        )
        self.assertIn("Error loop detected", combined)
        self.assertIn("quota", combined.lower())

    # ── L2: healthy trace -> untouched ─────────────────────────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_healthy_trace_untouched(self, mock_sweep):
        mock_sweep.return_value = []
        task = Task.objects.create(
            board=self.board,
            title="healthy task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            model_name="minimax-m3",
        )
        live_pid, proc = self._spawn_odin_exec_lookalike(task.id)
        trace = self.tmp_dir / f".odin/logs/task.trace.jsonl"
        _healthy_trace(trace)
        task.metadata = {"trace_file": str(trace)}
        task.save(update_fields=["metadata"])
        run = task_runs.start_run(task, "healthy-token", pid=live_pid)

        reconcile_task_runs()

        run.refresh_from_db()
        self.assertEqual(run.state, TaskRunState.RUNNING)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        mock_sweep.assert_not_called()

    # ── L3: provider backoff delay honored ────────────────────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
        TASK_RUN_ERROR_LOOP_PROVIDER_BACKOFF=600,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_provider_backoff_delay_honored(self, mock_sweep):
        mock_sweep.return_value = []
        task, run, trace = self._executing_task_with_trace(
            trace_lines=[_LOOP_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )

        reconcile_task_runs()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # next_retry_after stamped ~10 min in the future.
        nra = task.metadata.get("next_retry_after")
        self.assertIsNotNone(nra, "provider loop must stamp next_retry_after")
        from django.utils.dateparse import parse_datetime
        when = parse_datetime(nra)
        self.assertIsNotNone(when)
        delta = (when - timezone.now()).total_seconds()
        # Within a generous window of the configured 600s.
        self.assertGreater(delta, 480)
        self.assertLess(delta, 720)

    # ── L4: parked after max attempts ─────────────────────────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_parked_after_max_attempts(self, mock_sweep):
        mock_sweep.return_value = []
        task, run, trace = self._executing_task_with_trace(
            trace_lines=[_LOOP_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )
        # Pre-seed the redispatch counter at the cap so the policy parks it.
        task.metadata = dict(task.metadata or {})
        task.metadata["auto_redispatch_count"] = 2  # == max_retries
        task.metadata["trace_file"] = str(trace)
        task.save(update_fields=["metadata"])

        reconcile_task_runs()

        task.refresh_from_db()
        # Cap reached -> left FAILED for human, not requeued.
        self.assertEqual(task.status, TaskStatus.FAILED)
        combined = "\n".join(
            c.content for c in TaskComment.objects.filter(task=task).order_by("id")
        )
        self.assertIn("cap reached", combined.lower())

    # ── L5: ErrorEvent recorded per detection ─────────────────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_error_event_recorded(self, mock_sweep):
        mock_sweep.return_value = []
        task, run, trace = self._executing_task_with_trace(
            trace_lines=[_LOOP_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )

        reconcile_task_runs()

        events = ErrorEvent.objects.filter(
            task=task, source=ErrorEvent.SOURCE_ERROR_LOOP
        )
        self.assertEqual(events.count(), 1)
        evt = events.first()
        self.assertIn("quota", evt.symptom.lower())
        self.assertEqual(evt.failure_class, "quota_exhaustion")
        self.assertIn("minimax", evt.context.get("dominant_signature", ""))

    # ── L6: reassign OFF by default ───────────────────────────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
        TASK_RUN_ERROR_LOOP_REASSIGN=False,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_reassign_off_by_default(self, mock_sweep):
        mock_sweep.return_value = []
        task, run, trace = self._executing_task_with_trace(
            trace_lines=[_LOOP_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )
        original_assignee = task.assignee_id

        reconcile_task_runs()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Same agent requeued — no reassign.
        self.assertEqual(task.assignee_id, original_assignee)

    # ── L7: reassign ON + provider loop -> fallback agent ─────────

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
        TASK_RUN_ERROR_LOOP_REASSIGN=True,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_reassign_when_flag_on(self, mock_sweep):
        mock_sweep.return_value = []
        # A fallback agent on the board.
        fallback = self.make_user(name="Claude", email="claude@test.com")
        from tasks.models import BoardMembership
        BoardMembership.objects.create(board=self.board, user=fallback)
        fallback.role = UserRole.AGENT
        fallback.save()

        task, run, trace = self._executing_task_with_trace(
            trace_lines=[_LOOP_LINE] * 45 + [_HEALTHY_LINE] * 5,
        )
        original_assignee = task.assignee_id
        self.assertNotEqual(fallback.id, original_assignee)

        reconcile_task_runs()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Reassigned to the fallback agent.
        self.assertEqual(task.assignee_id, fallback.id)
        combined = "\n".join(
            c.content for c in TaskComment.objects.filter(task=task).order_by("id")
        )
        self.assertIn("reassigned", combined.lower())
