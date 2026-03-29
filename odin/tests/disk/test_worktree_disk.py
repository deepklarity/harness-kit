"""Disk tests for WorktreeManager — real git repos in tmp directories.

These tests create actual git repositories and exercise real git operations.
No mocks — verifies end-to-end worktree lifecycle.
"""

import subprocess
import threading
from pathlib import Path

import pytest

from odin.worktree import WorktreeManager, AGENT_CONFIG_PATHS, _GITIGNORE_MARKER


def _run(args, cwd, **kwargs):
    """Run a git command, check=True by default."""
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, **kwargs)


def _init_repo(path: Path) -> None:
    """Initialize a bare-bones git repo with one commit on main."""
    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "config", "user.email", "test@test.com"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# Test repo\n")
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "-m", "initial"], cwd=path)


def _commit_file(path: Path, filename: str, content: str, msg: str) -> str:
    """Write a file, add, commit, return the commit SHA."""
    (path / filename).write_text(content)
    _run(["git", "add", filename], cwd=path)
    _run(["git", "commit", "-m", msg], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A fresh git repo with one commit on main."""
    _init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def wt(repo):
    return WorktreeManager(repo, worktree_dir=".odin/worktrees")


# ------------------------------------------------------------------
# Spec branch
# ------------------------------------------------------------------

class TestSpecBranch:
    def test_create_spec_branch(self, wt, repo):
        branch = wt.create_spec_branch("sp_test", base_branch="main")
        assert branch == "spec/sp_test"
        result = _run(["git", "branch", "--list", "spec/sp_test"], cwd=repo)
        assert "spec/sp_test" in result.stdout

    def test_spec_branch_idempotent(self, wt):
        wt.create_spec_branch("sp_idem")
        branch = wt.create_spec_branch("sp_idem")
        assert branch == "spec/sp_idem"

    def test_spec_branch_forks_from_main(self, wt, repo):
        main_sha = _run(["git", "rev-parse", "main"], cwd=repo).stdout.strip()
        wt.create_spec_branch("sp_fork")
        spec_sha = _run(["git", "rev-parse", "spec/sp_fork"], cwd=repo).stdout.strip()
        assert main_sha == spec_sha


# ------------------------------------------------------------------
# Task worktree
# ------------------------------------------------------------------

class TestTaskWorktree:
    def test_create_task_worktree(self, wt, repo):
        wt.create_spec_branch("sp_wt")
        path = wt.create_task_worktree("sp_wt", "101")

        assert path.exists()
        assert (path / "README.md").exists()
        result = _run(["git", "branch", "--show-current"], cwd=path)
        assert result.stdout.strip() == "task/sp_wt/101"

    def test_worktree_idempotent(self, wt):
        wt.create_spec_branch("sp_wt2")
        path1 = wt.create_task_worktree("sp_wt2", "201")
        path2 = wt.create_task_worktree("sp_wt2", "201")
        assert path1 == path2

    def test_parallel_worktrees_independent(self, wt, repo):
        """Two task worktrees from the same spec can coexist."""
        wt.create_spec_branch("sp_par")
        path_a = wt.create_task_worktree("sp_par", "301")
        path_b = wt.create_task_worktree("sp_par", "302")

        assert path_a != path_b
        assert path_a.exists()
        assert path_b.exists()

        _commit_file(path_a, "a.txt", "from task A", "add a.txt")
        _commit_file(path_b, "b.txt", "from task B", "add b.txt")

        for tid in ["301", "302"]:
            result = _run(["git", "branch", "--list", f"task/sp_par/{tid}"], cwd=repo)
            assert f"task/sp_par/{tid}" in result.stdout

    def test_symlinks_created(self, wt, repo):
        # Use a path NOT in AGENT_CONFIG_PATHS (those get copied, not symlinked)
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "marker.txt").write_text("pkg\n")
        wt.create_spec_branch("sp_sym")
        path = wt.create_task_worktree("sp_sym", "401", symlinks=["node_modules"])
        assert (path / "node_modules").is_symlink()
        assert (path / "node_modules" / "marker.txt").read_text() == "pkg\n"

    def test_post_hooks_run_in_worktree(self, wt, repo):
        """Post-hooks execute with cwd set to the worktree."""
        wt.create_spec_branch("sp_hook")
        path = wt.create_task_worktree(
            "sp_hook", "410",
            post_hooks=["touch hook_marker.txt"],
        )
        assert (path / "hook_marker.txt").exists()

    def test_existing_task_branch_reused(self, wt, repo):
        """If task branch exists from a previous run, worktree reattaches to it."""
        wt.create_spec_branch("sp_reuse")
        path1 = wt.create_task_worktree("sp_reuse", "420")
        _commit_file(path1, "work.txt", "important work", "commit work")

        # Remove worktree but keep the branch
        wt.remove_task_worktree("sp_reuse", "420")
        assert not path1.exists()

        # Re-create worktree — should reattach to existing branch with its commit
        path2 = wt.create_task_worktree("sp_reuse", "420")
        assert (path2 / "work.txt").exists()
        assert (path2 / "work.txt").read_text() == "important work"


# ------------------------------------------------------------------
# Merge
# ------------------------------------------------------------------

class TestMerge:
    def test_merge_task_into_spec(self, wt, repo):
        wt.create_spec_branch("sp_merge")
        path = wt.create_task_worktree("sp_merge", "501")

        _commit_file(path, "feature.py", "def hello(): pass\n", "add feature")

        result = wt.merge_task_into_spec("sp_merge", "501", "Add feature")
        assert result.success is True
        assert result.conflict is False

        show = _run(["git", "show", "spec/sp_merge:feature.py"], cwd=repo)
        assert "def hello()" in show.stdout

    def test_merge_conflict_detected(self, wt, repo):
        wt.create_spec_branch("sp_conflict")
        path_a = wt.create_task_worktree("sp_conflict", "601")
        path_b = wt.create_task_worktree("sp_conflict", "602")

        _commit_file(path_a, "shared.txt", "version_a", "task 601 changes")
        _commit_file(path_b, "shared.txt", "version_b", "task 602 changes")

        r1 = wt.merge_task_into_spec("sp_conflict", "601", "Task A")
        assert r1.success is True

        r2 = wt.merge_task_into_spec("sp_conflict", "602", "Task B")
        assert r2.success is False
        assert r2.conflict is True

    def test_downstream_gets_upstream_work(self, wt, repo):
        """A task worktree created AFTER an upstream merge includes that work."""
        wt.create_spec_branch("sp_chain")

        path_t1 = wt.create_task_worktree("sp_chain", "701")
        _commit_file(path_t1, "upstream.py", "upstream = True\n", "upstream work")

        r = wt.merge_task_into_spec("sp_chain", "701")
        assert r.success

        path_t2 = wt.create_task_worktree("sp_chain", "702")
        assert (path_t2 / "upstream.py").exists()
        assert (path_t2 / "upstream.py").read_text() == "upstream = True\n"

    def test_empty_branch_merge(self, wt, repo):
        """THE critical test: merge a task branch with zero commits.

        This is the 'branch is empty' bug scenario. A worktree is created
        but the agent produces no commits. What happens when we merge?
        """
        wt.create_spec_branch("sp_empty")
        path = wt.create_task_worktree("sp_empty", "510")

        # Do NOT commit anything — this is the bug scenario
        assert path.exists()

        result = wt.merge_task_into_spec("sp_empty", "510")
        # The merge of an identical branch should either:
        # - succeed as a no-op (no commits to merge), or
        # - fail with a clear error message
        # It should NOT produce a corrupt/empty merge commit
        if result.success:
            # Verify spec branch is unchanged (no garbage merge commit)
            spec_log = _run(
                ["git", "log", "--oneline", "spec/sp_empty"],
                cwd=repo,
            )
            # Should only have the initial commit (no merge commit for nothing)
            assert "Merge task" not in spec_log.stdout
        else:
            # If it fails, it should have a clear error
            assert result.error is not None

    def test_noop_merge_identical_branches(self, wt, repo):
        """Task branch identical to spec (same commit) → merge result."""
        wt.create_spec_branch("sp_noop")
        _path = wt.create_task_worktree("sp_noop", "520")

        # Don't modify anything — task branch == spec branch
        result = wt.merge_task_into_spec("sp_noop", "520")
        # Same as empty branch: should be no-op
        if result.success:
            spec_log = _run(
                ["git", "log", "--oneline", "spec/sp_noop"],
                cwd=repo,
            )
            assert "Merge task" not in spec_log.stdout

    def test_multiple_sequential_nonconflicting_merges(self, wt, repo):
        """Tasks A and B modify different files → both merge cleanly."""
        wt.create_spec_branch("sp_seq")
        path_a = wt.create_task_worktree("sp_seq", "530")
        path_b = wt.create_task_worktree("sp_seq", "531")

        _commit_file(path_a, "file_a.py", "a_content", "add file_a")
        _commit_file(path_b, "file_b.py", "b_content", "add file_b")

        r1 = wt.merge_task_into_spec("sp_seq", "530", "Task A")
        assert r1.success is True

        r2 = wt.merge_task_into_spec("sp_seq", "531", "Task B")
        assert r2.success is True

        # Spec branch has both files
        show_a = _run(["git", "show", "spec/sp_seq:file_a.py"], cwd=repo)
        assert "a_content" in show_a.stdout
        show_b = _run(["git", "show", "spec/sp_seq:file_b.py"], cwd=repo)
        assert "b_content" in show_b.stdout

    def test_merge_worktree_cleanup_on_conflict(self, wt, repo):
        """After a merge conflict, the temp merge worktree is cleaned up."""
        wt.create_spec_branch("sp_mclean")
        path_a = wt.create_task_worktree("sp_mclean", "540")
        path_b = wt.create_task_worktree("sp_mclean", "541")

        _commit_file(path_a, "conflict.txt", "version_a", "task a")
        _commit_file(path_b, "conflict.txt", "version_b", "task b")

        wt.merge_task_into_spec("sp_mclean", "540")
        r2 = wt.merge_task_into_spec("sp_mclean", "541")
        assert r2.success is False
        assert r2.conflict is True

        # Temp merge worktree should be cleaned up
        merge_wt = wt.worktree_base / "_merge" / "spec_sp_mclean"
        assert not merge_wt.exists()

    def test_nonexistent_task_branch(self, wt, repo):
        """Merge for a task that was never created returns error."""
        wt.create_spec_branch("sp_noexist")
        result = wt.merge_task_into_spec("sp_noexist", "999")
        assert result.success is False
        assert "does not exist" in result.error

    def test_merge_creates_no_ff_commit(self, wt, repo):
        """Merge uses --no-ff so the merge commit is always visible."""
        wt.create_spec_branch("sp_noff")
        path = wt.create_task_worktree("sp_noff", "550")
        _commit_file(path, "noff.txt", "content", "add file")

        wt.merge_task_into_spec("sp_noff", "550", "My task title")

        log = _run(["git", "log", "--oneline", "spec/sp_noff"], cwd=repo)
        assert "Merge task 550: My task title" in log.stdout


# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_task_worktree(self, wt, repo):
        wt.create_spec_branch("sp_clean")
        path = wt.create_task_worktree("sp_clean", "801")
        assert path.exists()

        wt.cleanup_task_worktree("sp_clean", "801")
        assert not path.exists()

        result = _run(["git", "branch", "--list", "task/sp_clean/801"], cwd=repo)
        assert "task/sp_clean/801" not in result.stdout

    def test_remove_task_worktree_preserves_branch(self, wt, repo):
        """remove_task_worktree removes the worktree but keeps the branch."""
        wt.create_spec_branch("sp_preserve")
        path = wt.create_task_worktree("sp_preserve", "810")
        _commit_file(path, "work.txt", "valuable work", "save work")

        wt.remove_task_worktree("sp_preserve", "810")

        # Worktree directory should be gone
        assert not path.exists()
        # But the branch with its commits should survive
        result = _run(["git", "branch", "--list", "task/sp_preserve/810"], cwd=repo)
        assert "task/sp_preserve/810" in result.stdout
        # And the commit should be on the branch
        show = _run(
            ["git", "show", "task/sp_preserve/810:work.txt"],
            cwd=repo,
        )
        assert "valuable work" in show.stdout

    def test_cleanup_dirty_worktree(self, wt, repo):
        """Cleanup works even with uncommitted changes (--force)."""
        wt.create_spec_branch("sp_dirty")
        path = wt.create_task_worktree("sp_dirty", "820")

        # Leave uncommitted changes
        (path / "dirty.txt").write_text("uncommitted")

        # Should not raise
        wt.cleanup_task_worktree("sp_dirty", "820")
        assert not path.exists()

    def test_finalize_spec(self, wt, repo):
        wt.create_spec_branch("sp_fin")
        wt.create_task_worktree("sp_fin", "901")
        wt.create_task_worktree("sp_fin", "902")

        wt.finalize_spec("sp_fin")

        spec_dir = wt.worktree_base / "sp_fin"
        if spec_dir.exists():
            assert len(list(spec_dir.iterdir())) == 0

    def test_cleanup_all(self, wt, repo):
        wt.create_spec_branch("sp_all1")
        wt.create_spec_branch("sp_all2")
        wt.create_task_worktree("sp_all1", "1001")
        wt.create_task_worktree("sp_all2", "1002")

        wt.cleanup_all()

        if wt.worktree_base.exists():
            remaining = [
                p for p in wt.worktree_base.iterdir()
                if p.name != "_merge"
            ]
            assert len(remaining) == 0


# ------------------------------------------------------------------
# Lock serialization
# ------------------------------------------------------------------

class TestLockSerialization:
    def test_lock_file_created(self, wt, repo):
        wt.create_spec_branch("sp_lock")
        path = wt.create_task_worktree("sp_lock", "1101")
        _commit_file(path, "file.txt", "content", "add file")

        wt.merge_task_into_spec("sp_lock", "1101", "Test lock")
        assert wt.lock_dir.exists()

    def test_concurrent_merges_serialize(self, wt, repo):
        """Two threads try to merge different tasks simultaneously.
        Both should succeed sequentially via file lock — no corruption."""
        wt.create_spec_branch("sp_conc")
        path_a = wt.create_task_worktree("sp_conc", "1201")
        path_b = wt.create_task_worktree("sp_conc", "1202")

        _commit_file(path_a, "from_a.txt", "a_data", "task a commit")
        _commit_file(path_b, "from_b.txt", "b_data", "task b commit")

        results = [None, None]
        errors = [None, None]

        def merge_task(idx, tid, title):
            try:
                results[idx] = wt.merge_task_into_spec("sp_conc", tid, title)
            except Exception as e:
                errors[idx] = e

        t1 = threading.Thread(target=merge_task, args=(0, "1201", "Task A"))
        t2 = threading.Thread(target=merge_task, args=(1, "1202", "Task B"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert errors[0] is None, f"Thread 0 error: {errors[0]}"
        assert errors[1] is None, f"Thread 1 error: {errors[1]}"
        assert results[0].success is True
        assert results[1].success is True

        # Verify both files on spec branch
        show_a = _run(["git", "show", "spec/sp_conc:from_a.txt"], cwd=repo)
        assert "a_data" in show_a.stdout
        show_b = _run(["git", "show", "spec/sp_conc:from_b.txt"], cwd=repo)
        assert "b_data" in show_b.stdout


# ------------------------------------------------------------------
# Agent config propagation
# ------------------------------------------------------------------

class TestAgentConfigCopy:
    def test_configs_copied_to_task_worktree(self, wt, repo):
        """Agent configs from project root appear in the task worktree."""
        (repo / ".env").write_text("BOARD_ID=5\n")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text('{"model":"opus"}')
        odin_dir = repo / ".odin"
        odin_dir.mkdir(exist_ok=True)
        (odin_dir / "config.yaml").write_text("board_id: 5\n")

        wt.create_spec_branch("sp_cfg")
        path = wt.create_task_worktree("sp_cfg", "2001")

        assert (path / ".env").read_text() == "BOARD_ID=5\n"
        assert (path / ".claude" / "settings.json").read_text() == '{"model":"opus"}'
        assert (path / ".odin" / "config.yaml").read_text() == "board_id: 5\n"

    def test_configs_copied_to_spec_worktree(self, wt, repo):
        (repo / ".env").write_text("KEY=val\n")
        wt.create_spec_branch("sp_cfgs")
        path = wt.create_spec_worktree("sp_cfgs")
        assert (path / ".env").read_text() == "KEY=val\n"

    def test_configs_are_copies_not_symlinks(self, wt, repo):
        (repo / ".env").write_text("original\n")
        wt.create_spec_branch("sp_nosym")
        path = wt.create_task_worktree("sp_nosym", "2010")

        assert not (path / ".env").is_symlink()
        assert (path / ".env").read_text() == "original\n"

    def test_idempotent_when_destination_exists(self, wt, repo):
        """Second worktree creation doesn't overwrite existing configs."""
        (repo / ".env").write_text("first\n")
        wt.create_spec_branch("sp_idem")
        path = wt.create_task_worktree("sp_idem", "2020")

        # Modify the copy in worktree
        (path / ".env").write_text("modified\n")

        # Re-creating is a no-op (worktree already exists)
        path2 = wt.create_task_worktree("sp_idem", "2020")
        assert path == path2
        assert (path / ".env").read_text() == "modified\n"

    def test_no_error_when_no_configs_exist(self, wt, repo):
        """Worktree creation works fine when project root has no agent configs."""
        wt.create_spec_branch("sp_nocfg")
        path = wt.create_task_worktree("sp_nocfg", "2030")
        assert path.exists()


class TestAgentConfigGitignore:
    def test_gitignore_written_in_worktree(self, wt, repo):
        wt.create_spec_branch("sp_gi")
        path = wt.create_task_worktree("sp_gi", "2100")

        gi = (path / ".gitignore")
        assert gi.exists()
        content = gi.read_text()
        assert _GITIGNORE_MARKER in content
        assert "/.env" in content
        assert "/.claude" in content

    def test_git_add_does_not_stage_agent_configs(self, wt, repo):
        """git add -A in the worktree should not stage agent config files."""
        (repo / ".env").write_text("SECRET=x\n")
        wt.create_spec_branch("sp_nostage")
        path = wt.create_task_worktree("sp_nostage", "2110")

        # Write a real change and run git add -A
        (path / "feature.py").write_text("print('hello')\n")
        _run(["git", "add", "-A"], cwd=path)

        # Check what's staged
        staged = _run(["git", "diff", "--cached", "--name-only"], cwd=path)
        staged_files = staged.stdout.strip().splitlines()

        assert "feature.py" in staged_files
        assert ".env" not in staged_files
        assert ".gitignore" not in staged_files


class TestAgentConfigMergeFix:
    def test_merge_succeeds_with_config_copies(self, wt, repo):
        """Two worktrees both get config copies — merge shouldn't conflict."""
        (repo / ".env").write_text("BOARD=5\n")
        wt.create_spec_branch("sp_mcfg")
        path_a = wt.create_task_worktree("sp_mcfg", "2201")
        path_b = wt.create_task_worktree("sp_mcfg", "2202")

        # Both have .env copies
        assert (path_a / ".env").exists()
        assert (path_b / ".env").exists()

        # Each task modifies different files
        _commit_file(path_a, "a.py", "a_code", "task a")
        _commit_file(path_b, "b.py", "b_code", "task b")

        r1 = wt.merge_task_into_spec("sp_mcfg", "2201", "Task A")
        assert r1.success is True

        r2 = wt.merge_task_into_spec("sp_mcfg", "2202", "Task B")
        assert r2.success is True

        # Both files on spec branch
        show_a = _run(["git", "show", "spec/sp_mcfg:a.py"], cwd=repo)
        assert "a_code" in show_a.stdout
        show_b = _run(["git", "show", "spec/sp_mcfg:b.py"], cwd=repo)
        assert "b_code" in show_b.stdout

    def test_auto_commit_excludes_gitignore(self, wt, repo):
        """Auto-commit should not include .gitignore in the commit."""
        wt.create_spec_branch("sp_acgi")
        path = wt.create_task_worktree("sp_acgi", "2210")

        # Write a real file but don't commit
        (path / "uncommitted.py").write_text("code\n")

        # Auto-commit via merge
        result = wt.merge_task_into_spec("sp_acgi", "2210", "Auto test")
        assert result.success is True

        # Verify .gitignore is NOT in the task branch history
        log = _run(
            ["git", "log", "--all", "--name-only", "--pretty=format:", "task/sp_acgi/2210"],
            cwd=repo,
        )
        committed_files = log.stdout.strip().splitlines()
        assert ".gitignore" not in committed_files
