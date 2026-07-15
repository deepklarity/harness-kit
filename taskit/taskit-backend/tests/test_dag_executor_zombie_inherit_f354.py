"""F354 regression tests: zombie scanner must not reap fresh retries that
inherit a stale per-task trace file.

Origin: F354 (2026-07-11). Four tasks (301, 306, 342, 345) died repeatedly
to ``worker_died`` because ``_reap_stalled_progress_runs`` is keyed to the
per-task trace file mtime (``{working_dir}/.odin/logs/task_<id>.trace.jsonl``
or its ``.out`` fallback). On retry the agent inside the new run hasn't
written yet, so the file still carries the previous attempt's mtime — a
retry after any long gap is judged by a 16-hour-old mtime and reaped at
birth. Four requeues and one wrong theory (RAM pressure) later the operator
finally found it.

Two-part fix (this file pins both halves):

  T1 — Resolver-output guard: a RUNNING run whose ``run.started_at`` is
       newer than the trace file mtime must NOT be reaped by the progress
       scanner. The trace belongs to a previous attempt; the run falls
       back to the lease/heartbeat check, which is the only check that
       can correctly judge it (live supervisor, dead/in-progress agent).

  T2 — Dispatch-time rotation: when ``poll_and_executor`` dispatches a
       task, any leftover ``task_<id>.trace.jsonl`` (and its ``.out``
       fallback) MUST be rotated aside (timestamped backup) BEFORE the
       new run starts — so per-run evidence stays clean and this class
       of bug dies for good.

The test for T2 exercises the rotation helper directly so the contract
sits in one place instead of being entangled with the dispatch loop's
other side effects (worktree, worktree opt-in, etc.).
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
from tasks.dag_executor import (
    _reap_stalled_progress_runs,
    reconcile_task_runs,
)
from tasks.models import Board, Task, TaskComment, TaskRun, TaskRunState, TaskStatus
from tasks.session_resolver import (
    _rotate_leftover_trace_files_for_task,
)


class StaleTraceInheritedByRetryTests(APITestCase):
    """T1: a RUNNING run whose trace file predates the run's own
    ``started_at`` must not be reaped by the progress scanner."""

    def setUp(self):
        super().setUp()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="f354_inherit_"))
        self.board = Board.objects.create(
            name="f354 board", working_dir=str(self.tmp_dir),
        )
        self.user = self.make_user(name="Agent", email="agent@test.com")

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _make_trace_file(self, idle_seconds):
        trace = self.tmp_dir / "task.trace.jsonl"
        trace.write_text('{"event":"start"}\n')
        old = time.time() - idle_seconds
        os.utime(trace, (old, old))
        return trace

    def _executing_task_with_run(self, *, trace_path=None, started_at_override=None):
        metadata = {}
        if trace_path is not None:
            metadata["trace_file"] = str(trace_path)
        task = Task.objects.create(
            board=self.board,
            title="f354 inherit task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            assignee=self.user,
            metadata=metadata,
        )
        run = task_runs.start_run(task, f"f354-token-{task.id}")
        if started_at_override is not None:
            # Override the auto_now_add stamp so the run is "fresh" but the
            # trace on disk is "ancient" — exactly the F354 poisoned-retry
            # case the operator traced through 4 dead tasks to find.
            TaskRun.objects.filter(pk=run.pk).update(
                started_at=started_at_override,
                last_heartbeat=timezone.now(),
            )
            run.refresh_from_db()
        return task, run

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
    )
    def test_trace_older_than_run_started_at_is_NOT_reaped_by_progress_scanner(self):
        """The trace file is 16 hours old; the run was started 30 seconds ago.
        The progress scanner must see the mtime predates the run and skip,
        letting the lease/heartbeat check govern."""
        trace = self._make_trace_file(idle_seconds=16 * 3600)  # 16h stale
        task, run = self._executing_task_with_run(
            trace_path=trace,
            started_at_override=timezone.now() - timedelta(seconds=30),
        )

        _reap_stalled_progress_runs()

        run.refresh_from_db()
        self.assertEqual(
            run.state, TaskRunState.RUNNING,
            "progress scanner must skip a run whose trace predates its started_at",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertFalse(
            TaskComment.objects.filter(task=task).exists(),
            "no requeue comment should fire for a freshly-started run",
        )

    @override_settings(
        TASK_RUN_LEASE_SECONDS=180,
        TASK_RUN_PROGRESS_WINDOW_SECONDS=600,
    )
    @patch("tasks.dag_executor._sweep_orphaned_sandboxes")
    def test_trace_younger_than_run_started_at_IS_still_reaped(self, mock_sweep):
        """Sanity guard: the mtime-vs-started_at guard must not silence the
        real zombie case. A trace that's been idle 30 minutes AFTER the run
        started IS a zombie — progress scanner must reap it."""
        mock_sweep.return_value = []
        trace = self._make_trace_file(idle_seconds=1800)  # 30 min idle
        task, run = self._executing_task_with_run(
            trace_path=trace,
            # run started 35 minutes ago — well before the trace went idle.
            started_at_override=timezone.now() - timedelta(seconds=35 * 60),
        )

        _reap_stalled_progress_runs()

        run.refresh_from_db()
        self.assertIn(
            run.state, (TaskRunState.EXPIRED, TaskRunState.KILLED),
            "zombie with a trace mtime AFTER its run start must still be reaped",
        )


class DispatchRotatesLeftoverTraceFilesTests(APITestCase):
    """T2: dispatch must rotate leftover per-task trace files aside BEFORE
    the new run starts, so a retry can never inherit a previous attempt's
    file and the progress scanner can't be poisoned by stale mtime."""

    def setUp(self):
        super().setUp()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="f354_rotate_"))
        self.log_dir = self.tmp_dir / ".odin" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.board = Board.objects.create(
            name="f354 rotate board", working_dir=str(self.tmp_dir),
        )
        self.user = self.make_user(name="Agent", email="agent@test.com")

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _make_task(self):
        return Task.objects.create(
            board=self.board,
            title="f354 rotate task",
            status=TaskStatus.IN_PROGRESS,
            created_by="op@test.com",
            assignee=self.user,
        )

    def test_rotation_moves_leftover_trace_jsonl_aside(self):
        task = self._make_task()
        leftover = self.log_dir / f"task_{task.id}.trace.jsonl"
        leftover.write_text('{"event":"leftover from previous attempt"}\n')
        pre_mtime = leftover.stat().st_mtime

        rotated = _rotate_leftover_trace_files_for_task(task)

        self.assertFalse(
            leftover.exists(),
            "leftover task_<id>.trace.jsonl must be moved aside before the new run",
        )
        self.assertEqual(len(rotated), 1)
        backup = rotated[0]
        self.assertTrue(backup.exists())
        self.assertIn(f"task_{task.id}.trace.jsonl", backup.name)
        # Backup lives alongside the original — same log dir.
        self.assertEqual(backup.parent, self.log_dir)
        # Preserves content for forensics.
        self.assertEqual(
            backup.read_text(),
            '{"event":"leftover from previous attempt"}\n',
        )
        # Preserves mtime — that's the whole point (don't lie about when the
        # previous attempt ended).
        self.assertAlmostEqual(backup.stat().st_mtime, pre_mtime, places=2)

    def test_rotation_also_moves_leftover_out_file(self):
        """The session resolver falls back to ``.out`` when ``.trace.jsonl``
        is missing. The rotation must cover both so the .out fallback can't
        poison the progress scanner either."""
        task = self._make_task()
        leftover_jsonl = self.log_dir / f"task_{task.id}.trace.jsonl"
        leftover_out = self.log_dir / f"task_{task.id}.out"
        leftover_jsonl.write_text('{"event":"jsonl leftover"}\n')
        leftover_out.write_text("out leftover\n")

        rotated = _rotate_leftover_trace_files_for_task(task)

        self.assertFalse(leftover_jsonl.exists())
        self.assertFalse(leftover_out.exists())
        rotated_names = sorted(p.name for p in rotated)
        self.assertEqual(len(rotated), 2)
        # The .trace.jsonl backup keeps its .jsonl suffix.
        self.assertTrue(any(".trace.jsonl." in n for n in rotated_names))
        # The .out backup keeps its .out suffix.
        self.assertTrue(any(".out." in n for n in rotated_names))

    def test_rotation_is_noop_when_no_leftover_files_exist(self):
        """First-ever dispatch (or one already rotated): nothing to do, no
        crash, empty list returned."""
        task = self._make_task()
        rotated = _rotate_leftover_trace_files_for_task(task)
        self.assertEqual(rotated, [])

    def test_rotation_does_not_touch_other_tasks_files(self):
        """Only THIS task's files get rotated — a sibling task's trace must
        stay put."""
        other_task = self._make_task()
        other_trace = self.log_dir / f"task_{other_task.id}.trace.jsonl"
        other_trace.write_text('{"event":"other task, leave alone"}\n')

        task = self._make_task()
        own_trace = self.log_dir / f"task_{task.id}.trace.jsonl"
        own_trace.write_text('{"event":"own leftover"}\n')

        rotated = _rotate_leftover_trace_files_for_task(task)

        self.assertFalse(own_trace.exists())
        self.assertTrue(
            other_trace.exists(),
            "rotation must not move other tasks' files",
        )
        self.assertEqual(other_trace.read_text(), '{"event":"other task, leave alone"}\n')
        self.assertEqual(len(rotated), 1)