"""Replay tests for scripts/self_audit_diff.sh against the task-170 fixture.

The task-170 forensic audit found that an agent shipped triplicate definitions
of an email-to-agent parser (two `def _extract_agent(` plus a `_agent_for_email`
twin in one file) and ~90 lines of commented-out `contextStats` dead code. The
cleanup landed in commit e0a8e187; the bad state is its parent fe766f45.

These tests replay the self-audit script against both commits to prove it would
have caught the slop BEFORE it reached review — the whole point of the
pre-completion gate. fe766f45 must flag the duplicates and commented code;
e0a8e187 (the cleanup) must come back clean.

Tags: [simple] — local git reads + a shell script, no network, no agents.
Skipped automatically on checkouts that lack the fixture commits (shallow
clones, fresh worktrees without history depth).
"""

import os
import subprocess

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "scripts", "self_audit_diff.sh"
)
SCRIPT = os.path.abspath(SCRIPT)
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT, "..", ".."))

BAD_COMMIT = "fe766f45"      # W3.17 feat — introduced the triplicate parser + dead code
CLEAN_COMMIT = "e0a8e187"    # cleanup — collapsed the parser, dropped the dead code


def _commit_exists(ref: str) -> bool:
    res = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ref, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return res.returncode == 0


pytestmark = pytest.mark.skipif(
    not (_commit_exists(BAD_COMMIT) and _commit_exists(CLEAN_COMMIT)),
    reason=f"fixture commits {BAD_COMMIT}/{CLEAN_COMMIT} not in history (shallow clone?)",
)


def _run_script(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", SCRIPT, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class TestScriptExists:
    def test_script_is_present_and_executable(self):
        assert os.path.isfile(SCRIPT), f"missing script at {SCRIPT}"


class TestReplayTask170Bad:
    """Replaying on fe766f45 (the known-bad introducing commit) MUST flag the
    duplicate `_extract_agent` definition and the commented-out dead code.
    If this test passes, the gate would have caught task-170's slop before
    review."""

    def test_exits_nonzero(self):
        result = _run_script(BAD_COMMIT)
        assert result.returncode == 1, (
            f"expected exit 1 (issues found) on {BAD_COMMIT}, got "
            f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_flags_duplicate_extract_agent(self):
        """The headline task-170 defect: two `def _extract_agent(` in one file."""
        result = _run_script(BAD_COMMIT)
        assert "_extract_agent" in result.stdout, (
            f"expected _extract_agent flagged as duplicate\nstdout:\n{result.stdout}"
        )

    def test_flags_commented_out_code(self):
        """The App.tsx commented-out contextStats blocks must be surfaced."""
        result = _run_script(BAD_COMMIT)
        assert "commented" in result.stdout.lower(), (
            f"expected commented-out code flagged\nstdout:\n{result.stdout}"
        )

    def test_machine_scannable_summary_line(self):
        """Last stdout line is SELF_AUDIT: ... for agent/pipeline consumption."""
        result = _run_script(BAD_COMMIT)
        last_line = result.stdout.strip().splitlines()[-1]
        assert last_line.startswith("SELF_AUDIT:"), (
            f"expected SELF_AUDIT summary line, got: {last_line!r}"
        )


class TestReplayTask170Clean:
    """Replaying on e0a8e187 (the cleanup) must come back clean — proving the
    script does not false-positive on the fixed state."""

    def test_exits_zero(self):
        result = _run_script(CLEAN_COMMIT)
        assert result.returncode == 0, (
            f"expected exit 0 (clean) on {CLEAN_COMMIT}, got "
            f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_does_not_flag_extract_agent(self):
        result = _run_script(CLEAN_COMMIT)
        assert "_extract_agent" not in result.stdout, (
            f"_extract_agent should not be flagged after cleanup\n"
            f"stdout:\n{result.stdout}"
        )


class TestUsageErrors:
    def test_missing_commit_exits_two(self):
        """A nonexistent ref is a usage error, distinct from 'issues found'."""
        result = _run_script("definitely-not-a-real-commit-xyz")
        assert result.returncode == 2
