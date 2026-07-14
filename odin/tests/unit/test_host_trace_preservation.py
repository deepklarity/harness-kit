"""Tests for host-side trace preservation across attempts (task #330).

Root cause this closes: trace files resolved against the process cwd. When
odin runs inside a task worktree (cwd under ``.odin/worktrees/...``) the
traces land *inside the worktree*, and worktree lifecycle (reset/cleanup
between retries) destroys them — so the next attempt's rotation finds
nothing to preserve. That is how task #314's prior-attempt evidence was
lost while task #306 (host-side) kept its attempt-N files.

Fix: resolve the log dir to the host project root (the directory that
*contains* ``.odin/worktrees``) so traces survive across attempts.
"""

from pathlib import Path

from odin.orchestrator import Orchestrator


class TestResolveHostLogDir:
    """_resolve_host_log_dir escapes the worktree to the host project root."""

    def test_method_exists(self):
        assert callable(getattr(Orchestrator, "_resolve_host_log_dir", None)), (
            "Orchestrator._resolve_host_log_dir must exist so traces resolve "
            "host-side and survive worktree resets between attempts"
        )

    def test_relative_log_dir_from_worktree_resolves_to_host_root(self, tmp_path):
        # Simulate a project layout under tmp_path.
        project = tmp_path / "proj"
        worktree = project / ".odin" / "worktrees" / "spec1" / "t1"
        worktree.mkdir(parents=True)
        host_logs = project / ".odin" / "logs"

        resolved = Orchestrator._resolve_host_log_dir(".odin/logs", cwd=worktree)
        assert resolved == host_logs.resolve(), (
            "a relative log_dir resolved from inside a worktree must land at "
            "the HOST project .odin/logs, not the worktree copy"
        )

    def test_relative_log_dir_from_project_root_unchanged(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        resolved = Orchestrator._resolve_host_log_dir(".odin/logs", cwd=project)
        assert resolved == (project / ".odin" / "logs").resolve()

    def test_absolute_log_dir_passes_through(self, tmp_path):
        abs_logs = tmp_path / "elsewhere" / "logs"
        resolved = Orchestrator._resolve_host_log_dir(str(abs_logs), cwd=tmp_path)
        assert resolved == abs_logs.resolve()

    def test_non_worktree_cwd_unchanged(self, tmp_path):
        # cwd has no .odin/worktrees ancestor — resolve normally.
        resolved = Orchestrator._resolve_host_log_dir(".odin/logs", cwd=tmp_path)
        assert resolved == (tmp_path / ".odin" / "logs").resolve()


class TestTwoAttemptsTwoTraces:
    """Two forced attempts leave two trace files, both readable — even when
    the worktree is reset between attempts (the task #314 scenario)."""

    def test_two_attempts_leave_two_host_trace_files(self, tmp_path):
        project = tmp_path / "proj"
        worktree = project / ".odin" / "worktrees" / "spec1" / "t1"
        worktree.mkdir(parents=True)
        host_logs = project / ".odin" / "logs"
        task_id = "314"

        log_dir = Orchestrator._resolve_host_log_dir(".odin/logs", cwd=worktree)
        trace_file = str(log_dir / f"task_{task_id}.trace.jsonl")
        out_file = str(log_dir / f"task_{task_id}.out")

        # Attempt 1: rotate (nothing yet) + write trace.
        Orchestrator._rotate_live_trace_files(trace_file, out_file)
        Path(trace_file).write_text('{"attempt":1,"event":"step_finish"}')
        Path(out_file).write_text("attempt 1 output")

        # The worktree is reset/cleaned between attempts (root cause of #314's
        # loss when traces lived inside the worktree). The host copy must survive.
        import shutil
        shutil.rmtree(worktree, ignore_errors=True)
        worktree.mkdir(parents=True)

        # Attempt 2: resolve host dir again (same place), rotate prior trace.
        log_dir2 = Orchestrator._resolve_host_log_dir(".odin/logs", cwd=worktree)
        assert log_dir2 == log_dir, "host dir must be stable across attempts"
        trace_file2 = str(log_dir2 / f"task_{task_id}.trace.jsonl")
        out_file2 = str(log_dir2 / f"task_{task_id}.out")
        Orchestrator._rotate_live_trace_files(trace_file2, out_file2)
        Path(trace_file2).write_text('{"attempt":2,"event":"step_finish"}')
        Path(out_file2).write_text("attempt 2 output")

        # Both attempts' evidence survives on the host.
        attempt1 = host_logs / f"task_{task_id}.trace.attempt-1.jsonl"
        attempt1_out = host_logs / f"task_{task_id}.out.attempt-1"
        canonical = host_logs / f"task_{task_id}.trace.jsonl"

        assert attempt1.exists(), "attempt-1 trace must survive the worktree reset"
        assert attempt1.read_text() == '{"attempt":1,"event":"step_finish"}'
        assert attempt1_out.exists() and attempt1_out.read_text() == "attempt 1 output"
        assert canonical.exists() and canonical.read_text() == '{"attempt":2,"event":"step_finish"}'
