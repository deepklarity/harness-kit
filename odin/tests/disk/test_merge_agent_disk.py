"""Disk tests for the merge agent's additive-non-overlapping auto-resolve
(task 254) — real git repos, real ``WorktreeManager``, real conflicts.

Replays the exact shape of task 249's conflict (two tasks each add a new,
disjoint constant + choice-tuple entry to the same list) and confirms it
now auto-merges without a human reply. A constructed modify-vs-add
conflict (one side edits an existing line) still parks, proving the
confidence gate holds the line at real edits.
"""

import subprocess
from pathlib import Path

import pytest

from odin.worktree import WorktreeManager


def _run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, **kwargs)


def _init_repo(path: Path) -> None:
    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "config", "user.email", "test@test.com"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# Test repo\n")
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "-m", "initial"], cwd=path)


def _commit_file(path: Path, filename: str, content: str, msg: str) -> str:
    (path / filename).write_text(content)
    _run(["git", "add", filename], cwd=path)
    _run(["git", "commit", "-m", msg], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def wt(repo):
    return WorktreeManager(repo, worktree_dir=".odin/worktrees")


_BASE_MODELS_PY = '''class ErrorEvent:
    SOURCE_A = "a"
    SOURCE_B = "b"
    SOURCE_C = "c"

    SOURCE_CHOICES = (
        (SOURCE_A, "A"),
        (SOURCE_B, "B"),
        (SOURCE_C, "C"),
    )
'''

_TASK_A_MODELS_PY = '''class ErrorEvent:
    SOURCE_A = "a"
    SOURCE_B = "b"
    SOURCE_C = "c"
    SOURCE_D = "d_from_task_a"

    SOURCE_CHOICES = (
        (SOURCE_A, "A"),
        (SOURCE_B, "B"),
        (SOURCE_C, "C"),
        (SOURCE_D, "D from task a"),
    )
'''

_TASK_B_MODELS_PY = '''class ErrorEvent:
    SOURCE_A = "a"
    SOURCE_B = "b"
    SOURCE_C = "c"
    SOURCE_E = "e_from_task_b"

    SOURCE_CHOICES = (
        (SOURCE_A, "A"),
        (SOURCE_B, "B"),
        (SOURCE_C, "C"),
        (SOURCE_E, "E from task b"),
    )
'''

# One side (task C) edits an EXISTING line (SOURCE_C's value) instead of
# only adding new ones — a real edit, not a pure addition. It touches the
# same line task A's addition sits right after, so git conflicts on it
# (same as the SOURCE_D/SOURCE_E case) — but this time the gate must
# reject it, because SOURCE_C's value itself changed.
_TASK_C_MODIFY_MODELS_PY = '''class ErrorEvent:
    SOURCE_A = "a"
    SOURCE_B = "b"
    SOURCE_C = "c_modified_by_task_c"
    SOURCE_F = "f_from_task_c"

    SOURCE_CHOICES = (
        (SOURCE_A, "A"),
        (SOURCE_B, "B"),
        (SOURCE_C, "C"),
        (SOURCE_F, "F from task c"),
    )
'''


class TestAdditiveNonOverlappingAutoMerge:
    """Replay of task 249's exact conflict shape: two tasks append a
    distinct constant + choice-tuple entry at the same spot. Both sides
    are pure additions against the merge base — the agent applies
    keep-both without asking."""

    def test_replays_task_249_conflict_and_auto_merges(self, wt, repo):
        _commit_file(repo, "models.py", _BASE_MODELS_PY, "add base models.py")
        wt.create_spec_branch("sp_249replay")
        path_a = wt.create_task_worktree("sp_249replay", "248")
        path_b = wt.create_task_worktree("sp_249replay", "249")

        _commit_file(path_a, "models.py", _TASK_A_MODELS_PY, "task 248: add SOURCE_D")
        _commit_file(path_b, "models.py", _TASK_B_MODELS_PY, "task 249: add SOURCE_E")

        r1 = wt.merge_task_into_spec("sp_249replay", "248", "Task 248", attempt_resolution=True)
        assert r1.success is True
        assert r1.conflict is False

        r2 = wt.merge_task_into_spec("sp_249replay", "249", "Task 249", attempt_resolution=True)

        # No human reply needed — auto-merged.
        assert r2.success is True
        assert r2.conflict is False
        assert r2.needs_human is False
        assert "models.py" in r2.resolved_files

        # Rationale names the safety net and the non-overlap proof.
        assert r2.resolution_rationale is not None
        assert "W5.12" in r2.resolution_rationale
        assert "models.py" in r2.resolution_rationale

        # Both additions landed on the spec branch — nothing lost.
        show = _run(["git", "show", "spec/sp_249replay:models.py"], cwd=repo)
        assert "SOURCE_D" in show.stdout
        assert "d_from_task_a" in show.stdout
        assert "SOURCE_E" in show.stdout
        assert "e_from_task_b" in show.stdout

        # And the merged file still compiles.
        compile(show.stdout, "models.py", "exec")

    def test_modify_vs_add_still_parks(self, wt, repo):
        """One side edits an existing line (SOURCE_B's value) instead of
        only adding new content — a real edit, not a pure addition. The
        gate must fail closed and escalate exactly like before."""
        _commit_file(repo, "models.py", _BASE_MODELS_PY, "add base models.py")
        wt.create_spec_branch("sp_modifyvsadd")
        path_a = wt.create_task_worktree("sp_modifyvsadd", "301")
        path_c = wt.create_task_worktree("sp_modifyvsadd", "302")

        _commit_file(path_a, "models.py", _TASK_A_MODELS_PY, "task 301: add SOURCE_D")
        _commit_file(path_c, "models.py", _TASK_C_MODIFY_MODELS_PY, "task 302: edit SOURCE_B + add SOURCE_F")

        r1 = wt.merge_task_into_spec("sp_modifyvsadd", "301", "Task 301", attempt_resolution=True)
        assert r1.success is True

        r2 = wt.merge_task_into_spec("sp_modifyvsadd", "302", "Task 302", attempt_resolution=True)

        assert r2.success is False
        assert r2.conflict is True
        assert r2.needs_human is True
        assert "models.py" in r2.ambiguous_files

    def test_pure_additive_disabled_without_attempt_resolution(self, wt, repo):
        """Without ``attempt_resolution``, conflicts still park — the
        auto-merge is opt-in the same way mechanical resolution already
        is (unchanged behavior, regression guard)."""
        wt.create_spec_branch("sp_noattempt")
        path_a = wt.create_task_worktree("sp_noattempt", "401")
        path_b = wt.create_task_worktree("sp_noattempt", "402")

        _commit_file(path_a, "models.py", _TASK_A_MODELS_PY, "task 401")
        _commit_file(path_b, "models.py", _TASK_B_MODELS_PY, "task 402")

        wt.merge_task_into_spec("sp_noattempt", "401", "Task 401")
        r2 = wt.merge_task_into_spec("sp_noattempt", "402", "Task 402")

        assert r2.success is False
        assert r2.conflict is True


# ------------------------------------------------------------------
# Post-merge safety gate (task 338) — real git repos, real merge.
# The gate runs AFTER every merge commit and refuses to land the
# result if any changed file contains conflict markers or fails to
# parse as Python. The merge commit is reset; the gate violation is
# surfaced via MergeResult.gate_violations.
# ------------------------------------------------------------------


_GATE_PY_BASE = '''def hello():
    return 1
'''

_GATE_PY_WITH_MARKERS = '''def hello():
<<<<<<< HEAD
    return 1
=======
    return 2
>>>>>>> task/spec/338
'''


class TestPostMergeGateRefusesMarkers:
    """The historical breakage: the merge agent's resolution left
    literal ``<<<<<<<``/``=======``/``>>>>>>>`` markers in a
    committed file. The post-merge gate must catch this and refuse
    the merge."""

    def test_marker_in_merged_file_blocks_landing(self, wt, repo):
        # Build a repo where task A's branch delivers a file that
        # already contains literal conflict markers (simulating a
        # broken merge-agent resolution from a previous task).
        _commit_file(repo, "views.py", _GATE_PY_BASE, "add base views.py")
        wt.create_spec_branch("sp_gate_markers")
        path_a = wt.create_task_worktree("sp_gate_markers", "338")
        # The task branch itself committed a file with markers.
        _commit_file(path_a, "views.py", _GATE_PY_WITH_MARKERS, "task 338: oops")

        result = wt.merge_task_into_spec(
            "sp_gate_markers", "338", "Task 338", attempt_resolution=True,
        )

        # Merge did not land.
        assert result.success is False
        assert result.needs_human is True
        assert len(result.gate_violations) >= 1
        for path, error_type, _ in result.gate_violations:
            assert path == "views.py"
            assert error_type == "conflict_marker"

        # The spec branch on disk is unchanged — the merge commit
        # was reset, so the file on the spec branch is still the
        # base version (no markers).
        show = _run(["git", "show", "spec/sp_gate_markers:views.py"], cwd=repo)
        assert "<<<<<<<" not in show.stdout


_GATE_PY_UNCLOSED_DICT = '''def view():
    return {
        "a": 1,
'''


class TestPostMergeGateRefusesSyntaxError:
    """The other historical breakage: keep-both concatenated two
    partial dicts and produced an unclosed literal. The gate's
    ``compile()`` check catches it before the commit lands."""

    def test_unparseable_python_blocks_landing(self, wt, repo):
        _commit_file(repo, "views.py", _GATE_PY_BASE, "add base views.py")
        wt.create_spec_branch("sp_gate_syntax")
        path_a = wt.create_task_worktree("sp_gate_syntax", "338")
        _commit_file(
            path_a, "views.py", _GATE_PY_UNCLOSED_DICT,
            "task 338: unclosed dict",
        )

        result = wt.merge_task_into_spec(
            "sp_gate_syntax", "338", "Task 338", attempt_resolution=True,
        )

        assert result.success is False
        assert result.needs_human is True
        assert any(
            path == "views.py" and error_type == "syntax_error"
            for path, error_type, _ in result.gate_violations
        )
        # Spec branch on disk still has the BASE version — the merge
        # commit was reset, so the unclosed dict never landed.
        show = _run(["git", "show", "spec/sp_gate_syntax:views.py"], cwd=repo)
        assert "return {" not in show.stdout  # base is `return 1`, not `return {`
        assert "return 1" in show.stdout


class TestPostMergeGateCleanMerge:
    """Regression guard: the gate MUST NOT block a clean merge that
    contains no markers and parses as Python. The whole point of
    the gate is to be cheap enough that it doesn't slow down the
    happy path."""

    def test_clean_merge_still_lands(self, wt, repo):
        _commit_file(repo, "views.py", _GATE_PY_BASE, "add base views.py")
        wt.create_spec_branch("sp_gate_clean")
        path_a = wt.create_task_worktree("sp_gate_clean", "338")
        _commit_file(
            path_a, "views.py",
            'def hello():\n    return 42\n',
            "task 338: clean",
        )

        result = wt.merge_task_into_spec(
            "sp_gate_clean", "338", "Task 338", attempt_resolution=True,
        )

        assert result.success is True
        assert result.conflict is False
        assert result.gate_violations == []
        show = _run(["git", "show", "spec/sp_gate_clean:views.py"], cwd=repo)
        assert "return 42" in show.stdout
