"""Tests for run-scoped worktree delta detection (task #331).

The evidence ladder must measure work against the HEAD recorded when THIS
run began — not ``main..HEAD``. ``main..HEAD`` counts commits a prior
attempt already left on the branch, so it reports "work" for a run that
did nothing. ``worktree_has_work_since(start_head)`` measures only the
delta this run produced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odin.harnesses.base import (
    _git_head,
    worktree_has_work_since,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "README").write_text("seed")
    _git(wt, "add", "README")
    _git(wt, "commit", "-q", "-m", "seed")
    return wt


class TestGitHead:
    def test_returns_current_head(self, repo: Path):
        head = _git_head(str(repo))
        assert head
        assert len(head) >= 7

    def test_none_for_non_git(self, tmp_path: Path):
        d = tmp_path / "plain"
        d.mkdir()
        assert _git_head(str(d)) is None

    def test_none_for_missing_path(self, tmp_path: Path):
        assert _git_head(str(tmp_path / "nope")) is None

    def test_none_for_none_path(self):
        assert _git_head(None) is None


class TestWorktreeHasWorkSince:
    def test_no_work_when_head_unchanged(self, repo: Path):
        start = _git_head(str(repo))
        assert worktree_has_work_since(str(repo), start) is False

    def test_new_commit_since_start_is_work(self, repo: Path):
        start = _git_head(str(repo))
        (repo / "feature.py").write_text("def f(): return 1\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-q", "-m", "real work")
        assert worktree_has_work_since(str(repo), start) is True

    def test_uncommitted_edit_is_work_when_clean_at_start(self, repo: Path):
        start = _git_head(str(repo))
        (repo / "README").write_text("edited but not committed")
        assert worktree_has_work_since(str(repo), start, start_dirty=False) is True

    def test_untracked_file_is_work_when_clean_at_start(self, repo: Path):
        start = _git_head(str(repo))
        (repo / "new.txt").write_text("brand new")
        assert worktree_has_work_since(str(repo), start, start_dirty=False) is True

    def test_ambient_dirt_is_not_work_when_dirty_at_start(self, repo: Path):
        """The ambient-cwd trap: a run that fell back to the shared project
        checkout starts on a dirty tree (a developer's uncommitted edits).
        Those must NOT be read as the agent's work — only new commits
        count when the tree was already dirty at run start."""
        # Tree is dirty before the run begins.
        (repo / "README").write_text("developer's unrelated edit")
        start = _git_head(str(repo))
        # The run does nothing new.
        assert worktree_has_work_since(str(repo), start, start_dirty=True) is False

    def test_new_commit_counts_even_when_dirty_at_start(self, repo: Path):
        """A real commit is always attributable to the run, even if the
        tree was dirty at start."""
        (repo / "README").write_text("developer's unrelated edit")
        start = _git_head(str(repo))
        (repo / "feature.py").write_text("def f(): return 1\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-q", "-m", "agent work")
        assert worktree_has_work_since(str(repo), start, start_dirty=True) is True

    def test_preexisting_commit_is_not_counted_as_this_runs_work(self, repo: Path):
        """The crux: a commit that existed BEFORE this run began must not
        register as this run's work. ``main..HEAD`` would count it; the
        start-HEAD delta must not."""
        # A prior attempt left a commit on the branch.
        (repo / "prior.py").write_text("from a previous attempt\n")
        _git(repo, "add", "prior.py")
        _git(repo, "commit", "-q", "-m", "prior attempt work")
        # THIS run starts here — HEAD already has the prior commit.
        start = _git_head(str(repo))
        # This run does nothing.
        assert worktree_has_work_since(str(repo), start) is False

    def test_no_start_head_never_counts_ambient_state_as_work(self, repo: Path):
        """When the start HEAD could not be recorded there is no run-scoped
        baseline, so NOTHING counts as this run's work — not even a dirty
        tree. This guards the ambient-cwd trap: a run that fell back to the
        project checkout (no task worktree) must not read a developer's
        uncommitted edits as an agent completion."""
        assert worktree_has_work_since(str(repo), None) is False
        (repo / "README").write_text("dirty")
        assert worktree_has_work_since(str(repo), None) is False
