"""Unit tests for WorktreeManager — mocked subprocess, no real git."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import subprocess

import pytest

from odin.mcps.taskit_mcp.config import HARNESS_GENERATED_PATHS
from odin.worktree import (
    AGENT_CONFIG_PATHS,
    _GITIGNORE_MARKER,
    _WORKTREE_GITIGNORE_PATHS,
    _WORKTREE_LOCAL_PATHS,
    AutoCommitResult,
    MergeResult,
    WorktreeManager,
    _AUTO_COMMIT_MAX_NEW_FILES,
    _is_pollution_path,
    _find_venv_roots,
)


@pytest.fixture
def wt(tmp_path):
    (tmp_path / ".git").mkdir()
    return WorktreeManager(tmp_path, worktree_dir=".odin/worktrees")


def _ok(stdout="", stderr=""):
    """Shorthand for a successful subprocess result."""
    return MagicMock(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr="", stdout=""):
    """Shorthand for a failed subprocess result."""
    return MagicMock(returncode=1, stderr=stderr, stdout=stdout)


# ------------------------------------------------------------------
# MergeResult dataclass
# ------------------------------------------------------------------

class TestMergeResult:
    def test_defaults(self):
        r = MergeResult(success=True)
        assert r.success is True
        assert r.conflict is False
        assert r.error is None

    def test_conflict(self):
        r = MergeResult(success=False, conflict=True, error="files differ")
        assert r.success is False
        assert r.conflict is True
        assert r.error == "files differ"

    def test_error_without_conflict(self):
        r = MergeResult(success=False, error="push failed")
        assert r.conflict is False
        assert r.error == "push failed"


# ------------------------------------------------------------------
# __init__ validation
# ------------------------------------------------------------------

class TestInit:
    def test_raises_valueerror_when_not_git_repo(self, tmp_path):
        with pytest.raises(ValueError, match="Not a git repository"):
            WorktreeManager(tmp_path)

    def test_succeeds_when_git_dir_exists(self, tmp_path):
        (tmp_path / ".git").mkdir()
        wt = WorktreeManager(tmp_path)
        assert wt.project_root == tmp_path


# ------------------------------------------------------------------
# get_worktree_path
# ------------------------------------------------------------------

class TestGetWorktreePath:
    def test_returns_expected_path(self, wt):
        path = wt.get_worktree_path("sp_abc", "42")
        assert path == wt.project_root / ".odin" / "worktrees" / "sp_abc" / "42"


# ------------------------------------------------------------------
# is_ancestor
# ------------------------------------------------------------------

class TestIsAncestor:
    """``is_ancestor`` wraps ``git merge-base --is-ancestor`` for the
    needs_human reconciliation pass. Exit 0 = ancestor (merged); anything
    else = not merged / unknown ref — never a positive signal."""

    @patch("odin.worktree.subprocess.run")
    def test_true_when_exit_zero(self, mock_run, wt):
        mock_run.return_value = _ok()
        assert wt.is_ancestor("task/sp_x/1", "spec/sp_x") is True
        args = mock_run.call_args[0][0]
        assert args[:2] == ["git", "merge-base"]
        assert "--is-ancestor" in args
        assert "task/sp_x/1" in args
        assert "spec/sp_x" in args

    @patch("odin.worktree.subprocess.run")
    def test_false_when_not_ancestor(self, mock_run, wt):
        """Exit 1 = ref exists but is NOT an ancestor → False."""
        mock_run.return_value = _fail()
        assert wt.is_ancestor("task/sp_x/1", "spec/sp_x") is False

    @patch("odin.worktree.subprocess.run")
    def test_false_when_ref_missing(self, mock_run, wt):
        """Exit 128 = unknown ref (e.g. branch deleted) → False, not raise.

        Reconciliation must never claim a merge happened on the basis of an
        error; only exit 0 is a positive signal.
        """
        err = MagicMock(returncode=128, stdout="", stderr="unknown revision")
        mock_run.return_value = err
        assert wt.is_ancestor("task/sp_x/1", "spec/sp_x") is False


# ------------------------------------------------------------------
# create_spec_branch
# ------------------------------------------------------------------

class TestCreateSpecBranch:
    @patch("odin.worktree.subprocess.run")
    def test_creates_branch_from_origin_main(self, mock_run, wt):
        mock_run.side_effect = [
            _fail(),  # rev-parse: branch doesn't exist
            _ok(),    # fetch origin main
            _ok(),    # rev-parse HEAD (repo has commits)
            _ok(),    # rev-parse origin/main exists
            _ok(),    # git branch spec/sp_test origin/main
            _ok(),    # push -u origin
        ]
        result = wt.create_spec_branch("sp_test")
        assert result == "spec/sp_test"
        branch_call = mock_run.call_args_list[4]
        assert "origin/main" in branch_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_idempotent_when_branch_exists(self, mock_run, wt):
        mock_run.return_value = _ok()  # branch exists
        result = wt.create_spec_branch("sp_existing")
        assert result == "spec/sp_existing"
        assert mock_run.call_count == 1

    @patch("odin.worktree.subprocess.run")
    def test_custom_base_branch(self, mock_run, wt):
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist
            _ok(),    # fetch origin develop
            _ok(),    # rev-parse HEAD
            _ok(),    # rev-parse origin/develop
            _ok(),    # branch create
            _ok(),    # push
        ]
        result = wt.create_spec_branch("sp_dev", base_branch="develop")
        assert result == "spec/sp_dev"
        fetch_call = mock_run.call_args_list[1]
        assert "develop" in fetch_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_falls_back_to_local_main(self, mock_run, wt):
        """When origin/main doesn't exist, use local main."""
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist
            _ok(),    # fetch (succeeds but no remote ref)
            _ok(),    # rev-parse HEAD
            _fail(),  # rev-parse origin/main → not found
            _ok(),    # rev-parse main → found (local)
            _ok(),    # branch create
            _ok(),    # push
        ]
        result = wt.create_spec_branch("sp_local")
        assert result == "spec/sp_local"
        branch_call = mock_run.call_args_list[5]
        # Should use "main" (not "origin/main") as base
        assert branch_call[0][0] == ["git", "branch", "spec/sp_local", "main"]

    @patch("odin.worktree.subprocess.run")
    def test_falls_back_to_head(self, mock_run, wt):
        """When neither origin/main nor local main exist, use HEAD."""
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist
            _ok(),    # fetch
            _ok(),    # rev-parse HEAD
            _fail(),  # rev-parse origin/main → not found
            _fail(),  # rev-parse main → not found
            _ok(),    # branch create from HEAD
            _ok(),    # push
        ]
        result = wt.create_spec_branch("sp_head")
        assert result == "spec/sp_head"
        branch_call = mock_run.call_args_list[5]
        assert branch_call[0][0] == ["git", "branch", "spec/sp_head", "HEAD"]

    @patch("odin.worktree.subprocess.run")
    def test_push_failure_is_warning_not_error(self, mock_run, wt):
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist
            _ok(),    # fetch
            _ok(),    # rev-parse HEAD
            _ok(),    # rev-parse origin/main
            _ok(),    # branch create
            _fail(stderr="push failed"),  # push fails
        ]
        result = wt.create_spec_branch("sp_nopush")
        assert result == "spec/sp_nopush"

    @patch("odin.worktree.subprocess.run")
    def test_branch_creation_failure_raises(self, mock_run, wt):
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist
            _ok(),    # fetch
            _ok(),    # rev-parse HEAD
            _ok(),    # rev-parse origin/main
            _fail(stderr="fatal: cannot create branch"),  # branch create fails
            _fail(),  # re-check: branch still doesn't exist -> genuine failure
        ]
        with pytest.raises(RuntimeError, match="Failed to create spec branch"):
            wt.create_spec_branch("sp_fail")

    @patch("odin.worktree.subprocess.run")
    def test_branch_creation_race_lost_is_success(self, mock_run, wt):
        """Parallel dispatch (F51): losing the branch-create race to a
        concurrent task returns the branch instead of raising."""
        mock_run.side_effect = [
            _fail(),  # branch doesn't exist (pre-check)
            _ok(),    # fetch
            _ok(),    # rev-parse HEAD
            _ok(),    # rev-parse origin/main
            _fail(stderr="fatal: cannot lock ref: reference already exists"),
            _ok(),    # re-check: branch exists now (created concurrently)
        ]
        assert wt.create_spec_branch("sp_race") == "spec/sp_race"


# ------------------------------------------------------------------
# create_task_worktree
# ------------------------------------------------------------------

class TestCreateTaskWorktree:
    @patch("odin.worktree.subprocess.run")
    def test_creates_worktree_with_new_branch(self, mock_run, wt):
        mock_run.side_effect = [
            _ok(),    # fetch origin spec/sp_abc
            _ok(),    # rev-parse origin/spec/sp_abc → found
            _fail(),  # rev-parse task branch → doesn't exist
            _ok(),    # worktree add -b
            _ok(),    # rev-parse --git-path info/exclude
        ]
        path = wt.create_task_worktree("sp_abc", "42")
        assert path == wt.worktree_base / "sp_abc" / "42"
        wt_call = mock_run.call_args_list[3]
        assert "-b" in wt_call[0][0]
        assert "task/sp_abc/42" in wt_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_existing_task_branch_no_b_flag(self, mock_run, wt):
        """When task branch already exists, worktree add without -b."""
        mock_run.side_effect = [
            _ok(),    # fetch
            _ok(),    # rev-parse origin/spec branch
            _ok(),    # rev-parse task branch → exists
            _ok(),    # worktree add (no -b)
            _ok(),    # rev-parse --git-path info/exclude
        ]
        path = wt.create_task_worktree("sp_abc", "42")
        assert path == wt.worktree_base / "sp_abc" / "42"
        wt_call = mock_run.call_args_list[3]
        assert "-b" not in wt_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_idempotent_when_worktree_exists(self, mock_run, wt):
        wt_path = wt.worktree_base / "sp_abc" / "42"
        wt_path.mkdir(parents=True)
        (wt_path / ".git").touch()

        path = wt.create_task_worktree("sp_abc", "42")
        assert path == wt_path
        assert mock_run.call_count == 0

    @patch("odin.worktree.subprocess.run")
    def test_stale_directory_recovered_before_creation(self, mock_run, wt):
        """A stale directory (exists but no .git) from a failed run is
        removed before ``git worktree add``.  Without this, retry crashes
        with 'already exists' (task 236)."""
        wt_path = wt.worktree_base / "sp_abc" / "42"
        wt_path.mkdir(parents=True)
        # No .git file — stale state

        mock_run.side_effect = [
            _ok(),    # git worktree remove --force (stale cleanup)
            _ok(),    # fetch origin spec/sp_abc
            _ok(),    # rev-parse origin/spec/sp_abc → found
            _fail(),  # rev-parse task branch → doesn't exist
            _ok(),    # worktree add -b task/sp_abc/42
            _ok(),    # rev-parse --git-path info/exclude
        ]
        path = wt.create_task_worktree("sp_abc", "42")
        assert path == wt_path
        # First git call must be the stale-directory cleanup
        first_call = mock_run.call_args_list[0]
        assert "remove" in first_call[0][0]
        assert "--force" in first_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_worktree_add_failure_raises(self, mock_run, wt):
        mock_run.side_effect = [
            _ok(),    # fetch
            _ok(),    # rev-parse origin/spec
            _fail(),  # task branch doesn't exist
            _fail(stderr="worktree add failed"),  # worktree add fails
        ]
        with pytest.raises(RuntimeError, match="Failed to create worktree"):
            wt.create_task_worktree("sp_abc", "42")

    @patch("odin.worktree.subprocess.run")
    def test_falls_back_to_local_spec_branch(self, mock_run, wt):
        """When origin/spec doesn't exist, use local spec branch."""
        mock_run.side_effect = [
            _ok(),    # fetch
            _fail(),  # rev-parse origin/spec/sp_abc → not found
            _fail(),  # task branch doesn't exist
            _ok(),    # worktree add -b with local spec branch
            _ok(),    # rev-parse --git-path info/exclude
        ]
        wt.create_task_worktree("sp_abc", "42")
        wt_call = mock_run.call_args_list[3]
        # base_ref should be "spec/sp_abc" (local), not "origin/spec/sp_abc"
        assert wt_call[0][0][-1] == "spec/sp_abc"

    @patch("odin.worktree.subprocess.run")
    def test_post_hooks_run_in_worktree_cwd(self, mock_run, wt):
        """Post-hooks should run with cwd set to the worktree path."""
        mock_run.side_effect = [
            _ok(),    # fetch
            _ok(),    # rev-parse origin/spec
            _fail(),  # task branch doesn't exist
            _ok(),    # worktree add
            _ok(),    # rev-parse --git-path info/exclude
            _ok(),    # hook subprocess.run
        ]
        wt_path = wt.worktree_base / "sp_abc" / "42"
        wt.create_task_worktree("sp_abc", "42", post_hooks=["npm install"])
        # The hook is called via subprocess.run directly (not _git); the
        # info/exclude rev-parse precedes it, so it's the 6th call
        hook_call = mock_run.call_args_list[5]
        assert hook_call[1]["cwd"] == wt_path
        assert hook_call[1]["shell"] is True

    @patch("odin.worktree.subprocess.run")
    def test_subprocess_timeout_propagates(self, mock_run, wt):
        """TimeoutExpired from git should propagate."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=120)
        with pytest.raises(subprocess.TimeoutExpired):
            wt.create_task_worktree("sp_abc", "42")


# ------------------------------------------------------------------
# merge_task_into_spec — uses _do_merge's temp worktree pattern
# ------------------------------------------------------------------

class TestMergeTaskIntoSpec:
    """Mock chain must match the actual flow (fallback path, no _spec worktree):
    1. rev-parse task branch exists
    2. (auto-commit check — skipped when worktree dir doesn't exist on disk)
    3. (lock acquired)
    4. check stale merge_wt exists (path.exists)
    5. fetch origin spec branch
    6. worktree add <merge_wt> <spec_branch>
    7. _merge_in_worktree:
       a. fetch origin spec
       b. pull origin spec
       c. diff --stat spec...task (if empty → noop, return early)
       d. merge --no-ff task_branch
       e. push origin spec
    8. (finally) worktree remove merge_wt
    """

    def _merge_wt_path(self, wt):
        return wt.worktree_base / "_merge" / "spec_sp_abc"

    @patch("odin.worktree.subprocess.run")
    def test_successful_merge(self, mock_run, wt):
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            # _do_merge fallback (merge_wt doesn't exist → skip stale cleanup)
            _ok(),    # worktree add merge_wt spec/sp_abc
            # _merge_in_worktree starts
            _ok(),    # fetch origin spec/sp_abc
            _ok(),    # pull origin spec/sp_abc
            _ok(stdout=" file.py | 10 ++++\n 1 file changed"),  # diff --stat
            _ok(),    # merge --no-ff task/sp_abc/42
            _ok(stdout=""),    # post-merge gate (task 338): no files changed
            _ok(),    # push origin spec/sp_abc
            _ok(),    # finally: worktree remove merge_wt
        ]
        result = wt.merge_task_into_spec("sp_abc", "42", "Fix bug")
        assert result.success is True
        assert result.conflict is False
        assert result.noop is False
        assert result.error is None
        assert result.diff_stat is not None

        # Verify merge commit message includes title
        merge_call = mock_run.call_args_list[5]
        assert "Fix bug" in merge_call[0][0][-1]  # -m arg

    @patch("odin.worktree.subprocess.run")
    def test_merge_conflict(self, mock_run, wt):
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _fail(stderr="CONFLICT"),  # merge fails
            _ok(stdout="UU file.py\n"),  # status --porcelain shows conflicts
            _ok(),    # merge --abort
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        assert result.conflict is True
        assert "conflict" in result.error.lower()

    @patch("odin.worktree.subprocess.run")
    def test_merge_conflict_records_conflicting_files(self, mock_run, wt):
        """When a merge conflicts, MergeResult.conflicting_files lists the
        files that blocked the merge so downstream status comments can name
        them (regression for task #112: board comment said only "Merge
        conflict:" with no file list, leaving the operator unable to act
        from the task UI).
        """
        porcelain = (
            "UU opencode.json\n"
            "AA .mcp.json\n"
            "DD removed.txt\n"
            "?? untracked.txt\n"
        )
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _fail(stderr="CONFLICT"),  # merge fails
            _ok(stdout=porcelain),    # status --porcelain shows conflicts
            _ok(),    # merge --abort
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        assert result.conflict is True
        assert result.conflicting_files == ["opencode.json", ".mcp.json", "removed.txt"]
        # Untracked files must not be mistaken for conflicts
        assert "untracked.txt" not in result.conflicting_files

    @patch("odin.worktree.subprocess.run")
    def test_merge_failure_without_conflict(self, mock_run, wt):
        """Merge fails but not due to conflict (e.g. unrelated git error)."""
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _fail(stderr="merge error"),  # merge fails
            _ok(stdout=""),  # status: no conflict markers
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        assert result.conflict is False
        assert "merge failed" in result.error
        # When the merge fails for a non-conflict reason, there is no
        # file list to expose.
        assert result.conflicting_files == []

    @patch("odin.worktree.subprocess.run")
    def test_nonexistent_task_branch(self, mock_run, wt):
        mock_run.return_value = _fail()  # rev-parse: branch doesn't exist
        result = wt.merge_task_into_spec("sp_abc", "999")
        assert result.success is False
        assert "does not exist" in result.error

    @patch("odin.worktree.subprocess.run")
    def test_worktree_add_failure_returns_error(self, mock_run, wt):
        """When worktree add for the merge workspace fails."""
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _fail(stderr="worktree locked"),  # worktree add fails
            # finally: merge_wt doesn't exist → no worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        assert "worktree add failed" in result.error

    @patch("odin.worktree.subprocess.run")
    def test_push_failure_still_returns_success(self, mock_run, wt):
        """Push failure after successful merge → still success (warning only)."""
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _ok(),    # merge succeeds
            _ok(stdout=""),    # post-merge gate (task 338): no files changed
            _fail(stderr="push rejected"),  # push fails
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is True

    @patch("odin.worktree.subprocess.run")
    def test_stale_merge_worktree_cleaned_before_merge(self, mock_run, wt):
        """If merge_wt exists from previous failed attempt, it's removed first."""
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove stale merge_wt
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _ok(),    # merge
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof found)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            _ok(),    # push
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is True
        # First git call after branch check should be stale cleanup
        stale_call = mock_run.call_args_list[1]
        assert "remove" in stale_call[0][0]
        assert "--force" in stale_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_cleanup_runs_even_on_merge_error(self, mock_run, wt):
        """Finally block: merge_wt is removed even when merge raises."""
        merge_wt = self._merge_wt_path(wt)

        # Create merge_wt so .exists() returns True (stale cleanup + finally block)
        merge_wt.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove stale merge_wt
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _fail(stderr="merge failed"),  # merge fails
            _ok(stdout=""),  # status (no conflicts)
            # finally block: merge_wt.exists() → True
            _ok(),    # worktree remove (finally)
        ]

        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        # Verify the last call is worktree remove for cleanup
        last_call = mock_run.call_args_list[-1]
        assert "remove" in last_call[0][0]
        assert str(merge_wt) in last_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_merge_without_title(self, mock_run, wt):
        """Merge commit message format when no title provided."""
        mock_run.side_effect = [
            _ok(),    # rev-parse
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.py | 5 ++\n 1 file changed"),  # diff --stat
            _ok(),    # merge
            _ok(),    # push
            _ok(),    # finally: remove
        ]
        wt.merge_task_into_spec("sp_abc", "42")
        merge_call = mock_run.call_args_list[5]
        msg = merge_call[0][0][-1]  # last arg is the -m message
        assert msg.startswith("Merge task 42")
        # Provenance trailers must be present (Task-Id / Spec-Id).
        assert "Task-Id: 42" in msg
        assert "Spec-Id: sp_abc" in msg

    @patch("odin.worktree.subprocess.run")
    def test_lock_timeout_returns_error(self, mock_run, wt):
        """When file lock acquisition times out, merge returns error."""
        import filelock
        mock_run.return_value = _ok()  # rev-parse: branch exists
        with patch.object(wt, "_spec_lock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock(
                side_effect=filelock.Timeout(lock_file="test.lock")
            )
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is False
        assert result.error is not None


# ------------------------------------------------------------------
# merge_task_into_spec with attempt_resolution=True
# ------------------------------------------------------------------

class TestMergeResolution:
    """When ``attempt_resolution=True`` and a conflict occurs, the merge
    agent attempts to resolve mechanical conflicts (generated configs)
    in-place.  Ambiguous conflicts (product code) abort as before.

    These tests mock the full subprocess chain, including the git calls
    that ``resolve_conflicts_in_worktree`` makes (checkout --ours, add).
    """

    def _merge_wt_path(self, wt):
        return wt.worktree_base / "_merge" / "spec_sp_abc"

    @patch("odin.worktree.subprocess.run")
    def test_mechanical_conflict_auto_resolved(self, mock_run, wt):
        """Generated-config-only conflict → resolved by checkout --ours,
        merge committed and pushed.  This is the core capability: a
        trivial generated-config collision no longer blocks the operator.
        """
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch origin spec
            _ok(),    # pull origin spec
            _ok(stdout=" file.changed\n 1 file"),  # diff --stat (non-empty)
            _fail(stderr="CONFLICT"),  # merge --no-ff fails
            _ok(stdout="UU opencode.json\n"),  # status --porcelain
            # resolve_conflicts_in_worktree:
            _ok(),    # checkout --ours opencode.json
            _ok(),    # add opencode.json
            # back in _merge_in_worktree:
            _ok(),    # commit --no-edit
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            _ok(),    # push origin spec
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec(
            "sp_abc", "42", "Fix bug", attempt_resolution=True,
        )

        assert result.success is True
        assert result.conflict is False
        assert "opencode.json" in result.resolved_files
        assert result.resolution_rationale is not None

    @patch("odin.worktree.subprocess.run")
    def test_ambiguous_conflict_needs_human(self, mock_run, wt):
        """Product-code conflict → merge aborted, needs_human=True.
        The agent must NOT guess on semantic conflicts.
        """
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.changed\n 1 file"),  # diff --stat
            _fail(stderr="CONFLICT"),  # merge fails
            _ok(stdout="UU src/feature.py\n"),  # status --porcelain
            # resolve_conflicts_in_worktree sees ambiguous → no git calls
            # back in _merge_in_worktree: abort + finally
            _ok(),    # merge --abort
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec(
            "sp_abc", "42", "Fix bug", attempt_resolution=True,
        )

        assert result.success is False
        assert result.conflict is True
        assert result.needs_human is True
        assert "src/feature.py" in result.ambiguous_files
        assert result.resolved_files == []

    @patch("odin.worktree.subprocess.run")
    def test_mixed_conflict_needs_human(self, mock_run, wt):
        """Generated config + product code → needs_human (all-or-nothing).
        Even though the generated config could be auto-resolved, the
        presence of an ambiguous file means the whole merge must be
        escalated — no partial merges.
        """
        mock_run.side_effect = [
            _ok(),    # rev-parse
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.changed\n 1 file"),  # diff --stat
            _fail(stderr="CONFLICT"),  # merge fails
            _ok(stdout="UU opencode.json\nUU src/main.py\n"),  # status
            # classify → ambiguous (src/main.py) → no checkout calls
            _ok(),    # merge --abort
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec(
            "sp_abc", "42", attempt_resolution=True,
        )

        assert result.success is False
        assert result.needs_human is True
        assert "src/main.py" in result.ambiguous_files

    @patch("odin.worktree.subprocess.run")
    def test_resolution_disabled_falls_back_to_abort(self, mock_run, wt):
        """attempt_resolution=False (default) → conflict aborts as before.
        No merge-agent code runs.
        """
        mock_run.side_effect = [
            _ok(),    # rev-parse
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            _ok(stdout=" file.changed\n 1 file"),  # diff --stat
            _fail(stderr="CONFLICT"),  # merge fails
            _ok(stdout="UU opencode.json\n"),  # status
            _ok(),    # merge --abort
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")

        assert result.success is False
        assert result.conflict is True
        assert result.needs_human is False
        assert result.resolved_files == []


# ------------------------------------------------------------------
# Conflict comment formatting (board-side)
# ------------------------------------------------------------------

class TestConflictCommentFormatting:
    """The board comment posted for a merge conflict must name the
    conflicting files and surface a hint when a known generated agent
    config is involved (regression for task #112: bare "Merge conflict:"
    comment left the operator with no UI affordance to debug).
    """

    def _format(self, files):
        """Pin the public contract by calling the real formatter."""
        from odin.worktree import format_merge_conflict_comment
        return format_merge_conflict_comment(
            "task/sp_abc/42", "spec/sp_abc", files,
        )

    def test_comment_lists_conflicting_files(self):
        msg = self._format(["opencode.json", "shared.py"])
        assert "opencode.json" in msg
        assert "shared.py" in msg
        # Header line must remain so the comment still reads as a status
        assert "Merge conflict merging" in msg

    def test_comment_omits_file_list_when_empty(self):
        msg = self._format([])
        assert "Conflicting files" not in msg
        # Hint must not appear when there is nothing to hint about
        assert "F24" not in msg

    def test_comment_surfaces_generated_config_hint(self):
        msg = self._format(["opencode.json"])
        # Single-line hint, easy to grep from the board UI
        assert "F24" in msg
        assert "generated agent config" in msg.lower()

    def test_hint_skipped_for_non_generated_files(self):
        msg = self._format(["shared.py"])
        assert "F24" not in msg

    def test_hint_matches_dotfile_generated_config(self):
        # The dot-prefixed `.mcp.json` is also in HARNESS_GENERATED_PATHS
        assert "F24" in self._format([".mcp.json"])

    def test_hint_matches_directory_generated_config(self):
        # The whole `.claude/` directory is generated
        assert "F24" in self._format([".claude/settings.local.json"])


# ------------------------------------------------------------------
# cleanup_task_worktree
# ------------------------------------------------------------------

class TestCleanupTaskWorktree:
    @patch("odin.worktree.subprocess.run")
    def test_removes_worktree_and_branch(self, mock_run, wt):
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(),    # worktree remove
            _ok(),    # rev-parse: branch exists
            _ok(),    # branch -D
        ]
        wt.cleanup_task_worktree("sp_abc", "42")
        remove_call = mock_run.call_args_list[0]
        assert "remove" in remove_call[0][0]
        assert "--force" in remove_call[0][0]
        delete_call = mock_run.call_args_list[2]
        assert "-D" in delete_call[0][0]

    @patch("odin.worktree.subprocess.run")
    def test_no_op_when_nothing_exists(self, mock_run, wt):
        """No worktree dir, no branch → no remove calls, just branch check."""
        mock_run.return_value = _fail()  # branch doesn't exist
        wt.cleanup_task_worktree("sp_abc", "99")
        # Only the branch existence check
        assert mock_run.call_count == 1


# ------------------------------------------------------------------
# remove_task_worktree (preserves branch)
# ------------------------------------------------------------------

class TestRemoveTaskWorktree:
    @patch("odin.worktree.subprocess.run")
    def test_removes_worktree_preserves_branch(self, mock_run, wt):
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.return_value = _ok()
        wt.remove_task_worktree("sp_abc", "42")
        # Should call worktree remove but NOT branch -D
        assert mock_run.call_count == 1
        call_cmd = mock_run.call_args_list[0][0][0]
        assert "remove" in call_cmd
        assert "-D" not in call_cmd

    @patch("odin.worktree.subprocess.run")
    def test_no_op_when_worktree_doesnt_exist(self, mock_run, wt):
        wt.remove_task_worktree("sp_abc", "99")
        assert mock_run.call_count == 0


# ------------------------------------------------------------------
# finalize_spec
# ------------------------------------------------------------------

class TestFinalizeSpec:
    @patch("odin.worktree.subprocess.run")
    def test_removes_all_task_worktrees(self, mock_run, wt):
        spec_dir = wt.worktree_base / "sp_abc"
        (spec_dir / "42").mkdir(parents=True)
        (spec_dir / "43").mkdir(parents=True)

        mock_run.return_value = _ok()
        wt.finalize_spec("sp_abc")
        # Should call worktree remove for each task dir
        remove_calls = [
            c for c in mock_run.call_args_list
            if "remove" in c[0][0]
        ]
        assert len(remove_calls) == 2

    @patch("odin.worktree.subprocess.run")
    def test_no_op_when_spec_dir_doesnt_exist(self, mock_run, wt):
        wt.finalize_spec("sp_nonexistent")
        assert mock_run.call_count == 0


# ------------------------------------------------------------------
# cleanup_all
# ------------------------------------------------------------------

class TestCleanupAll:
    @patch("odin.worktree.subprocess.run")
    def test_delegates_to_finalize_per_spec(self, mock_run, wt):
        (wt.worktree_base / "sp_a" / "1").mkdir(parents=True)
        (wt.worktree_base / "sp_b" / "2").mkdir(parents=True)

        mock_run.return_value = _ok()
        wt.cleanup_all()
        # Should have remove calls for each task + a prune call
        remove_calls = [c for c in mock_run.call_args_list if "remove" in c[0][0]]
        prune_calls = [c for c in mock_run.call_args_list if "prune" in c[0][0]]
        assert len(remove_calls) == 2
        assert len(prune_calls) == 1

    @patch("odin.worktree.subprocess.run")
    def test_no_op_when_base_doesnt_exist(self, mock_run, wt):
        mock_run.return_value = _ok()
        wt.cleanup_all()
        # Only the prune call (no spec dirs to finalize)
        assert mock_run.call_count == 1
        assert "prune" in mock_run.call_args_list[0][0][0]


# ------------------------------------------------------------------
# create_spec_pr
# ------------------------------------------------------------------

class TestCreateSpecPr:
    @patch("odin.worktree.subprocess.run")
    def test_creates_pr(self, mock_run, wt):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/org/repo/pull/42\n",
        )
        url = wt.create_spec_pr("sp_abc", "My feature", ["Task 1: done"])
        assert url == "https://github.com/org/repo/pull/42"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gh"
        assert "--head" in cmd
        assert "spec/sp_abc" in cmd

    @patch("odin.worktree.subprocess.run")
    def test_pr_failure_returns_none(self, mock_run, wt):
        mock_run.return_value = _fail(stderr="no remote")
        url = wt.create_spec_pr("sp_abc", "My feature")
        assert url is None

    @patch("odin.worktree.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_found(self, mock_run, wt):
        url = wt.create_spec_pr("sp_abc", "My feature")
        assert url is None

    @patch("odin.worktree.subprocess.run")
    def test_pr_body_includes_task_summaries(self, mock_run, wt):
        mock_run.return_value = _ok(stdout="https://github.com/pull/1\n")
        wt.create_spec_pr("sp_abc", "Title", ["summary A", "summary B"])
        body_arg = mock_run.call_args[0][0]
        # Find the --body flag and its value
        body_idx = body_arg.index("--body") + 1
        body = body_arg[body_idx]
        assert "summary A" in body
        assert "summary B" in body


# ------------------------------------------------------------------
# _copy_agent_configs
# ------------------------------------------------------------------

class TestCopyAgentConfigs:
    def test_copies_files_from_project_root(self, wt):
        """Existing config files are copied into the worktree."""
        # Create a source file
        (wt.project_root / ".env").write_text("KEY=val\n")
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        wt._copy_agent_configs(wt_path)
        assert (wt_path / ".env").exists()
        assert (wt_path / ".env").read_text() == "KEY=val\n"

    def test_copies_directories(self, wt):
        """Directory configs (like .claude/) are copied recursively."""
        claude_dir = wt.project_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text('{"key": "val"}')
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        wt._copy_agent_configs(wt_path)
        assert (wt_path / ".claude" / "settings.json").exists()
        assert (wt_path / ".claude" / "settings.json").read_text() == '{"key": "val"}'

    def test_skips_missing_sources(self, wt):
        """No error when source config doesn't exist."""
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        wt._copy_agent_configs(wt_path)  # no sources exist — should not raise

    def test_skips_existing_destinations(self, wt):
        """Doesn't overwrite configs already in the worktree."""
        (wt.project_root / ".env").write_text("original\n")
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".env").write_text("existing\n")

        wt._copy_agent_configs(wt_path)
        assert (wt_path / ".env").read_text() == "existing\n"

    def test_handles_oserror_gracefully(self, wt):
        """OSError during copy is swallowed (best-effort)."""
        (wt.project_root / ".env").write_text("content\n")
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        with patch("odin.worktree.shutil.copy2", side_effect=OSError("disk full")):
            wt._copy_agent_configs(wt_path)  # should not raise

    def test_creates_parent_directories(self, wt):
        """Nested config paths like .odin/config.yaml get parents created."""
        odin_dir = wt.project_root / ".odin"
        odin_dir.mkdir()
        (odin_dir / "config.yaml").write_text("board_id: 5\n")
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        wt._copy_agent_configs(wt_path)
        assert (wt_path / ".odin" / "config.yaml").read_text() == "board_id: 5\n"


# ------------------------------------------------------------------
# _write_worktree_gitignore
# ------------------------------------------------------------------

class TestWriteWorktreeGitignore:
    """Excludes go to git info/exclude — the tracked .gitignore is NEVER
    touched (task 287: a dirty tracked .gitignore aborts any merge whose
    branch also modifies it)."""

    def _run_with_exclude(self, wt_path):
        return patch(
            "odin.worktree.subprocess.run",
            return_value=MagicMock(
                returncode=0, stdout=".git/info/exclude\n", stderr=""
            ),
        )

    def test_writes_all_paths_to_info_exclude(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        with self._run_with_exclude(wt_path):
            wt._write_worktree_gitignore(wt_path)

        content = (wt_path / ".git" / "info" / "exclude").read_text()
        assert _GITIGNORE_MARKER in content
        for p in _WORKTREE_GITIGNORE_PATHS:
            assert f"/{p}" in content
        assert "/.odin" in content
        # The tracked .gitignore must not exist / be created at all.
        assert not (wt_path / ".gitignore").exists()

    def test_idempotent_on_repeat_call(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        with self._run_with_exclude(wt_path):
            wt._write_worktree_gitignore(wt_path)
            first = (wt_path / ".git" / "info" / "exclude").read_text()
            wt._write_worktree_gitignore(wt_path)
            second = (wt_path / ".git" / "info" / "exclude").read_text()

        assert first == second

    def test_existing_tracked_gitignore_left_untouched(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".gitignore").write_text("node_modules/\n")

        with self._run_with_exclude(wt_path):
            wt._write_worktree_gitignore(wt_path)

        assert (wt_path / ".gitignore").read_text() == "node_modules/\n"
        assert _GITIGNORE_MARKER in (
            wt_path / ".git" / "info" / "exclude"
        ).read_text()


# ------------------------------------------------------------------
# _clean_untracked_agent_configs
# ------------------------------------------------------------------

class TestCleanUntrackedAgentConfigs:
    def test_removes_untracked_file(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".env").write_text("SECRET=x\n")

        with patch.object(wt, "_git") as mock_git:
            mock_git.return_value = _fail()  # ls-files: not tracked
            wt._clean_untracked_agent_configs(wt_path)

        assert not (wt_path / ".env").exists()

    def test_preserves_tracked_file(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".env").write_text("SECRET=x\n")

        with patch.object(wt, "_git") as mock_git:
            mock_git.return_value = _ok()  # ls-files: tracked
            wt._clean_untracked_agent_configs(wt_path)

        assert (wt_path / ".env").exists()

    def test_removes_untracked_directory(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".claude").mkdir()
        (wt_path / ".claude" / "settings.json").write_text("{}")

        with patch.object(wt, "_git") as mock_git:
            mock_git.return_value = _fail()  # not tracked
            wt._clean_untracked_agent_configs(wt_path)

        assert not (wt_path / ".claude").exists()

    def test_removes_gitignore_with_marker(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".gitignore").write_text(f"{_GITIGNORE_MARKER}\n/.env\n")

        with patch.object(wt, "_git") as mock_git:
            mock_git.return_value = _fail()  # not tracked
            wt._clean_untracked_agent_configs(wt_path)

        assert not (wt_path / ".gitignore").exists()

    def test_preserves_gitignore_without_marker(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".gitignore").write_text("node_modules/\n")

        with patch.object(wt, "_git") as mock_git:
            mock_git.return_value = _fail()  # not tracked
            wt._clean_untracked_agent_configs(wt_path)

        # .gitignore without our marker should be left alone
        assert (wt_path / ".gitignore").exists()

    def test_skips_nonexistent_paths(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        with patch.object(wt, "_git") as mock_git:
            wt._clean_untracked_agent_configs(wt_path)

        # Should not call git at all — nothing to check
        mock_git.assert_not_called()


# ------------------------------------------------------------------
# _auto_commit_worktree — never-commit reset
# ------------------------------------------------------------------

class TestAutoCommitGitignoreUnstaging:
    @patch("odin.worktree.subprocess.run")
    def test_unstages_gitignore_after_add(self, mock_run, wt):
        """Auto-commit should unstage .gitignore and harness-generated paths."""
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(stdout="?? new.txt\n"),    # status --porcelain: changes
            _ok(),                          # git add -A
            _ok(),                          # git reset -- .gitignore <generated>
            _ok(stdout="new.txt\n"),        # diff --cached --name-only (post-reset)
            _ok(stdout="A\tnew.txt\n"),     # diff --cached --name-status (bulk guard)
            _ok(stdout="new.txt\n"),        # diff --cached --name-only (final check)
            _ok(),                          # git commit
        ]
        result = wt._auto_commit_worktree("sp_abc", "42", "title")
        assert bool(result) is True
        assert result.committed is True
        assert result.skipped == 0

        # Verify the generated-path reset unstaged every never-commit path:
        # .gitignore, all harness-generated paths, all worktree-local paths.
        reset_call = mock_run.call_args_list[2]
        expected_reset = [
            "git", "reset", "--", ".gitignore",
            *HARNESS_GENERATED_PATHS,
            *_WORKTREE_LOCAL_PATHS,
        ]
        assert reset_call[0][0] == expected_reset

    @patch("odin.worktree.subprocess.run")
    def test_bails_when_only_gitignore_changed(self, mock_run, wt):
        """If only .gitignore was staged, bail out after unstaging it."""
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(stdout="?? .gitignore\n"),  # status: only .gitignore
            _ok(),                           # git add -A
            _ok(),                           # git reset -- .gitignore ...
            _ok(stdout=""),                  # diff --cached: nothing left → bail
        ]
        result = wt._auto_commit_worktree("sp_abc", "42")
        assert bool(result) is False
        assert result.committed is False


# ------------------------------------------------------------------
# _auto_commit_worktree — .proof/ pathspec exclude
# ------------------------------------------------------------------

class TestAutoCommitProofPathspec:
    """The git add in auto-commit must use a pathspec exclude for .proof/
    so proof artifacts are never staged — not staged-then-unstaged.

    The reset layer (Layer 1 with .proof in _WORKTREE_LOCAL_PATHS) is
    defence-in-depth; the pathspec is the primary mechanism because it
    never creates a window where .proof/ is in the index.
    """

    @patch("odin.worktree.subprocess.run")
    def test_git_add_excludes_proof_via_pathspec(self, mock_run, wt):
        """The git add command must include ':(exclude).proof'."""
        wt_path = wt.get_worktree_path("sp_pf", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(stdout="?? code.py\n?? .proof/task-42/proof.md\n"),
            _ok(),                          # git add -A . :(exclude).proof
            _ok(),                          # git reset -- .gitignore <paths>
            _ok(stdout="code.py\n"),        # diff --cached --name-only
            _ok(stdout="A\tcode.py\n"),     # diff --cached --name-status
            _ok(stdout="code.py\n"),        # diff --cached --name-only (final)
            _ok(),                          # git commit
        ]
        result = wt._auto_commit_worktree("sp_pf", "42", "title")
        assert result.committed is True

        add_cmd = mock_run.call_args_list[1][0][0]
        assert "add" in add_cmd
        assert any(
            "exclude" in str(a) and ".proof" in str(a)
            for a in add_cmd
        ), f"git add must exclude .proof via pathspec, got: {add_cmd}"


# ------------------------------------------------------------------
# _auto_commit_worktree — pollution blocklist helpers
# ------------------------------------------------------------------

class TestPollutionBlocklistHelpers:
    """Pure-logic tests for the venv/build pollution detection."""

    def test_is_pollution_path_component_markers(self):
        """node_modules / site-packages / __pycache__ match at any depth."""
        for p in [
            "node_modules/foo/index.js",
            "src/node_modules/x",
            ".venv/lib/site-packages/pkg/__init__.py",
            "app/__pycache__/x.pyc",
            "__pycache__/top.pyc",
        ]:
            assert _is_pollution_path(p, frozenset()), f"{p!r} should be pollution"

    def test_is_pollution_path_venv_prefix(self):
        """``.venv*`` directory components match (.venv, .venv311)."""
        assert _is_pollution_path(".venv/bin/python", frozenset())
        assert _is_pollution_path(".venv311/lib/foo.py", frozenset())
        assert _is_pollution_path("sub/.venv/x", frozenset())

    def test_is_pollution_path_venv_exact(self):
        """Plain ``venv/`` component matches."""
        assert _is_pollution_path("venv/bin/python", frozenset())
        assert _is_pollution_path("deploy/venv/lib/x", frozenset())

    def test_is_not_pollution_path_for_real_source(self):
        """Legitimate source paths are not flagged."""
        for p in ["feature.py", "src/app/main.py", "tests/test_x.py", "README.md"]:
            assert not _is_pollution_path(p, frozenset()), f"{p!r} is not pollution"

    def test_find_venv_roots_via_marker(self):
        """pyvenv.cfg marks a venv root — catches non-standard names."""
        staged = [
            "env/pyvenv.cfg",
            "env/bin/python",
            "env/lib/foo.py",
            ".venv/pyvenv.cfg",
            "feature.py",
        ]
        roots = _find_venv_roots(staged)
        assert roots == frozenset({"env", ".venv"})

    def test_venv_root_covers_nonstandard_name(self):
        """A venv named ``env/`` (no .venv prefix) is caught via pyvenv.cfg."""
        roots = _find_venv_roots(["env/pyvenv.cfg", "env/bin/python"])
        assert _is_pollution_path("env/bin/python", roots)
        assert _is_pollution_path("env/lib/site-packages/x", roots)


# ------------------------------------------------------------------
# _auto_commit_worktree — bulk-add guard (mocked subprocess)
# ------------------------------------------------------------------

class TestAutoCommitBulkGuard:
    @patch("odin.worktree.subprocess.run")
    def test_bulk_guard_unstages_excess_new_files(self, mock_run, wt):
        """Over the threshold of new files → unstage them, keep modifications."""
        wt_path = wt.get_worktree_path("sp_bulk", "1")
        wt_path.mkdir(parents=True)

        over = _AUTO_COMMIT_MAX_NEW_FILES + 5
        new_listing = "".join(f"A\tgen/log_{i}.txt\n" for i in range(over))
        mod_listing = "M\tfeature.py\n"

        mock_run.side_effect = [
            _ok(stdout="?? gen/\n M feature.py\n"),  # status
            _ok(),                                    # add -A
            _ok(),                                    # reset generated paths
            _ok(stdout="feature.py\n" + "".join(f"gen/log_{i}.txt\n" for i in range(over))),  # diff --cached --name-only
            _ok(stdout=mod_listing + new_listing),    # diff --cached --name-status
            _ok(),                                    # reset batch of new files
            _ok(stdout="feature.py\n"),               # diff --cached --name-only (final)
            _ok(),                                    # commit
        ]
        result = wt._auto_commit_worktree("sp_bulk", "1", "bulk")
        assert result.committed is True
        assert result.skipped_bulk == over
        assert result.skipped_blocklist == 0

    @patch("odin.worktree.subprocess.run")
    def test_bulk_guard_reports_when_nothing_left(self, mock_run, wt, caplog):
        """Bulk guard fires and no tracked modifications remain → nothing
        committed, but the exclusion is still reported loudly."""
        import logging
        wt_path = wt.get_worktree_path("sp_bulk", "2")
        wt_path.mkdir(parents=True)

        over = _AUTO_COMMIT_MAX_NEW_FILES + 1
        new_listing = "".join(f"A\tg/l_{i}.txt\n" for i in range(over))

        mock_run.side_effect = [
            _ok(stdout="?? g/\n"),                    # status
            _ok(),                                    # add -A
            _ok(),                                    # reset generated paths
            _ok(stdout="".join(f"g/l_{i}.txt\n" for i in range(over))),  # name-only
            _ok(stdout=new_listing),                  # name-status
            _ok(),                                    # reset batch
            _ok(stdout=""),                           # name-only (final: empty)
        ]
        with caplog.at_level(logging.WARNING, logger="odin.worktree"):
            result = wt._auto_commit_worktree("sp_bulk", "2")
        assert result.committed is False
        assert result.skipped_bulk == over
        assert any("excluded" in r.getMessage().lower() for r in caplog.records)

    @patch("odin.worktree.subprocess.run")
    def test_blocklist_excludes_venv_files(self, mock_run, wt):
        """Staged venv files are unstaged by the blocklist layer."""
        wt_path = wt.get_worktree_path("sp_bl", "3")
        wt_path.mkdir(parents=True)
        staged = ".venv/pyvenv.cfg\n.venv/bin/python\nfeature.py\n"

        mock_run.side_effect = [
            _ok(stdout="?? feature.py\n?? .venv/\n"),  # status
            _ok(),                                      # add -A
            _ok(),                                      # reset generated paths
            _ok(stdout=staged),                         # name-only (post reset)
            _ok(),                                      # reset blocklist batch
            _ok(stdout="A\tfeature.py\n"),              # name-status (post blocklist)
            _ok(stdout="feature.py\n"),                 # name-only (final)
            _ok(),                                      # commit
        ]
        result = wt._auto_commit_worktree("sp_bl", "3", "venv")
        assert result.committed is True
        assert result.skipped_blocklist == 2  # pyvenv.cfg + bin/python
        assert result.skipped_bulk == 0


class TestAutoCommitResultContract:
    def test_truthy_iff_committed(self):
        assert not AutoCommitResult()
        assert bool(AutoCommitResult(committed=True))

    def test_summary_empty_when_no_skips(self):
        assert AutoCommitResult(committed=True).summary == ""

    def test_summary_lists_blocklist(self):
        s = AutoCommitResult(skipped_blocklist=42).summary
        assert "42" in s and "blocklist" in s

    def test_summary_lists_bulk(self):
        s = AutoCommitResult(skipped_bulk=500).summary
        assert "500" in s and "bulk" in s


# ------------------------------------------------------------------
# Single source of truth — HARNESS_GENERATED_PATHS coverage
# ------------------------------------------------------------------

class TestNeverCommitCoverage:
    """The gitignore + auto-commit reset + cleanup must cover every path
    the harness/MCP config writers produce."""

    def test_gitignore_patterns_match_mcp_config_map(self):
        """Every MCP_CONFIG_MAP path must appear as a gitignore pattern."""
        from odin.mcps.taskit_mcp.config import MCP_CONFIG_MAP
        for path in set(MCP_CONFIG_MAP.values()):
            assert path in _WORKTREE_GITIGNORE_PATHS, (
                f"MCP config path {path!r} missing from "
                f"_WORKTREE_GITIGNORE_PATHS — adding a new agent to "
                f"MCP_CONFIG_MAP should auto-extend gitignore coverage."
            )

    def test_mcp_json_in_generated_paths(self):
        """Regression: F24 instance 1 — ``.mcp.json`` must be gitignored."""
        assert ".mcp.json" in HARNESS_GENERATED_PATHS
        assert ".mcp.json" in _WORKTREE_GITIGNORE_PATHS

    def test_qwen_not_in_mcp_config_map(self):
        """Regression: qwen was RETIRED in task #102 (user directive).

        Must NOT be re-added to MCP_CONFIG_MAP / MCP_FORMATTERS /
        ``HARNESS_GENERATED_PATHS``.  Re-adding it would silently bring
        back a retired harness — exactly the operator directive this
        test guards against.
        """
        from odin.mcps.taskit_mcp.config import (
            AGENTS_WITH_TOOL_APPROVAL,
            MCP_CONFIG_MAP,
            MCP_FORMATTERS,
        )
        assert "qwen" not in MCP_CONFIG_MAP
        assert "qwen" not in MCP_FORMATTERS
        assert "qwen" not in AGENTS_WITH_TOOL_APPROVAL
        assert ".qwen/settings.json" not in HARNESS_GENERATED_PATHS

    def test_qwen_defensively_gitignored(self):
        """Legacy ``.qwen/`` artifacts (from old worktrees before
        retirement) must still be excluded by the worktree ``.gitignore``
        so they can't sneak into a commit.  This is ignore-only — NOT in
        HARNESS_GENERATED_PATHS (single source of truth for live writers).
        """
        assert ".qwen" in _WORKTREE_GITIGNORE_PATHS

    def test_gitignore_includes_orig_backups(self):
        """Regression: .orig backup files must never be committed."""
        assert "*.orig" in HARNESS_GENERATED_PATHS
        assert "*.orig" in _WORKTREE_GITIGNORE_PATHS

    def test_agent_config_paths_superset_of_generated(self):
        """AGENT_CONFIG_PATHS (copy list) must cover every generated path."""
        for path in HARNESS_GENERATED_PATHS:
            # AGENT_CONFIG_PATHS only handles literal paths; glob patterns
            # like "*.orig" don't need to be in the copy list.
            if "*" in path or "?" in path:
                continue
            assert any(
                p == path or p.endswith(path) or path.startswith(p)
                for p in AGENT_CONFIG_PATHS
            ), f"Generated path {path!r} not copyable from project root"


# ------------------------------------------------------------------
# Django migration leaf-conflict detection
# ------------------------------------------------------------------
#
# Wave 4 hit a recurring class: two parallel task branches each minted a
# `0049_*` migration; the merge landed the two leaves together on the
# spec branch; every sibling branch's backend suite then died on
# "Conflicting migrations detected" until a human hand-added a merge
# migration.  This block tests the layer that catches it on the merge
# path BEFORE the broken state is pushed to the spec branch.
#
# Two surfaces are covered:
#   1. ``_parse_conflict_leaves`` — the regex / parser that extracts the
#      leaf filenames and app from Django's stderr.
#   2. ``_check_django_migration_conflicts`` — the worktree-side helper
#      that locates the Django backend and runs
#      ``python manage.py makemigrations --check --dry-run``.
#   3. End-to-end: ``merge_task_into_spec`` must fail-fast with the
#      named-leaves message when the merge produced two colliding leaves.


class TestParseConflictLeaves:
    """Django's stderr is the only signal that says *which* two leaves
    collided — `makemigrations --check` alone returns non-zero without
    telling you why.  We parse the parenthetical
    ``(0049_a, 0049_b in tasks)`` and extract the leaf filenames plus
    the owning app so the operator-facing message can name both.

    Failure modes that have already bitten real agents:
      * Two leaves only — the common wave-4 case.
      * Three or more leaves — possible when three task branches fork
        from the same migration prefix on the same wave.
      * Leaves with trailing ``in <app>`` and we must NOT include the
        app token in the leaf list.
    """

    def test_two_leaves(self):
        from odin.worktree import _parse_conflict_leaves
        stderr = (
            "CommandError: Conflicting migrations detected; multiple leaf "
            "nodes in the migration graph: (0049_a, 0049_b in tasks).\n"
            "To fix them run 'python manage.py makemigrations --merge'\n"
        )
        result = _parse_conflict_leaves(stderr)
        assert result.app == "tasks"
        assert result.leaves == ["0049_a", "0049_b"]

    def test_three_leaves(self):
        from odin.worktree import _parse_conflict_leaves
        stderr = (
            "CommandError: Conflicting migrations detected; multiple leaf "
            "nodes in the migration graph: (0049_x, 0049_y, 0049_z in tasks).\n"
        )
        result = _parse_conflict_leaves(stderr)
        assert result.app == "tasks"
        assert result.leaves == ["0049_x", "0049_y", "0049_z"]

    def test_no_conflict_message_returns_empty(self):
        """A non-zero exit with no ``Conflicting migrations detected``
        line is a DIFFERENT failure (e.g. model field drift).  We must
        not mistake it for a leaf collision — the operator fix is
        completely different (add a migration, not a merge one)."""
        from odin.worktree import _parse_conflict_leaves
        stderr = (
            "CommandError: Field 'metadata' on Board has changed; "
            "please add a migration.\n"
        )
        result = _parse_conflict_leaves(stderr)
        assert result.app is None
        assert result.leaves == []

    def test_empty_input(self):
        from odin.worktree import _parse_conflict_leaves
        assert _parse_conflict_leaves("").leaves == []
        assert _parse_conflict_leaves("").app is None

    def test_app_extracted_separately_from_leaves(self):
        """Regression: the app name must not leak into the leaf list
        (an earlier prototype split on raw comma and shipped '0049_b in
        tasks' as a leaf — operator copy-paste then broke Django)."""
        from odin.worktree import _parse_conflict_leaves
        stderr = (
            "Conflicting migrations detected; multiple leaf nodes in the "
            "migration graph: (0049_a, 0049_b in tasks).\n"
        )
        result = _parse_conflict_leaves(stderr)
        assert all("in" not in leaf for leaf in result.leaves)
        assert "tasks" not in result.leaves
        assert result.app == "tasks"


class TestCheckDjangoMigrationConflicts:
    """``_check_django_migration_conflicts`` locates the Django backend
    inside the worktree and shells out to ``makemigrations --check
    --dry-run``.  Returns a structured result so callers can name the
    leaves in the operator-facing message.

    Behaviours tested:
      * No Django backend on disk (worktree without ``taskit-backend``)
        → skip the check, return no-conflict (defensive).
      * Django reports a conflict → leaves populated, has_conflict=True.
      * Django clean → has_conflict=False, leaves=[].
      * Django reports a NON-conflict error (e.g. model drift) →
        has_conflict=False — never mistake drift for a leaf collision.
    """

    def test_no_backend_dir_skips_check(self, tmp_path):
        """If the worktree has no ``taskit/taskit-backend/manage.py``,
        there is nothing to check — return clean."""
        from odin.worktree import _check_django_migration_conflicts
        result = _check_django_migration_conflicts(tmp_path)
        assert result.has_conflict is False
        assert result.leaves == []

    def test_clean_migrations_no_conflict(self, tmp_path, monkeypatch):
        """Django exits 0 → no conflict."""
        from odin import worktree as wt_mod

        backend = tmp_path / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        fake = MagicMock(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(wt_mod.subprocess, "run", lambda *a, **k: fake)

        result = wt_mod._check_django_migration_conflicts(tmp_path)
        assert result.has_conflict is False
        assert result.leaves == []

    def test_conflict_detected_and_leaves_parsed(self, tmp_path, monkeypatch):
        """Django exits 1 with ``Conflicting migrations detected`` and
        two named leaves → has_conflict=True with leaves populated."""
        from odin import worktree as wt_mod

        backend = tmp_path / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        stderr = (
            "CommandError: Conflicting migrations detected; multiple leaf "
            "nodes in the migration graph: (0049_a, 0049_b in tasks).\n"
            "To fix them run 'python manage.py makemigrations --merge'\n"
        )
        fake = MagicMock(returncode=1, stdout="", stderr=stderr)
        monkeypatch.setattr(wt_mod.subprocess, "run", lambda *a, **k: fake)

        result = wt_mod._check_django_migration_conflicts(tmp_path)
        assert result.has_conflict is True
        assert result.leaves == ["0049_a", "0049_b"]
        assert result.app == "tasks"

    def test_non_conflict_django_error_is_not_mistaken(self, tmp_path, monkeypatch):
        """Django exits 1 for a NON-conflict reason (model drift,
        ImportError, etc.).  We must NOT treat that as a leaf collision
        — the operator fix is different (add a migration, not merge).
        Returning has_conflict=False leaves the merge to proceed so the
        test suite / CI surfaces the real error downstream."""
        from odin import worktree as wt_mod

        backend = tmp_path / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        stderr = "CommandError: Field 'metadata' has changed.\n"
        fake = MagicMock(returncode=1, stdout="", stderr=stderr)
        monkeypatch.setattr(wt_mod.subprocess, "run", lambda *a, **k: fake)

        result = wt_mod._check_django_migration_conflicts(tmp_path)
        assert result.has_conflict is False
        assert result.leaves == []

    def test_subprocess_exception_is_treated_as_no_conflict(self, tmp_path, monkeypatch, caplog):
        """If the check itself blows up (e.g. python3 missing,
        manage.py crashes with ImportError), we conservatively treat as
        no conflict — surfacing the Django stderr would be a false
        positive that breaks every merge.  The exception is logged so
        the operator can see why the check did not run, but never
        blocks the merge."""
        from odin import worktree as wt_mod
        import logging

        backend = tmp_path / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        def boom(*a, **k):
            raise OSError("python3 not found")

        with caplog.at_level(logging.WARNING, logger="odin.worktree"):
            monkeypatch.setattr(wt_mod.subprocess, "run", boom)
            result = wt_mod._check_django_migration_conflicts(tmp_path)
        assert result.has_conflict is False
        assert result.leaves == []
        # The exception must be logged so the operator can see why the
        # check did not run; otherwise silent skips hide real bugs.
        assert any(
            "failed to run" in r.getMessage().lower() for r in caplog.records
        )

    def test_invokes_django_with_check_and_dry_run(self, tmp_path, monkeypatch):
        """The exact command must be
        ``python3 manage.py makemigrations --check --dry-run`` from the
        backend dir, with timeout — so a hung Django cannot wedge the
        merge path."""
        from odin import worktree as wt_mod

        backend = tmp_path / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            captured["timeout"] = kwargs.get("timeout")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(wt_mod.subprocess, "run", fake_run)
        wt_mod._check_django_migration_conflicts(tmp_path)
        assert captured["cmd"][:3] == ["python3", "manage.py", "makemigrations"]
        assert "--check" in captured["cmd"]
        assert "--dry-run" in captured["cmd"]
        assert Path(captured["cwd"]) == backend
        assert captured["timeout"] is not None


class TestMergeRejectsMigrationConflicts:
    """End-to-end: ``merge_task_into_spec`` must fail-fast with a
    named-leaves message when the merge produced two colliding leaves.

    The flow under test:
      1. ``git merge --no-ff`` succeeds (git-level merge is clean — both
         leaves sit side-by-side in the tree, no content overlap).
      2. The new migration-conflict check runs and reports conflicting
         leaves.
      3. ``git reset --hard HEAD~1`` undoes the local merge commit so
         the broken state never reaches the spec branch.
      4. The merge result surfaces the leaves in ``conflicting_files``
         and a human-readable message in ``error`` that names the fix
         (renumber or commit a merge migration).

    The clean path is also covered: if the Django check passes, the
    merge still pushes normally.  This is the regression check against
    the check turning every green merge into a red one.
    """

    def _merge_wt_path(self, wt):
        return wt.worktree_base / "_merge" / "spec_sp_abc"

    @patch("odin.worktree.subprocess.run")
    def test_clean_merge_passes_through_check(self, mock_run, wt, tmp_path):
        """Healthy merge: git succeeds, makemigrations check passes,
        push happens.  No false-positive HOLD."""
        # Build the merge worktree on disk so the check helper finds it
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)
        # Populate a fake Django backend under the merge worktree so the
        # helper actually has something to run.  We override the
        # subprocess call inside the helper to return exit 0.
        backend = merge_wt / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        ok_zero = MagicMock(returncode=0, stdout="", stderr="")
        ok_with_diff = MagicMock(
            returncode=0,
            stdout=" file.py | 5 ++\n 1 file changed",
            stderr="",
        )
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove --force stale merge_wt (mkdir above)
            _ok(),    # worktree add merge_wt
            _ok(),    # fetch origin spec
            _ok(),    # pull origin spec
            ok_with_diff,  # diff --stat (non-empty)
            _ok(),    # merge --no-ff
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof found)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            ok_zero,  # django makemigrations --check --dry-run (clean)
            _ok(),    # push origin spec
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42", "Fix bug")
        assert result.success is True
        assert result.conflict is False
        # The Django check call happened between merge and push
        cmd_list = [c[0][0] for c in mock_run.call_args_list]
        assert any(
            len(cmd) >= 3 and cmd[1] == "manage.py" and cmd[2] == "makemigrations"
            for cmd in cmd_list
        ), "Django makemigrations check should run between merge and push"

    @patch("odin.worktree.subprocess.run")
    def test_auto_merge_failure_parks_for_human(self, mock_run, wt, tmp_path):
        """When the auto ``makemigrations --merge`` fix itself fails
        (Django could not produce a merge migration — e.g. genuinely
        incompatible operations across the two leaves), the gate falls
        back to parking for a human: the local merge commit is reset so
        the broken state never reaches the spec branch, and the result
        names the leaves so the operator knows exactly what to resolve.
        The reply-resume listener only fires on ``needs_human``, so the
        flag must stay set on this fallback path."""
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)
        backend = merge_wt / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        stderr = (
            "CommandError: Conflicting migrations detected; multiple leaf "
            "nodes in the migration graph: (0049_a, 0049_b in tasks).\n"
            "To fix them run 'python manage.py makemigrations --merge'\n"
        )
        django_conflict = MagicMock(returncode=1, stdout="", stderr=stderr)
        merge_failed = MagicMock(
            returncode=1, stdout="", stderr="could not merge branches",
        )
        ok_with_diff = MagicMock(
            returncode=0,
            stdout=" 0049_a.py | 5 ++\n 0049_b.py | 5 ++\n 2 files changed",
            stderr="",
        )
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove --force stale merge_wt
            _ok(),    # worktree add merge_wt
            _ok(),    # fetch origin spec
            _ok(),    # pull origin spec
            ok_with_diff,  # diff --stat (non-empty)
            _ok(),    # merge --no-ff (clean git merge)
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof found)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            django_conflict,  # django makemigrations --check --dry-run (CONFLICT)
            merge_failed,  # makemigrations --merge (AUTO FIX FAILS)
            _ok(),    # git reset --hard HEAD~1 (undo local merge commit)
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42", "Fix bug")
        assert result.success is False
        assert result.conflict is True
        # Auto-fix failed → park for human (a reply can re-attempt the fix).
        assert result.needs_human is True
        assert result.migration_conflict is True
        # The gate MUST have attempted the auto merge-migration fix first
        # (and it failed) — not parked without trying.
        cmd_list = [c[0][0] for c in mock_run.call_args_list]
        assert any(
            len(cmd) >= 3 and cmd[1] == "manage.py"
            and cmd[2] == "makemigrations" and "--merge" in cmd
            for cmd in cmd_list
        ), "Gate must attempt makemigrations --merge before parking for a human"
        # Both leaves named in conflicting_files for downstream UI
        assert set(result.conflicting_files) == {"0049_a", "0049_b"}
        # Error message names both leaves AND the fix
        assert "0049_a" in result.error
        assert "0049_b" in result.error
        # Tells the operator / agent how to recover
        msg = result.error.lower()
        assert "merge migration" in msg or "renumber" in msg, (
            "Error must tell the operator how to fix it (renumber or "
            "commit a merge migration)."
        )
        # Push must NOT have been called
        cmd_strs = [" ".join(str(a) for a in c[0][0]) for c in mock_run.call_args_list]
        assert not any("push" in s and "origin" in s for s in cmd_strs), (
            "Push must be skipped when the merge-migration fix fails — "
            "broken state must never reach the spec branch."
        )

    @patch("odin.worktree.subprocess.run")
    def test_no_backend_skips_check_and_pushes(self, mock_run, wt):
        """Worktree without a Django backend → skip the check, push
        normally.  This is the case for non-TaskIt projects (the merge
        layer is generic)."""
        # No Django backend directory at all.
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(),    # rev-parse
            _ok(),    # worktree remove --force stale merge_wt
            _ok(),    # worktree add
            _ok(),    # fetch
            _ok(),    # pull
            MagicMock(returncode=0, stdout=" file.py | 5 ++\n 1 file changed", stderr=""),  # diff --stat
            _ok(),    # merge --no-ff
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof found)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            _ok(),    # push origin spec
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42")
        assert result.success is True
        # No Django subprocess call should have been made
        cmd_list = [c[0][0] for c in mock_run.call_args_list]
        assert not any(
            len(cmd) >= 2 and cmd[1] == "manage.py" for cmd in cmd_list
        ), "Django check should be skipped when no backend is present"

    @patch("odin.worktree.subprocess.run")
    def test_collision_auto_resolves_via_merge_migration(
        self, mock_run, wt, tmp_path,
    ):
        """Wave-4/6/7/8 recurring class: two parallel task branches each
        mint a migration, leaving two leaf nodes in the graph.  The merge
        gate must auto-run ``makemigrations --merge`` — Django creates a
        NEW merge migration deterministically (it never edits or renames
        existing files), folds it into the merge commit, and the merge
        completes WITHOUT a human in the loop.

        Reserving distinct numeric prefixes at dispatch does NOT prevent
        this (two parallel migrations always produce two leaves regardless
        of prefix number — verified empirically against Django 5.1), so the
        auto merge-migration is the single correct mechanism."""
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)
        backend = merge_wt / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        conflict_stderr = (
            "CommandError: Conflicting migrations detected; multiple leaf "
            "nodes in the migration graph: (0049_a, 0049_b in tasks).\n"
            "To fix them run 'python manage.py makemigrations --merge'\n"
        )
        ok_with_diff = MagicMock(
            returncode=0,
            stdout=" 0049_a.py | 5 ++\n 0049_b.py | 5 ++\n 2 files changed",
            stderr="",
        )
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove --force stale merge_wt
            _ok(),    # worktree add merge_wt
            _ok(),    # fetch origin spec
            _ok(),    # pull origin spec
            ok_with_diff,  # diff --stat (non-empty)
            _ok(),    # merge --no-ff (clean git merge)
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            MagicMock(returncode=1, stdout="", stderr=conflict_stderr),  # makemigrations --check (CONFLICT)
            _ok(stdout="Created merge migration"),  # makemigrations --merge (AUTO FIX — no human)
            MagicMock(  # git status --porcelain (new merge migration untracked)
                returncode=0,
                stdout=(
                    "?? taskit/taskit-backend/tasks/migrations/"
                    "0050_merge_0049_a_0049_b.py\n"
                ),
                stderr="",
            ),
            _ok(),    # git add <migration path>
            _ok(),    # git commit --amend --no-edit (fold migration in)
            _ok(stdout=""),  # makemigrations --check (CLEAN recheck)
            _ok(),    # push origin spec
            _ok(),    # finally: worktree remove
        ]
        # NOTE: no resolution_guidance — the fix runs unconditionally now.
        result = wt.merge_task_into_spec("sp_abc", "42", "Fix bug")
        # Merge completed via the auto merge-migration fix — no human asked.
        assert result.success is True
        assert result.conflict is False
        assert result.needs_human is False
        assert result.resolution_rationale is not None
        assert "merge migration" in result.resolution_rationale.lower()

        # The makemigrations --merge command ran automatically (no human reply).
        cmd_list = [c[0][0] for c in mock_run.call_args_list]
        assert any(
            len(cmd) >= 3 and cmd[1] == "manage.py"
            and cmd[2] == "makemigrations" and "--merge" in cmd
            for cmd in cmd_list
        ), "Collision must auto-run makemigrations --merge without human guidance"

        # The merge was pushed (the completed state reached the spec branch).
        assert any(
            "push" in " ".join(str(a) for a in c[0][0]) for c in mock_run.call_args_list
        ), "Completed merge must be pushed to the spec branch"

    @patch("odin.worktree.subprocess.run")
    def test_check_timeout_does_not_park_as_needs_human(self, mock_run, wt):
        """Retryable failure class (W5.11 invariant): if the migration
        check itself blows up (timeout / python missing), the merge must
        NOT park with ``needs_human`` — the check degrades to no-conflict
        and the merge proceeds.  Retry is the answer for transient infra
        failures, not a human; ``needs_human`` stays reserved for
        human-answerable failures."""
        merge_wt = self._merge_wt_path(wt)
        merge_wt.mkdir(parents=True)
        backend = merge_wt / "taskit" / "taskit-backend"
        backend.mkdir(parents=True)
        (backend / "manage.py").touch()

        ok_with_diff = MagicMock(
            returncode=0,
            stdout=" file.py | 5 ++\n 1 file changed",
            stderr="",
        )
        mock_run.side_effect = [
            _ok(),    # rev-parse: task branch exists
            _ok(),    # worktree remove --force stale merge_wt
            _ok(),    # worktree add merge_wt
            _ok(),    # fetch origin spec
            _ok(),    # pull origin spec
            ok_with_diff,  # diff --stat (non-empty)
            _ok(),    # merge --no-ff (clean git merge)
            _ok(stdout=""),  # strip: git rm --cached .proof (no proof)
            _ok(stdout=""),  # post-merge gate (task 338): no files changed
            subprocess.TimeoutExpired("makemigrations", 30),  # check blows up
            _ok(),    # push origin spec (merge proceeds — not blocked)
            _ok(),    # finally: worktree remove
        ]
        result = wt.merge_task_into_spec("sp_abc", "42", "Fix bug")
        # The check failure is NOT human-answerable — merge proceeds.
        assert result.success is True
        assert result.needs_human is False
        assert result.migration_conflict is False


class TestUnmergedStatusCoverage:
    def test_modify_delete_and_rename_conflicts_are_detected(self):
        """Task 284: UD/DU (modify-vs-delete) fell outside the conflict
        prefixes, so the merge aborted with an empty error and no
        needs_human question. Every git unmerged state must be detected."""
        from odin.worktree import _extract_conflicting_files

        lines = [
            "UU both/modified.py",
            "UD deleted/by/them.py",
            "DU deleted/by/us.py",
            "AA both/added.py",
            "AU rename/family.py",
            "?? untracked.txt",
            " M plain_modified.py",
        ]
        files = _extract_conflicting_files(lines)
        assert "deleted/by/them.py" in files
        assert "deleted/by/us.py" in files
        assert "rename/family.py" in files
        assert "untracked.txt" not in files
        assert "plain_modified.py" not in files
