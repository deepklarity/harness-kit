"""Unit tests for the ODIN-STATUS parser hardening (task #237).

The parser used to fail closed on any non-`SUCCESS` value. Real runs
emitted garbage like ``'\\'`` (the literal backslash the model
substituted for "no status") and the whole task FAILED even when
the agent had committed real work. These tests pin the new
behaviour:

- Well-formed ``SUCCESS`` / ``FAILED`` blocks: unchanged.
- Malformed block, no observable work: fail (same as today).
- Malformed block + committed work in the worktree: infer success
  with a metadata marker.
- Malformed block + dirty-but-no-commit worktree: infer success
  (the agent made local edits we can still salvage).
- The raw block is preserved for the orchestrator's ErrorEvent
  recording (W6.5) so the league table can see which model
  emits malformed blocks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odin.harnesses.base import (
    ODIN_STATUS_MARKER,
    OdinStatusResult,
    _git_commits_ahead,
    validate_odin_status,
    validate_odin_status_full,
)


# ── Backwards compatibility: legacy (success, error) tuple ────────


class TestLegacyValidateOdinStatus:
    """The legacy tuple API keeps working so callers that unpacked
    ``success, error = validate_odin_status(text)`` do not break.
    """

    def test_success_returns_true_none(self):
        text = f"work done\n{ODIN_STATUS_MARKER}\nSUCCESS\n"
        success, error = validate_odin_status(text)
        assert success is True
        assert error is None

    def test_failed_returns_false_explicit_message(self):
        text = f"work done\n{ODIN_STATUS_MARKER}\nFAILED\n"
        success, error = validate_odin_status(text)
        assert success is False
        assert "FAILED" in (error or "")

    def test_missing_block_returns_false(self):
        success, error = validate_odin_status("just some prose, no envelope")
        assert success is False
        assert "ODIN-STATUS" in (error or "")


# ── New full API ────────────────────────────────────────────────


class TestValidateOdinStatusFull:
    """The new dataclass returns success/error plus a structured
    ``raw_block`` + ``inferred`` payload for the orchestrator.
    """

    def test_well_formed_success(self):
        text = f"work done\n{ODIN_STATUS_MARKER}\nSUCCESS\n"
        result = validate_odin_status_full(text)
        assert isinstance(result, OdinStatusResult)
        assert result.success is True
        assert result.error is None
        assert result.raw_block is None
        assert result.inferred is False
        assert result.inference_reason is None

    def test_well_formed_failed(self):
        text = f"work done\n{ODIN_STATUS_MARKER}\nFAILED\n"
        result = validate_odin_status_full(text)
        assert result.success is False
        assert "FAILED" in (result.error or "")
        assert result.raw_block is None
        assert result.inferred is False

    def test_empty_stdout_fails(self):
        result = validate_odin_status_full("")
        assert result.success is False
        assert "no output" in (result.error or "").lower()
        assert result.raw_block is None

    def test_missing_block_fails(self):
        result = validate_odin_status_full("no envelope here at all")
        assert result.success is False
        assert "ODIN-STATUS" in (result.error or "")
        assert result.raw_block is None
        assert result.inferred is False

    def test_malformed_block_records_raw_value(self):
        """The literal backslash the agent emitted for task 234:
        ``-------ODIN-STATUS-------\n\\\n``. The parser must capture
        the raw block so the orchestrator can record it.
        """
        text = f"real work was done\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text)
        assert result.success is False
        # The raw value MUST be preserved verbatim — this is what
        # the ErrorEvent row carries into the league table.
        assert result.raw_block == "\\"
        assert result.inferred is False

    def test_malformed_block_with_unparseable_garbage(self):
        text = f"work\n{ODIN_STATUS_MARKER}\n¯\\_(ツ)_/¯\n"
        result = validate_odin_status_full(text)
        assert result.success is False
        assert "¯" in (result.raw_block or "")

    def test_json_escaped_newlines_are_normalised(self):
        """Opencode / Claude stream JSON keeps the agent's text
        inside a JSON string, so backslash-n appears literally.
        The full parser must normalise them just like the legacy
        helper does.
        """
        text = (
            "work done\\n"
            f"{ODIN_STATUS_MARKER}\\n"
            "SUCCESS\\n"
            f"-------ODIN-SUMMARY-------\\nok\\n"
        )
        result = validate_odin_status_full(text)
        assert result.success is True
        assert result.error is None

    def test_malformed_block_without_worktree_fails(self):
        """Without a worktree path we have nothing to infer from —
        the parser must stay strict here, same as today."""
        text = f"work\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text)
        assert result.success is False
        assert result.raw_block == "\\"


# ── Worktree-based inference ────────────────────────────────────


@pytest.fixture
def git_worktree(tmp_path: Path) -> Path:
    """Create a minimal git repo on a ``task/foo`` branch off an
    empty base branch. The parser uses ``git rev-list --count base..HEAD``
    to detect commits ahead.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "test@test")
    _git(wt, "config", "user.name", "test")
    (wt / "README").write_text("seed")
    _git(wt, "add", "README")
    _git(wt, "commit", "-q", "-m", "seed")
    # Task branch with one commit ahead.
    _git(wt, "checkout", "-q", "-b", "task/237")
    (wt / "feature.py").write_text("def f(): return 1\n")
    _git(wt, "add", "feature.py")
    _git(wt, "commit", "-q", "-m", "real work")
    return wt


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


class TestGitCommitsAhead:
    def test_returns_one_when_branch_has_commit(self, git_worktree: Path):
        ahead = _git_commits_ahead(str(git_worktree), base="main")
        assert ahead == 1

    def test_returns_zero_when_at_base(self, tmp_path: Path):
        wt = tmp_path / "wt"
        wt.mkdir()
        _git(wt, "init", "-q", "-b", "main")
        _git(wt, "config", "user.email", "t@t")
        _git(wt, "config", "user.name", "t")
        (wt / "f").write_text("x")
        _git(wt, "add", "f")
        _git(wt, "commit", "-q", "-m", "seed")
        ahead = _git_commits_ahead(str(wt), base="main")
        assert ahead == 0

    def test_returns_zero_for_non_git_dir(self, tmp_path: Path):
        non_git = tmp_path / "nope"
        non_git.mkdir()
        assert _git_commits_ahead(str(non_git)) == 0

    def test_returns_zero_when_path_missing(self, tmp_path: Path):
        missing = tmp_path / "missing"
        assert _git_commits_ahead(str(missing)) == 0


class TestValidateWithInference:
    """The headline behaviour: a malformed block no longer kills the
    run when the agent actually committed work.
    """

    def test_malformed_block_with_committed_work_infers_success(
        self, git_worktree: Path
    ):
        text = f"work\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text, worktree_path=str(git_worktree))
        assert result.success is True
        assert result.inferred is True
        assert result.raw_block == "\\"
        assert result.inference_reason is not None
        assert "commit" in result.inference_reason.lower()

    def test_malformed_block_with_no_commits_fails(self, git_worktree: Path):
        """If the agent emitted a malformed block but the worktree
        has no commits ahead of the base, we cannot infer success
        — fail as today.
        """
        wt = git_worktree
        # Rewind to main so no commits are ahead.
        _git(wt, "checkout", "-q", "main")
        text = f"work\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text, worktree_path=str(wt))
        assert result.success is False
        assert result.inferred is False
        assert result.raw_block == "\\"

    def test_malformed_block_with_dirty_worktree_infers_success(
        self, tmp_path: Path
    ):
        """Uncommitted edits in the worktree count as observable
        work — the harness auto-commits after the run, so if the
        agent made local edits the next step can still salvage them.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        _git(wt, "init", "-q", "-b", "main")
        _git(wt, "config", "user.email", "t@t")
        _git(wt, "config", "user.name", "t")
        (wt / "f").write_text("x")
        _git(wt, "add", "f")
        _git(wt, "commit", "-q", "-m", "seed")
        # Dirty edit, no commit.
        (wt / "f").write_text("dirty")
        text = f"work\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text, worktree_path=str(wt))
        assert result.success is True
        assert result.inferred is True
        assert "uncommitted" in (result.inference_reason or "").lower()

    def test_malformed_block_with_clean_worktree_fails(
        self, git_worktree: Path
    ):
        """Branch has 1 commit ahead, but the user just rebased /
        rewound — no observable work since the base. The parser
        must still fall back to fail.
        """
        wt = git_worktree
        _git(wt, "checkout", "-q", "main")
        text = f"work\n{ODIN_STATUS_MARKER}\n\\\n"
        result = validate_odin_status_full(text, worktree_path=str(wt))
        assert result.success is False
        assert result.inferred is False

    def test_success_block_does_not_consult_worktree(self, tmp_path: Path):
        """A well-formed SUCCESS block must never trigger inference
        even if the worktree is empty — the parser trusts the
        declared verdict.
        """
        text = f"work\n{ODIN_STATUS_MARKER}\nSUCCESS\n"
        result = validate_odin_status_full(text, worktree_path=str(tmp_path))
        assert result.success is True
        assert result.inferred is False
        assert result.inference_reason is None


# ── Replay: the exact symptom from task #234 ──────────────────────


class TestReplayTask234:
    """Replay the live-case symptom from task #234 against a
    worktree that looks like 234's post-run state. The agent
    emitted a literal backslash for the ODIN-STATUS value AND
    committed real work — under the old parser the run FAILED,
    under the new parser the run is recovered.

    The ``spec_branch`` is the branch 234 was forked off; the
    parser counts commits between ``main`` (the task base) and
    ``HEAD`` (the task branch with the fix).
    """

    @pytest.fixture
    def worktree_234(self, tmp_path: Path) -> Path:
        """Stand-in for .odin/worktrees/sp_fable_w7/234 after the
        task #234 agent finished. main is the pre-task base; the
        task branch has 2 commits ahead representing the agent's
        shipped fix.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        _git(wt, "init", "-q", "-b", "main")
        _git(wt, "config", "user.email", "t@t")
        _git(wt, "config", "user.name", "t")
        # Two seeds on main = the base spec state.
        (wt / "README").write_text("seed")
        _git(wt, "add", "README")
        _git(wt, "commit", "-q", "-m", "seed")
        (wt / "src.py").write_text("def placeholder(): pass\n")
        _git(wt, "add", "src.py")
        _git(wt, "commit", "-q", "-m", "initial scaffolding")
        # Branch with the agent's fix.
        _git(wt, "checkout", "-q", "-b", "task/sp_fable_w7/234")
        (wt / "src.py").write_text("def real_fix(): return 42\n")
        _git(wt, "add", "src.py")
        _git(wt, "commit", "-q", "-m", "feat: implement real fix")
        (wt / "test_src.py").write_text("def test_real_fix(): assert real_fix() == 42\n")
        _git(wt, "add", "test_src.py")
        _git(wt, "commit", "-q", "-m", "test: cover real fix")
        return wt

    def test_234_malformed_block_now_yields_live_task(
        self, worktree_234: Path
    ):
        """Acceptance: the exact block from task #234's stdout
        + the worktree that 234 left behind = a live task, not
        FAILED.
        """
        # This is the literal text the agent emitted for 234's
        # ODIN-STATUS block, recovered from the live log.
        stdout_234 = (
            "Did the work and ran the tests.\n"
            "-------ODIN-STATUS-------\n"
            "\\\n"                              # ← the bug
            "-------ODIN-SUMMARY-------\n"
            "Should have been SUCCESS.\n"
        )

        # Old behaviour: success=False, run killed, 20-minute
        # attempt wasted. New behaviour: success=True via worktree
        # inference, run proceeds, the league-table records the
        # malformed block for review.
        result = validate_odin_status_full(
            stdout_234, worktree_path=str(worktree_234),
        )
        assert result.success is True, (
            "Acceptance: task #234's malformed block + its committed "
            "work must yield a live task, not FAILED."
        )
        assert result.inferred is True
        assert result.raw_block == "\\"
        assert "commit" in (result.inference_reason or "").lower()

    def test_234_legacy_helper_still_returns_false(self, worktree_234: Path):
        """Regression guard: the legacy (success, error) helper
        must NOT lie about success — callers that don't know
        about inference must keep seeing the strict verdict so
        their behaviour doesn't silently change.
        """
        stdout_234 = (
            "Did the work.\n"
            "-------ODIN-STATUS-------\n"
            "\\\n"
        )
        success, error = validate_odin_status(stdout_234)
        assert success is False
        assert "ODIN-STATUS" in (error or "")