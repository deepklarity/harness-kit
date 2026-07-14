"""Disk tests for WorktreeManager — real git repos in tmp directories.

These tests create actual git repositories and exercise real git operations.
No mocks — verifies end-to-end worktree lifecycle.
"""

import subprocess
import threading
from pathlib import Path

import pytest

from odin.mcps.taskit_mcp.config import HARNESS_GENERATED_PATHS, MCP_CONFIG_MAP
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

    def test_stale_directory_recovered(self, wt, repo):
        """Stale directory (no .git) at the worktree path is cleaned up and
        the worktree created fresh — regression for task 236 retry crash
        ('already exists')."""
        wt.create_spec_branch("sp_stale")
        stale_path = wt.get_worktree_path("sp_stale", "801")
        stale_path.mkdir(parents=True)
        (stale_path / "leftover.txt").write_text("from failed run")
        # No .git — stale state

        path = wt.create_task_worktree("sp_stale", "801")
        assert path.exists()
        assert (path / ".git").exists()
        # Stale content must be gone — fresh checkout, not the old dir
        assert not (path / "leftover.txt").exists()

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

    def test_merge_strips_committed_proof_from_spec_branch(self, wt, repo):
        """Committed .proof/ on the task branch never reaches the spec branch.

        Agents commit .proof/task-<id>/ per convention, but it belongs on the
        task board (uploaded as attachments), not in shared git history. The
        merge strips it before the push — even when the agent explicitly
        committed it.
        """
        wt.create_spec_branch("sp_cproof")
        path = wt.create_task_worktree("sp_cproof", "560")

        _commit_file(path, "feature.py", "def hello(): pass\n", "add feature")
        proof_dir = path / ".proof" / "task-560"
        proof_dir.mkdir(parents=True)
        (proof_dir / "proof.md").write_text("# Proof\nAll green.")
        (proof_dir / "tests.txt").write_text("5 passed")
        # Force-add past the worktree .gitignore (which now excludes .proof/).
        # This simulates a task branch that committed .proof/ before the
        # gitignore was in place, or an agent that bypassed it — exactly the
        # scenario the post-merge strip is designed to catch.
        _run(["git", "add", "-f", ".proof/"], cwd=path)
        _run(["git", "commit", "-m", "add proof files"], cwd=path)

        result = wt.merge_task_into_spec("sp_cproof", "560", "Task with proof")
        assert result.success is True

        # Real code is on the spec branch
        show = _run(["git", "show", "spec/sp_cproof:feature.py"], cwd=repo)
        assert "def hello()" in show.stdout

        # .proof/ is NOT on the spec branch
        ls = _run(["git", "ls-tree", "-r", "--name-only", "spec/sp_cproof"], cwd=repo)
        proof_files = [l for l in ls.stdout.splitlines() if l.startswith(".proof")]
        assert proof_files == [], f".proof/ leaked onto spec branch: {proof_files}"

    def test_merge_without_proof_is_unaffected(self, wt, repo):
        """A task with no .proof/ merges normally — strip is a no-op."""
        wt.create_spec_branch("sp_noproof")
        path = wt.create_task_worktree("sp_noproof", "561")
        _commit_file(path, "code.py", "x = 1\n", "add code")

        result = wt.merge_task_into_spec("sp_noproof", "561")
        assert result.success is True

        show = _run(["git", "show", "spec/sp_noproof:code.py"], cwd=repo)
        assert "x = 1" in show.stdout

    def test_auto_commit_excludes_uncommitted_proof(self, wt, repo):
        """Uncommitted .proof/ is not swept into the task branch by auto-commit."""
        wt.create_spec_branch("sp_acproof")
        path = wt.create_task_worktree("sp_acproof", "562")
        _commit_file(path, "code.py", "x = 1\n", "add code")

        proof_dir = path / ".proof" / "task-562"
        proof_dir.mkdir(parents=True)
        (proof_dir / "proof.md").write_text("evidence")

        result = wt.merge_task_into_spec("sp_acproof", "562")
        assert result.success is True

        # code.py merged, .proof/ did not
        show = _run(["git", "show", "spec/sp_acproof:code.py"], cwd=repo)
        assert "x = 1" in show.stdout
        ls = _run(["git", "ls-tree", "-r", "--name-only", "spec/sp_acproof"], cwd=repo)
        assert not any(l.startswith(".proof") for l in ls.stdout.splitlines())

    def test_auto_commit_pathspec_leaves_proof_untracked_and_on_disk(self, wt, repo):
        """_auto_commit_worktree must leave .proof/ untracked while the
        files remain on disk so the proof uploader can read them.

        The pathspec exclude in ``git add`` is the primary mechanism —
        .proof/ is never staged, not staged-then-unstaged.  This test
        calls _auto_commit_worktree directly (not via merge) to verify
        the index state immediately after the commit.
        """
        wt.create_spec_branch("sp_pf2")
        path = wt.create_task_worktree("sp_pf2", "563")
        _commit_file(path, "code.py", "x = 1\n", "add code")

        # Uncommitted source change + uncommitted .proof/ files
        proof_dir = path / ".proof" / "task-563"
        proof_dir.mkdir(parents=True)
        (proof_dir / "proof.md").write_text("# Evidence\nAll tests pass.")
        (proof_dir / "odin_tests.txt").write_text("5 passed")
        (path / "feature.py").write_text("y = 2\n")

        result = wt._auto_commit_worktree("sp_pf2", "563", "test task")
        assert result.committed is True

        # feature.py committed, .proof/ is NOT tracked
        tracked = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=path)
        assert "feature.py" in tracked.stdout
        assert not any(l.startswith(".proof") for l in tracked.stdout.splitlines())

        # .proof/ files survive on disk for the proof uploader
        assert (proof_dir / "proof.md").read_text() == "# Evidence\nAll tests pass."
        assert (proof_dir / "odin_tests.txt").read_text() == "5 passed"


# ------------------------------------------------------------------
# Merge-status query (git-reality check used by the merge watchdog)
# ------------------------------------------------------------------

class TestIsTaskMerged:
    """is_task_merged — is the task branch already an ancestor of the spec branch?

    The merge watchdog uses this to avoid escalating a merge whose commit
    landed but whose metadata stamp never did. Must be exact (never false
    'merged') and never raise.
    """

    def test_not_merged_before_merge(self, wt, repo):
        wt.create_spec_branch("sp_q")
        path = wt.create_task_worktree("sp_q", "901")
        _commit_file(path, "f.py", "x = 1\n", "task work")

        assert wt.is_task_merged("sp_q", "901") is False

    def test_merged_after_merge(self, wt, repo):
        wt.create_spec_branch("sp_qm")
        path = wt.create_task_worktree("sp_qm", "902")
        _commit_file(path, "f.py", "x = 1\n", "task work")
        assert wt.merge_task_into_spec("sp_qm", "902").success

        assert wt.is_task_merged("sp_qm", "902") is True

    def test_missing_branches_return_false(self, wt):
        """Neither branch exists → False, not an exception."""
        assert wt.is_task_merged("sp_missing", "999") is False

    def test_missing_task_branch_return_false(self, wt, repo):
        """Spec branch exists but task branch does not → False."""
        wt.create_spec_branch("sp_half")
        assert wt.is_task_merged("sp_half", "9999") is False


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

    def test_reprovisions_missing_config_on_reused_task_worktree(self, wt, repo):
        """A half-provisioned worktree (has .git but missing .odin/config.yaml)
        gets its config re-provisioned when the worktree is reused across
        retries. Regression for task #339: the idempotent early-return path
        skipped _copy_agent_configs, so a worktree left half-provisioned by a
        crashed run never received its config back — load_config() then fell
        back to defaults and produced a misleading resolver failure.
        """
        odin_dir = repo / ".odin"
        odin_dir.mkdir(exist_ok=True)
        (odin_dir / "config.yaml").write_text("board_id: 5\n")

        wt.create_spec_branch("sp_reprov")
        path = wt.create_task_worktree("sp_reprov", "2040")
        assert (path / ".odin" / "config.yaml").exists()

        # Simulate a half-provisioned worktree: remove the config.
        (path / ".odin" / "config.yaml").unlink()
        assert not (path / ".odin" / "config.yaml").exists()

        # Reuse across a retry — config must be re-provisioned.
        path2 = wt.create_task_worktree("sp_reprov", "2040")
        assert path == path2
        assert (path2 / ".odin" / "config.yaml").read_text() == "board_id: 5\n"

    def test_reprovisions_missing_config_on_reused_spec_worktree(self, wt, repo):
        """Same re-provisioning guarantee for the spec worktree."""
        odin_dir = repo / ".odin"
        odin_dir.mkdir(exist_ok=True)
        (odin_dir / "config.yaml").write_text("board_id: 9\n")

        wt.create_spec_branch("sp_reprovs")
        path = wt.create_spec_worktree("sp_reprovs")
        assert (path / ".odin" / "config.yaml").exists()

        (path / ".odin" / "config.yaml").unlink()
        assert not (path / ".odin" / "config.yaml").exists()

        path2 = wt.create_spec_worktree("sp_reprovs")
        assert path == path2
        assert (path2 / ".odin" / "config.yaml").read_text() == "board_id: 9\n"


class TestAgentConfigGitignore:
    def test_excludes_written_to_info_exclude_not_tracked_gitignore(self, wt, repo):
        """Agent-config ignores go to git info/exclude, NEVER the tracked
        .gitignore. Regression (task 287): odin appended to the tracked
        .gitignore as an uncommitted change, so any task branch that also
        touched .gitignore aborted its merge with would-be-overwritten."""
        wt.create_spec_branch("sp_gi")
        path = wt.create_task_worktree("sp_gi", "2100")

        exclude = _run(
            ["git", "rev-parse", "--git-path", "info/exclude"], cwd=path
        ).stdout.strip()
        exclude_path = Path(exclude)
        if not exclude_path.is_absolute():
            exclude_path = path / exclude_path
        content = exclude_path.read_text()
        assert _GITIGNORE_MARKER in content
        assert "/.env" in content
        assert "/.claude" in content

        # The tracked .gitignore must be untouched: worktree starts clean.
        status = _run(["git", "status", "--porcelain"], cwd=path).stdout.strip()
        assert status == "", f"worktree dirty after setup: {status!r}"

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

    def test_auto_commit_excludes_all_harness_generated_paths(self, wt, repo):
        """Regression for F24 (instance 2): merge-time auto-commit must
        never sweep generated agent configs onto the task branch.

        Fills the worktree with every artifact the harness/MCP config
        writers produce — ``.codex/``, ``.gemini/``, ``.kilocode/``,
        ``.qwen/``, ``opencode.json``, ``.mcp.json``, ``*.orig`` backups —
        then runs auto-commit and asserts the committed tree contains
        none of them.  Mirrors the real-world failure mode where two
        tasks share the same harness config and the second commit would
        otherwise conflict against the first.
        """
        wt.create_spec_branch("sp_f24")
        path = wt.create_task_worktree("sp_f24", "2230")

        # Write every file the MCP_CONFIG_MAP writers produce
        configs = {
            ".codex/config.toml": "[mcp_servers.taskit]\n",
            ".gemini/settings.json": '{"mcpServers": {}}',
            ".kilocode/mcp.json": '{"mcpServers": {}}',
            ".qwen/settings.json": '{"mcpServers": {}}',
            "opencode.json": '{"permission": {}, "mcp": {}}',
            ".mcp.json": '{"mcpServers": {}}',
        }
        for rel, content in configs.items():
            full = path / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)

        # A .orig backup (as git merge would create) and a stray
        # settings.local.json that the Claude CLI generates.
        (path / "opencode.json.orig").write_text("old\n")
        (path / ".claude").mkdir()
        (path / ".claude" / "settings.local.json").write_text(
            '{"permissions": {"allow": ["Read"]}}'
        )

        # Add one legitimate change that *should* be committed.
        (path / "feature.py").write_text("print('hi')\n")

        # Auto-commit via merge
        result = wt.merge_task_into_spec("sp_f24", "2230", "F24 regression")
        assert result.success is True, f"merge failed: {result.error}"

        # Walk the committed tree on the task branch and confirm none of
        # the harness-generated paths leaked in.  ``git log --name-only``
        # returns files touched in every commit, including the auto-commit.
        log = _run(
            ["git", "log", "--all", "--name-only", "--pretty=format:",
             "task/sp_f24/2230"],
            cwd=repo,
        )
        committed_files = set(filter(None, log.stdout.strip().splitlines()))

        # Legitimate change should have made it through.
        assert "feature.py" in committed_files

        # None of the harness-generated paths should be committed.
        never_commit = {
            ".codex/config.toml",
            ".gemini/settings.json",
            ".kilocode/mcp.json",
            ".qwen/settings.json",
            "opencode.json",
            "opencode.json.orig",
            ".mcp.json",
            ".claude/settings.local.json",
            ".gitignore",
        }
        leaked = never_commit & committed_files
        assert not leaked, (
            f"Auto-commit swept harness-generated configs onto task "
            f"branch: {sorted(leaked)}"
        )


class TestAutoCommitPollutionGuard:
    """Defence-in-depth against merge-time auto-commit sweeps that
    ``.gitignore`` alone can't stop (regression for the 4,738-file
    venv pollution incident).

    Two guards:
      1. A hard pollution blocklist (venv markers, node_modules,
         site-packages, __pycache__) unstaged regardless of .gitignore.
      2. A bulk-add guard: if more than ``_AUTO_COMMIT_MAX_NEW_FILES``
         new untracked files would be committed, refuse the bulk and
         commit only tracked modifications.
    """

    def _committed_files(self, repo, branch):
        log = _run(
            ["git", "log", "--all", "--name-only", "--pretty=format:", branch],
            cwd=repo,
        )
        return set(filter(None, log.stdout.strip().splitlines()))

    def test_venv_with_stale_gitignore_is_excluded(self, wt, repo):
        """A venv (with pyvenv.cfg) sitting in a worktree whose
        .gitignore predates the ``.venv*/`` pattern must never enter
        the auto-commit tree.  Real work alongside it still commits.
        """
        from odin.worktree import _AUTO_COMMIT_MAX_NEW_FILES

        wt.create_spec_branch("sp_venv")
        path = wt.create_task_worktree("sp_venv", "2401")

        # Simulate a real Python venv (the thing that caused the incident).
        # pyvenv.cfg is the definitive venv marker.
        (path / ".venv" / "bin").mkdir(parents=True)
        (path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (path / ".venv" / "bin" / "python").write_text("binary\n")
        (path / ".venv" / "lib" / "site-packages").mkdir(parents=True)
        (path / ".venv" / "lib" / "site-packages" / "pkg.py").write_text("# pkg\n")
        # A __pycache__ dir at repo root too.
        (path / "__pycache__").mkdir()
        (path / "__pycache__" / "x.pyc").write_text("bytecode\n")

        # Legitimate real work.
        (path / "feature.py").write_text("print('real work')\n")

        # Confirm the venv is genuinely unignored (stale .gitignore scenario):
        # ``git status --porcelain`` must list .venv as untracked.
        raw_status = _run(["git", "status", "--porcelain"], cwd=path).stdout
        assert ".venv/" in raw_status or ".venv/pyvenv.cfg" in raw_status, (
            "test setup broken: .venv is already gitignored; this test "
            "needs a stale-ignore scenario to be meaningful"
        )

        result = wt.merge_task_into_spec("sp_venv", "2401", "venv guard")
        assert result.success is True, f"merge failed: {result.error}"

        committed = self._committed_files(repo, "task/sp_venv/2401")
        assert "feature.py" in committed, "real work should still be committed"
        # No venv / pycache file may have leaked in.
        venv_leaked = {f for f in committed if ".venv" in f.split("/")
                       or f.startswith("__pycache__")
                       or "site-packages" in f.split("/")}
        assert not venv_leaked, f"auto-commit swept venv pollution: {sorted(venv_leaked)}"

    def test_venv_at_nonstandard_name_excluded_via_marker(self, wt, repo):
        """A venv at a non-obvious name (``env/``) is detected via the
        ``pyvenv.cfg`` marker file, not just the ``.venv*`` prefix."""
        wt.create_spec_branch("sp_envmk")
        path = wt.create_task_worktree("sp_envmk", "2402")

        (path / "env" / "bin").mkdir(parents=True)
        (path / "env" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (path / "env" / "bin" / "python").write_text("binary\n")
        (path / "real.py").write_text("x = 1\n")

        result = wt.merge_task_into_spec("sp_envmk", "2402", "marker")
        assert result.success is True, f"merge failed: {result.error}"

        committed = self._committed_files(repo, "task/sp_envmk/2402")
        assert "real.py" in committed
        env_leaked = {f for f in committed if f.startswith("env/")}
        assert not env_leaked, f"pyvenv.cfg marker missed a venv: {sorted(env_leaked)}"

    def test_node_modules_excluded(self, wt, repo):
        """node_modules is in the hard blocklist."""
        wt.create_spec_branch("sp_nm")
        path = wt.create_task_worktree("sp_nm", "2403")

        (path / "node_modules" / "foo").mkdir(parents=True)
        (path / "node_modules" / "foo" / "index.js").write_text("module.exports\n")
        (path / "app.js").write_text("console.log('real')\n")

        result = wt.merge_task_into_spec("sp_nm", "2403", "nm guard")
        assert result.success is True, f"merge failed: {result.error}"

        committed = self._committed_files(repo, "task/sp_nm/2403")
        assert "app.js" in committed
        assert not any(f.startswith("node_modules/") for f in committed), \
            "node_modules leaked into commit"

    def test_bulk_guard_refuses_many_new_files(self, wt, repo):
        """When the sweep would add more than ``_AUTO_COMMIT_MAX_NEW_FILES``
        new untracked files, the bulk is refused and only tracked
        modifications are committed.  The skip is reported."""
        from odin.worktree import _AUTO_COMMIT_MAX_NEW_FILES

        wt.create_spec_branch("sp_bulk")
        path = wt.create_task_worktree("sp_bulk", "2404")

        # A tracked file to modify (README.md exists from _init_repo).
        (path / "README.md").write_text("# modified by task\n")

        # Generate well over the threshold of new untracked files that
        # are NOT on the pollution blocklist (so only the bulk guard
        # catches them).
        over = _AUTO_COMMIT_MAX_NEW_FILES + 50
        bulk_dir = path / "generated_logs"
        bulk_dir.mkdir()
        for i in range(over):
            (bulk_dir / f"log_{i}.txt").write_text(f"entry {i}\n")

        result = wt.merge_task_into_spec("sp_bulk", "2404", "bulk guard")
        assert result.success is True, f"merge failed: {result.error}"

        committed = self._committed_files(repo, "task/sp_bulk/2404")
        # The tracked modification must survive.
        assert "README.md" in committed
        # None of the bulk new files should be committed.
        bulk_leaked = {f for f in committed if f.startswith("generated_logs/")}
        assert not bulk_leaked, (
            f"bulk guard failed: {len(bulk_leaked)} new files committed "
            f"(expected 0)"
        )

    def test_bulk_guard_threshold_allows_under_limit(self, wt, repo):
        """Exactly at / under the threshold, new files ARE committed
        (boundary check — guard must not be over-eager)."""
        from odin.worktree import _AUTO_COMMIT_MAX_NEW_FILES

        wt.create_spec_branch("sp_thresh")
        path = wt.create_task_worktree("sp_thresh", "2405")

        # Just under the threshold of legitimate new source files.
        count = _AUTO_COMMIT_MAX_NEW_FILES - 1
        src_dir = path / "src_new"
        src_dir.mkdir()
        for i in range(count):
            (src_dir / f"mod_{i}.py").write_text(f"x{i} = {i}\n")

        result = wt.merge_task_into_spec("sp_thresh", "2405", "threshold")
        assert result.success is True, f"merge failed: {result.error}"

        committed = self._committed_files(repo, "task/sp_thresh/2405")
        src_committed = {f for f in committed if f.startswith("src_new/")}
        assert len(src_committed) == count, (
            f"bulk guard over-fired: only {len(src_committed)} of {count} "
            f"legitimate files committed"
        )

    def test_exclusion_reported_in_result_and_log(
        self, wt, repo, caplog,
    ):
        """The exclusion must be visible — never silent.  Both the
        returned result object and the WARNING log carry the skipped
        count."""
        import logging
        from odin.worktree import AutoCommitResult

        wt.create_spec_branch("sp_rep")
        path = wt.create_task_worktree("sp_rep", "2406")

        (path / ".venv" / "bin").mkdir(parents=True)
        (path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (path / ".venv" / "bin" / "python").write_text("x\n")
        (path / "feature.py").write_text("print('real')\n")

        with caplog.at_level(logging.WARNING, logger="odin.worktree"):
            ac = wt._auto_commit_worktree("sp_rep", "2406", "report test")

        assert isinstance(ac, AutoCommitResult)
        assert ac.committed is True
        assert ac.skipped_blocklist >= 2, (
            f"expected venv files blocklisted, got {ac.skipped_blocklist}"
        )
        # The log must mention the exclusion loudly (WARNING level).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("excluded" in r.getMessage().lower() or "blocklist" in r.getMessage().lower()
                   for r in warnings), (
            f"exclusion not surfaced in logs: {[r.getMessage() for r in warnings]}"
        )


# ------------------------------------------------------------------
# Provenance trailers (Task-Id / Spec-Id)
# ------------------------------------------------------------------

class TestProvenanceTrailers:
    """Every commit odin creates on the merge path carries Task-Id and
    Spec-Id trailers so ``testing_tools/why.py`` can answer
    "why does this line exist" via blame → trailer → task/spec."""

    def test_auto_commit_message_has_trailers(self, wt, repo):
        """Uncommitted work auto-committed at merge time carries trailers."""
        wt.create_spec_branch("sp_tr")
        path = wt.create_task_worktree("sp_tr", "501")
        # Leave work uncommitted — auto-commit kicks in at merge
        (path / "feature.py").write_text("x = 1\n")

        result = wt.merge_task_into_spec("sp_tr", "501", "Trailer task")
        assert result.success is True

        # The auto-commit is on the task branch
        auto_msg = _run(
            ["git", "log", "--format=%B", "-1", "task/sp_tr/501"],
            cwd=repo,
        ).stdout
        trailers = _run(
            ["git", "log", "--format=%(trailers)", "-1", "task/sp_tr/501"],
            cwd=repo,
        ).stdout
        assert "Task-Id: 501" in auto_msg
        assert "Spec-Id: sp_tr" in auto_msg
        assert "Task-Id: 501" in trailers
        assert "Spec-Id: sp_tr" in trailers

    def test_merge_commit_message_has_trailers(self, wt, repo):
        """The --no-ff merge commit carries trailers."""
        wt.create_spec_branch("sp_mt")
        path = wt.create_task_worktree("sp_mt", "502")
        _commit_file(path, "work.py", "y = 2\n", "real work")

        result = wt.merge_task_into_spec("sp_mt", "502", "Merge trailer")
        assert result.success is True

        # Find the merge commit on the spec branch
        merge_msg = _run(
            ["git", "log", "--format=%B", "--merges", "-1", "spec/sp_mt"],
            cwd=repo,
        ).stdout
        trailers = _run(
            ["git", "log", "--format=%(trailers)", "--merges", "-1", "spec/sp_mt"],
            cwd=repo,
        ).stdout
        assert "Task-Id: 502" in merge_msg
        assert "Spec-Id: sp_mt" in merge_msg
        assert "Task-Id: 502" in trailers
        assert "Spec-Id: sp_mt" in trailers

    def test_trailers_extractable_by_key(self, wt, repo):
        """``git log --format=%(trailers:key=...)`` returns just the value,
        which is what why.py uses to walk the chain."""
        from odin.worktree import _provenance_trailers

        # Unit-test the helper directly too
        block = _provenance_trailers("sp_x", "99")
        assert "Task-Id: 99" in block
        assert "Spec-Id: sp_x" in block

        wt.create_spec_branch("sp_key")
        path = wt.create_task_worktree("sp_key", "503")
        _commit_file(path, "f.txt", "z\n", "work")

        wt.merge_task_into_spec("sp_key", "503", "Key extract")

        task_id = _run(
            ["git", "log", "--format=%(trailers:key=Task-Id,valueonly)",
             "--merges", "-1", "spec/sp_key"],
            cwd=repo,
        ).stdout.strip()
        spec_id = _run(
            ["git", "log", "--format=%(trailers:key=Spec-Id,valueonly)",
             "--merges", "-1", "spec/sp_key"],
            cwd=repo,
        ).stdout.strip()
        assert task_id == "503"
        assert spec_id == "sp_key"

    def test_trailers_present_without_title(self, wt, repo):
        """Merge with no task_title still gets trailers."""
        wt.create_spec_branch("sp_nt")
        path = wt.create_task_worktree("sp_nt", "504")
        _commit_file(path, "a.txt", "a\n", "work")

        result = wt.merge_task_into_spec("sp_nt", "504")
        assert result.success is True

        trailers = _run(
            ["git", "log", "--format=%(trailers)", "--merges", "-1", "spec/sp_nt"],
            cwd=repo,
        ).stdout
        assert "Task-Id: 504" in trailers
        assert "Spec-Id: sp_nt" in trailers
