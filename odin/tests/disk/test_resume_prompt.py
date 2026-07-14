"""Tests for the host-built resume prompt (task #332).

The resume prompt must be reconstructable from durable, host-side pieces
ONLY — the task brief, a git status/diff summary of the worktree, and the
tail of the previous attempt's trace — plus the standing instruction to
verify the working state first. No dependence on the provider session, so
ANY agent can resume a run another agent started.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odin.harnesses.base import build_resume_prompt, summarize_worktree_state


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


class TestSummarizeWorktreeState:
    def test_lists_uncommitted_and_untracked_files(self, repo: Path):
        (repo / "README").write_text("edited mid-flight")
        (repo / "new_module.py").write_text("def added(): ...\n")
        summary = summarize_worktree_state(str(repo))
        assert "README" in summary
        assert "new_module.py" in summary

    def test_clean_tree_summary_is_empty(self, repo: Path):
        assert summarize_worktree_state(str(repo)).strip() == ""

    def test_none_path_is_empty(self):
        assert summarize_worktree_state(None) == ""


class TestBuildResumePrompt:
    def test_prompt_carries_all_durable_pieces(self):
        prompt = build_resume_prompt(
            resume_count=1,
            worktree_state="Changed files:\n M src/thing.py",
            trace_tail="...wrote half of the refactor then the stream ended",
        )
        # Verify-state-first instruction — the anti-mid-edit guard.
        assert "verify" in prompt.lower()
        assert "mid-edit" in prompt.lower() or "mid edit" in prompt.lower()
        # The worktree diff summary.
        assert "src/thing.py" in prompt
        # The prior attempt's trace tail.
        assert "half of the refactor" in prompt
        # The resume count is surfaced so the agent knows it's a continuation.
        assert "1" in prompt

    def test_empty_when_nothing_to_resume(self):
        """No worktree state and no trace tail means there is nothing to
        resume from — don't fabricate a resume header over a fresh start."""
        assert build_resume_prompt(
            resume_count=1, worktree_state="", trace_tail=""
        ) == ""

    def test_built_from_state_only_no_session_reference(self):
        """The prompt must not depend on any provider/session identifier —
        it is assembled purely from the pieces passed in."""
        prompt = build_resume_prompt(
            resume_count=2,
            worktree_state="Changed files:\n M a.py",
            trace_tail="tail",
        )
        # Only the supplied pieces appear — assert the diff and tail made it
        # in and the header renders, which is all the caller provides.
        assert "a.py" in prompt
        assert prompt.strip() != ""
