"""Tests for resumable-work detection (task #332).

The strict evidence-ladder signal (``worktree_has_work_since``) drops
uncommitted edits when the tree was dirty at run start — the ambient-cwd
guard. That guard is correct for the project-root fallback, but it breaks a
*resume*: the second resume attempt starts on the FIRST attempt's uncommitted
edits (dirty at start), so strict has_work reports False even though there is
obviously work to resume.

``worktree_has_resumable_work`` is the signal for the truncation-resume path.
It runs only inside an isolated task worktree, where every uncommitted change
is agent work by construction — so it counts uncommitted edits regardless of
the start-dirty state, while still returning False for a genuinely clean tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odin.harnesses.base import (
    _git_head,
    worktree_has_resumable_work,
    worktree_has_work_since,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
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


class TestResumableWork:
    def test_clean_tree_has_no_resumable_work(self, repo: Path):
        start = _git_head(str(repo))
        assert worktree_has_resumable_work(str(repo), start) is False

    def test_new_commit_is_resumable_work(self, repo: Path):
        start = _git_head(str(repo))
        (repo / "feature.py").write_text("def f(): return 1\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-q", "-m", "work")
        assert worktree_has_resumable_work(str(repo), start) is True

    def test_uncommitted_edit_is_resumable_work(self, repo: Path):
        start = _git_head(str(repo))
        (repo / "partial.py").write_text("def half(): return  # cut off\n")
        assert worktree_has_resumable_work(str(repo), start) is True

    def test_dirty_at_start_still_resumable(self, repo: Path):
        """The crux: this is the second-resume case. The prior attempt left
        uncommitted edits (dirty at start). Strict has_work drops them; the
        resumable signal keeps them, so the resume loop doesn't stall."""
        # Prior attempt's uncommitted work is already present at run start.
        (repo / "partial.py").write_text("def half(): return  # from attempt 1\n")
        start = _git_head(str(repo))
        # Strict signal (with the ambient guard) sees no NEW work.
        assert worktree_has_work_since(str(repo), start, start_dirty=True) is False
        # The resumable signal correctly reports there is work to resume.
        assert worktree_has_resumable_work(str(repo), start) is True

    def test_none_path_is_not_resumable(self):
        assert worktree_has_resumable_work(None, None) is False
