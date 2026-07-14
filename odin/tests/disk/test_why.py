"""Disk tests for ``testing_tools/why.py`` — the provenance walker.

Exercises the CLI end-to-end against real git repos with commits that
carry Task-Id / Spec-Id trailers.  Verifies the blame → trailer →
task/spec chain renders correctly for single lines, ranges, whole files,
and commits that lack trailers (graceful degradation).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WHY_PY = REPO_ROOT / "testing_tools" / "why.py"


def _run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kwargs)


def _init_repo(path: Path) -> None:
    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "config", "user.email", "test@test.com"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)


def _commit(path: Path, filename: str, content: str, msg: str) -> str:
    (path / filename).write_text(content)
    _run(["git", "add", filename], cwd=path)
    _run(["git", "commit", "-m", msg], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


def _why(repo: Path, arg: str) -> subprocess.CompletedProcess:
    """Run why.py with the given argument (e.g. 'file.py:3')."""
    return _run([sys.executable, str(WHY_PY), arg], cwd=repo)


@pytest.fixture
def repo(tmp_path):
    _init_repo(tmp_path)
    return tmp_path


class TestWhySingleLine:
    def test_line_with_trailers(self, repo):
        """A line from a commit carrying trailers resolves to task/spec."""
        _commit(
            repo, "app.py",
            "def hello():\n    return 'world'\n",
            "Add hello\n\nTask-Id: 42\nSpec-Id: sp_demo",
        )
        r = _why(repo, "app.py:2")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "app.py:2" in out
        assert "42" in out
        assert "sp_demo" in out
        assert "Task-Id" in out
        assert "Spec-Id" in out

    def test_line_without_trailers(self, repo):
        """A line from a trailerless commit degrades gracefully — no crash,
        clear 'no trailer' indication."""
        _commit(repo, "bare.py", "x = 1\n", "bare commit")
        r = _why(repo, "bare.py:1")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "bare.py:1" in out
        assert "bare commit" in out
        # Must not fabricate trailers
        assert "Task-Id" not in out or "none" in out.lower() or "—" in out


class TestWhyRange:
    def test_range_resolves_each_line(self, repo):
        """A range 'file:start-end' shows provenance for the span."""
        _commit(
            repo, "mod.py",
            "a = 1\nb = 2\nc = 3\nd = 4\n",
            "Add module\n\nTask-Id: 7\nSpec-Id: sp_rng",
        )
        r = _why(repo, "mod.py:2-3")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "7" in out
        assert "sp_rng" in out

    def test_comma_lines(self, repo):
        """Comma-separated line numbers are accepted."""
        _commit(
            repo, "m.py",
            "a\nb\nc\nd\n",
            "init\n\nTask-Id: 1\nSpec-Id: sp_c",
        )
        r = _why(repo, "m.py:1,4")
        assert r.returncode == 0, r.stderr
        assert "1" in r.stdout  # task id


class TestWhyFileSummary:
    def test_whole_file_summary(self, repo):
        """Bare 'file' (no line) shows a summary of distinct commit
        provenances rather than every line."""
        _commit(
            repo, "s.py", "a = 1\n", "first\n\nTask-Id: 10\nSpec-Id: sp_s",
        )
        _commit(
            repo, "s.py", "a = 1\nb = 2\n", "second\n\nTask-Id: 11\nSpec-Id: sp_s",
        )
        r = _why(repo, "s.py")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "10" in out
        assert "11" in out
        assert "sp_s" in out


class TestWhyErrors:
    def test_nonexistent_file(self, repo):
        r = _why(repo, "nope.py:1")
        assert r.returncode != 0

    def test_nonexistent_line(self, repo):
        _commit(repo, "small.py", "only line\n", "x")
        # Line 999 doesn't exist — git blame fails; why.py must not crash
        # with a traceback.
        r = _why(repo, "small.py:999")
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    def test_no_arg_prints_usage(self, repo):
        r = _run([sys.executable, str(WHY_PY)], cwd=repo)
        assert r.returncode != 0
        assert "usage" in (r.stderr + r.stdout).lower()
