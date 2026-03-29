"""Unit tests for WorktreeManager — mocked subprocess, no real git."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import subprocess

import pytest

from odin.worktree import WorktreeManager, MergeResult, AGENT_CONFIG_PATHS, _GITIGNORE_MARKER, _WORKTREE_GITIGNORE_PATHS


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
            _fail(stderr="fatal: branch already exists"),  # branch create fails
        ]
        with pytest.raises(RuntimeError, match="Failed to create spec branch"):
            wt.create_spec_branch("sp_fail")


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
            _ok(),    # hook subprocess.run
        ]
        wt_path = wt.worktree_base / "sp_abc" / "42"
        wt.create_task_worktree("sp_abc", "42", post_hooks=["npm install"])
        # The hook is called via subprocess.run directly (not _git),
        # so it's the 5th call to subprocess.run
        hook_call = mock_run.call_args_list[4]
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
        assert msg == "Merge task 42"

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
    def test_creates_gitignore_with_all_paths(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        wt._write_worktree_gitignore(wt_path)
        content = (wt_path / ".gitignore").read_text()

        assert _GITIGNORE_MARKER in content
        for p in _WORKTREE_GITIGNORE_PATHS:
            assert f"/{p}" in content
        # /.odin covers all runtime artifacts (logs, costs, locks, config)
        assert "/.odin" in content
        assert "/.gitignore" in content

    def test_idempotent_on_repeat_call(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()

        wt._write_worktree_gitignore(wt_path)
        first = (wt_path / ".gitignore").read_text()

        wt._write_worktree_gitignore(wt_path)
        second = (wt_path / ".gitignore").read_text()

        assert first == second

    def test_appends_to_existing_content(self, wt):
        wt_path = wt.project_root / "wt"
        wt_path.mkdir()
        (wt_path / ".gitignore").write_text("node_modules/\n")

        wt._write_worktree_gitignore(wt_path)
        content = (wt_path / ".gitignore").read_text()

        assert content.startswith("node_modules/\n")
        assert _GITIGNORE_MARKER in content


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
# _auto_commit_worktree — .gitignore unstaging
# ------------------------------------------------------------------

class TestAutoCommitGitignoreUnstaging:
    @patch("odin.worktree.subprocess.run")
    def test_unstages_gitignore_after_add(self, mock_run, wt):
        """Auto-commit should unstage .gitignore to prevent committing it."""
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(stdout="?? new.txt\n"),    # status --porcelain: changes
            _ok(),                          # git add -A
            _ok(),                          # git reset -- .gitignore
            _ok(stdout="new.txt\n"),        # diff --cached --name-only
            _ok(),                          # git commit
        ]
        result = wt._auto_commit_worktree("sp_abc", "42", "title")
        assert result is True

        # Verify reset -- .gitignore .odin/ was called
        reset_call = mock_run.call_args_list[2]
        assert reset_call[0][0] == ["git", "reset", "--", ".gitignore", ".odin/"]

    @patch("odin.worktree.subprocess.run")
    def test_bails_when_only_gitignore_changed(self, mock_run, wt):
        """If only .gitignore was staged, bail out after unstaging it."""
        wt_path = wt.get_worktree_path("sp_abc", "42")
        wt_path.mkdir(parents=True)

        mock_run.side_effect = [
            _ok(stdout="?? .gitignore\n"),  # status: only .gitignore
            _ok(),                           # git add -A
            _ok(),                           # git reset -- .gitignore
            _ok(stdout=""),                  # diff --cached: nothing left
        ]
        result = wt._auto_commit_worktree("sp_abc", "42")
        assert result is False
