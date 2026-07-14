"""Tests for tasks.session_resolver — the helper that decides which JSONL
trace file (task execution vs reflection) represents the 'current session'
for a given task."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from tests.base import APITestCase

from tasks.models import ReflectionReport, ReflectionStatus, Task, TaskStatus
from tasks.session_resolver import (
    SESSION_TYPE_REFLECTION,
    SESSION_TYPE_TASK,
    resolve_session,
)


class SessionResolverTests(APITestCase):
    def setUp(self):
        super().setUp()
        # Per-test tmp dir used as the board's working_dir. Odin normally
        # writes traces to {working_dir}/.odin/logs, so we mimic that.
        self.tmp = tempfile.TemporaryDirectory()
        self.working_dir = Path(self.tmp.name)
        self.log_dir = self.working_dir / ".odin" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.board = self.make_board(working_dir=str(self.working_dir))
        self.task = self.make_task(self.board, status=TaskStatus.TODO)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    # ---- helpers ------------------------------------------------------

    def _write_task_trace(self, content: str = '{"hello": "world"}\n') -> Path:
        p = self.log_dir / f"task_{self.task.id}.trace.jsonl"
        p.write_text(content)
        return p

    def _write_reflection_trace(self, report_id: int, content: str = '{"refl": 1}\n') -> Path:
        p = self.log_dir / f"reflect_{report_id}.trace.jsonl"
        p.write_text(content)
        return p

    # ---- tests --------------------------------------------------------

    def test_returns_none_when_no_working_dir(self):
        board = self.make_board(name="No WD")
        task = self.make_task(board, title="No WD task")
        self.assertIsNone(resolve_session(task))

    def test_returns_none_when_no_files_and_not_running(self):
        # Status idle, no files, no reflections → None.
        self.assertIsNone(resolve_session(self.task))

    def test_live_task_execution_when_task_executing(self):
        self._write_task_trace()
        self.task.status = TaskStatus.EXECUTING
        self.task.save()

        info = resolve_session(self.task)
        self.assertIsNotNone(info)
        self.assertEqual(info.session_type, SESSION_TYPE_TASK)
        self.assertTrue(info.live)
        self.assertTrue(info.exists)
        self.assertEqual(info.task_id, self.task.id)
        self.assertIsNone(info.report_id)

    def test_live_reflection_takes_priority_over_task_status(self):
        # Even if the task status is EXECUTING, a RUNNING reflection wins.
        self._write_task_trace()
        self.task.status = TaskStatus.EXECUTING
        self.task.save()
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet",
            requested_by="tester@example.com",
            status=ReflectionStatus.RUNNING,
        )
        self._write_reflection_trace(report.id)

        info = resolve_session(self.task)
        self.assertEqual(info.session_type, SESSION_TYPE_REFLECTION)
        self.assertTrue(info.live)
        self.assertEqual(info.report_id, report.id)

    def test_idle_falls_back_to_latest_on_disk_trace(self):
        # Task not running, no running reflection — pick most recent file.
        task_path = self._write_task_trace()
        # Give the task file a slightly older mtime and write a newer reflection.
        older = time.time() - 60
        import os
        os.utime(task_path, (older, older))

        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet",
            requested_by="tester@example.com",
            status=ReflectionStatus.COMPLETED,
        )
        self._write_reflection_trace(report.id)

        info = resolve_session(self.task)
        self.assertIsNotNone(info)
        self.assertEqual(info.session_type, SESSION_TYPE_REFLECTION)
        self.assertFalse(info.live)
        self.assertEqual(info.report_id, report.id)

    def test_idle_picks_task_trace_when_newer(self):
        report = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet",
            requested_by="tester@example.com",
            status=ReflectionStatus.COMPLETED,
        )
        refl_path = self._write_reflection_trace(report.id)
        import os
        older = time.time() - 60
        os.utime(refl_path, (older, older))

        self._write_task_trace()  # current mtime

        info = resolve_session(self.task)
        self.assertEqual(info.session_type, SESSION_TYPE_TASK)
        self.assertFalse(info.live)


class SandboxedRunResolutionTests(APITestCase):
    """Celery runs odin with cwd = the task worktree, so live traces stream to
    {worktree}/.odin/logs — not the board-root guess. Resolution order must be:
    task.metadata['trace_file'] (odin records it at exec start) → board-root
    log dir → derived worktree path."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.working_dir = Path(self.tmp.name)
        self.root_logs = self.working_dir / ".odin" / "logs"
        self.root_logs.mkdir(parents=True, exist_ok=True)
        self.board = self.make_board(working_dir=str(self.working_dir))
        self.spec = self.make_spec(self.board, odin_id="sp_test")
        self.task = self.make_task(
            self.board, spec=self.spec, status=TaskStatus.EXECUTING
        )

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_metadata_trace_file_is_preferred(self):
        alt = self.working_dir / "anywhere.trace.jsonl"
        alt.write_text('{"x": 1}\n')
        self.task.metadata = {**(self.task.metadata or {}), "trace_file": str(alt)}
        self.task.save(update_fields=["metadata"])
        info = resolve_session(self.task)
        self.assertEqual(info.jsonl_path, str(alt))
        self.assertTrue(info.exists)
        self.assertEqual(info.session_type, SESSION_TYPE_TASK)

    def test_missing_metadata_path_falls_back_to_root(self):
        self.task.metadata = {
            **(self.task.metadata or {}),
            "trace_file": str(self.working_dir / "gone.trace.jsonl"),
        }
        self.task.save(update_fields=["metadata"])
        root = self.root_logs / f"task_{self.task.id}.trace.jsonl"
        root.write_text('{"y": 2}\n')
        info = resolve_session(self.task)
        self.assertEqual(info.jsonl_path, str(root))

    def test_worktree_trace_fallback_for_celery_runs(self):
        wt_logs = (
            self.working_dir / ".odin" / "worktrees" / "sp_test"
            / str(self.task.id) / ".odin" / "logs"
        )
        wt_logs.mkdir(parents=True)
        wt_trace = wt_logs / f"task_{self.task.id}.trace.jsonl"
        wt_trace.write_text('{"z": 3}\n')
        info = resolve_session(self.task)
        self.assertEqual(info.jsonl_path, str(wt_trace))
        self.assertTrue(info.exists)
