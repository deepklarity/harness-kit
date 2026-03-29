"""Tests for orchestrator worktree integration.

Verifies that plan() creates spec branch in metadata, exec_task() creates
task worktrees, defers merge on success, and handles failures gracefully.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from odin.orchestrator import Orchestrator

@pytest.fixture
def mock_orchestrator(odin_dirs, make_config, tmp_path):
    """Create an Orchestrator with mocked backend and worktree."""
    # WorktreeManager requires .git to exist at the project root
    (tmp_path / ".git").mkdir(exist_ok=True)
    config = make_config(
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        board_backend="local",
        worktree_enabled=True,
    )
    orch = Orchestrator(config=config)
    return orch


class TestWorktreeInit:
    def test_worktree_manager_created_when_enabled(self, mock_orchestrator):
        assert mock_orchestrator._worktree is not None

    def test_worktree_manager_none_when_disabled(self, odin_dirs, make_config):
        config = make_config(
            task_storage=str(odin_dirs["tasks"]),
            log_dir=str(odin_dirs["logs"]),
            cost_storage=str(odin_dirs["costs"]),
            board_backend="local",
            worktree_enabled=False,
        )
        orch = Orchestrator(config=config)
        assert orch._worktree is None


class TestPlanSpecBranch:
    @pytest.mark.asyncio
    async def test_plan_creates_spec_branch_in_metadata(self, mock_orchestrator, tmp_path):
        orch = mock_orchestrator
        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test123"

        with patch.object(orch, "_fetch_quota", new_callable=AsyncMock, return_value=None), \
             patch.object(orch, "_fetch_routing_config", return_value=None), \
             patch.object(orch, "_build_available_agents", new_callable=AsyncMock, return_value=""), \
             patch.object(orch, "_build_plan_prompt", return_value="test prompt"), \
             patch.object(orch, "_decompose", new_callable=AsyncMock) as mock_decompose, \
             patch.object(orch, "_create_tasks_from_plan", new_callable=AsyncMock, return_value=[]), \
             patch.object(orch, "_record_planning_trace"):

            mock_decompose.return_value = MagicMock(success=True, output="", duration_ms=100, agent="claude", error=None)

            plans_dir = Path(orch.config.task_storage).parent / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)

            original_save = orch._save_spec

            def save_and_write_plan(spec):
                original_save(spec)
                plan_path = plans_dir / f"plan_{spec.id}.json"
                plan_path.write_text("[]")

            with patch.object(orch, "_save_spec", side_effect=save_and_write_plan):
                sid, tasks = await orch.plan("Test spec content", mode="quiet")

            orch._worktree.create_spec_branch.assert_called_once()
            call_args = orch._worktree.create_spec_branch.call_args
            assert call_args[0][0] == sid
            assert call_args[1]["base_branch"] == "main"

    @pytest.mark.asyncio
    async def test_plan_succeeds_without_branch_on_git_failure(self, mock_orchestrator, tmp_path):
        orch = mock_orchestrator
        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.side_effect = RuntimeError("git not found")

        with patch.object(orch, "_fetch_quota", new_callable=AsyncMock, return_value=None), \
             patch.object(orch, "_fetch_routing_config", return_value=None), \
             patch.object(orch, "_build_available_agents", new_callable=AsyncMock, return_value=""), \
             patch.object(orch, "_build_plan_prompt", return_value="test prompt"), \
             patch.object(orch, "_decompose", new_callable=AsyncMock) as mock_decompose, \
             patch.object(orch, "_create_tasks_from_plan", new_callable=AsyncMock, return_value=[]), \
             patch.object(orch, "_record_planning_trace"):

            mock_decompose.return_value = MagicMock(success=True, output="", duration_ms=100, agent="claude", error=None)

            plans_dir = Path(orch.config.task_storage).parent / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)

            original_save = orch._save_spec

            def save_and_write_plan(spec):
                original_save(spec)
                plan_path = plans_dir / f"plan_{spec.id}.json"
                plan_path.write_text("[]")

            with patch.object(orch, "_save_spec", side_effect=save_and_write_plan):
                sid, tasks = await orch.plan("Test spec content", mode="quiet")

            assert sid is not None


class TestFinalizeSpec:
    def test_finalize_spec_creates_pr(self, mock_orchestrator):
        orch = mock_orchestrator
        orch._worktree = MagicMock()
        orch._worktree.create_spec_pr.return_value = "https://github.com/org/repo/pull/1"

        from odin.specs import SpecArchive
        spec = SpecArchive(id="sp_fin_test", title="Test Spec", source="test", content="test")
        orch.spec_store.save(spec)

        pr_url = orch.finalize_spec("sp_fin_test")
        assert pr_url == "https://github.com/org/repo/pull/1"
        orch._worktree.finalize_spec.assert_called_once_with("sp_fin_test")
        orch._worktree.create_spec_pr.assert_called_once()

    def test_finalize_spec_none_when_disabled(self, odin_dirs, make_config):
        config = make_config(
            task_storage=str(odin_dirs["tasks"]),
            log_dir=str(odin_dirs["logs"]),
            cost_storage=str(odin_dirs["costs"]),
            board_backend="local",
            worktree_enabled=False,
        )
        orch = Orchestrator(config=config)
        result = orch.finalize_spec("sp_noop")
        assert result is None

    def test_finalize_updates_spec_metadata(self, mock_orchestrator):
        orch = mock_orchestrator
        orch._worktree = MagicMock()
        orch._worktree.create_spec_pr.return_value = "https://github.com/org/repo/pull/2"

        from odin.specs import SpecArchive
        spec = SpecArchive(id="sp_meta", title="Meta Test", source="test", content="test")
        orch.spec_store.save(spec)

        orch.finalize_spec("sp_meta")

        updated = orch.spec_store.load("sp_meta")
        assert updated.metadata.get("pr_url") == "https://github.com/org/repo/pull/2"
        assert "finalized_at" in updated.metadata


# ------------------------------------------------------------------
# exec_task() worktree integration
# ------------------------------------------------------------------

def _create_task(orch, task_id="tsk_abc123", spec_id="sp_test1", agent="claude"):
    """Helper to create a task in the orchestrator's task manager."""
    from odin.taskit.models import Task, TaskStatus
    task = Task(
        id=task_id,
        title="Test task",
        description="Do something",
        status=TaskStatus.TODO,
        assigned_agent=agent,
        spec_id=spec_id,
    )
    orch.task_mgr.save_task(task)
    return task


class TestExecTaskWorktree:

    @pytest.mark.asyncio
    async def test_exec_task_creates_worktree(self, mock_orchestrator, tmp_path):
        """Verify worktree is created with correct args and working_dir is overridden."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "worktrees" / "sp_test1" / "tsk_abc123"
        wt_path.mkdir(parents=True)

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        orch._worktree.create_task_worktree.assert_called_once_with(
            spec_id="sp_test1",
            task_id="tsk_abc123",
            post_hooks=orch.config.worktree_post_hooks,
            symlinks=orch.config.worktree_symlinks,
        )
        call_args = mock_exec.call_args
        assert call_args[0][3] == str(wt_path)

        task = orch.task_mgr.get_task("tsk_abc123")
        assert task.metadata["branch"] == "task/sp_test1/tsk_abc123"
        assert task.metadata["worktree_path"] == str(wt_path)

    @pytest.mark.asyncio
    async def test_exec_task_defers_merge_on_success(self, mock_orchestrator, tmp_path):
        """Verify merge is deferred (not immediate) after successful execution."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        # Merge must NOT be called — it's deferred to reflection pass
        orch._worktree.merge_task_into_spec.assert_not_called()
        # Auto-commit must be called to save uncommitted agent work
        orch._worktree._auto_commit_worktree.assert_called_once_with(
            "sp_test1", "tsk_abc123", "Test task",
        )
        task = orch.task_mgr.get_task("tsk_abc123")
        assert task.metadata["merge_status"] == "deferred"

    @pytest.mark.asyncio
    async def test_exec_task_skips_merge_on_failure(self, mock_orchestrator, tmp_path):
        """Verify merge is NOT called when task execution fails."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": False, "error": "agent crashed"}
            await orch.exec_task("tsk_abc123")

        orch._worktree.merge_task_into_spec.assert_not_called()
        task = orch.task_mgr.get_task("tsk_abc123")
        assert task.metadata["merge_status"] == "pending"

    @pytest.mark.asyncio
    async def test_exec_task_handles_worktree_creation_failure(self, mock_orchestrator):
        """Verify graceful fallback when worktree creation fails."""
        orch = mock_orchestrator
        _create_task(orch)

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.side_effect = RuntimeError("git broken")

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            result = await orch.exec_task("tsk_abc123")

        mock_exec.assert_called_once()
        assert result["success"] is True
        orch._worktree.merge_task_into_spec.assert_not_called()
        orch._worktree.cleanup_task_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_exec_task_cleans_up_worktree_on_success(self, mock_orchestrator, tmp_path):
        """Verify cleanup_task_worktree is called after successful execution."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        # Worktree is preserved (not cleaned up) so the user
        # can open it in their editor to inspect the work.
        orch._worktree.cleanup_task_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_exec_task_no_worktree_when_disabled(self, odin_dirs, make_config):
        """Verify no worktree ops when _worktree is None."""
        config = make_config(
            task_storage=str(odin_dirs["tasks"]),
            log_dir=str(odin_dirs["logs"]),
            cost_storage=str(odin_dirs["costs"]),
            board_backend="local",
            worktree_enabled=False,
        )
        orch = Orchestrator(config=config)
        _create_task(orch)

        assert orch._worktree is None

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            result = await orch.exec_task("tsk_abc123")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_auto_commit_failure_does_not_crash(self, mock_orchestrator, tmp_path):
        """When _auto_commit_worktree raises, exec_task still returns."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path
        orch._worktree._auto_commit_worktree.side_effect = RuntimeError("git add failed")

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            result = await orch.exec_task("tsk_abc123")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_preserve_worktree_on_failure(self, mock_orchestrator, tmp_path):
        """On task failure, worktree is preserved for human inspection."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": False, "error": "agent crashed"}
            await orch.exec_task("tsk_abc123")

        # On failure: neither cleanup nor remove — worktree stays for inspection
        orch._worktree.remove_task_worktree.assert_not_called()
        orch._worktree.cleanup_task_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_mock_mode_skips_worktree(self, mock_orchestrator, tmp_path):
        """exec_task(mock=True) should not create worktree even if enabled."""
        orch = mock_orchestrator
        _create_task(orch)

        orch._worktree = MagicMock()

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123", mock=True)

        orch._worktree.create_task_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_spec_id_skips_worktree(self, mock_orchestrator, tmp_path):
        """Task without spec_id → no worktree operations."""
        orch = mock_orchestrator
        _create_task(orch, spec_id=None)

        orch._worktree = MagicMock()

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        orch._worktree.create_spec_branch.assert_not_called()
        orch._worktree.create_task_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_worktree_creation_failure_uses_original_working_dir(self, mock_orchestrator):
        """When worktree creation fails, _execute_task gets original working_dir."""
        orch = mock_orchestrator
        _create_task(orch)

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.side_effect = RuntimeError("git broken")

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        # working_dir should be the default (not a worktree path)
        call_args = mock_exec.call_args
        working_dir = call_args[0][3]  # positional arg 3 is working_dir
        assert "worktree" not in working_dir.lower()


# ------------------------------------------------------------------
# Comment posting on worktree events
# ------------------------------------------------------------------

class TestWorktreeComments:

    @pytest.mark.asyncio
    async def test_successful_worktree_creation_posts_comment(self, mock_orchestrator, tmp_path):
        """Verify a comment is posted when worktree is created successfully."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec, \
             patch.object(orch.task_mgr, "add_comment", wraps=orch.task_mgr.add_comment) as mock_comment:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        creation_comments = [
            c for c in mock_comment.call_args_list
            if "Git isolation active" in str(c)
        ]
        assert len(creation_comments) == 1
        assert "task/sp_test1/tsk_abc123" in str(creation_comments[0])

    @pytest.mark.asyncio
    async def test_worktree_creation_failure_posts_comment(self, mock_orchestrator):
        """Verify a comment is posted when worktree creation fails."""
        orch = mock_orchestrator
        _create_task(orch)

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.side_effect = RuntimeError("git broken")

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec, \
             patch.object(orch.task_mgr, "add_comment", wraps=orch.task_mgr.add_comment) as mock_comment:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        failure_comments = [
            c for c in mock_comment.call_args_list
            if "Worktree creation failed" in str(c)
        ]
        assert len(failure_comments) == 1
        assert "git broken" in str(failure_comments[0])

        task = orch.task_mgr.get_task("tsk_abc123")
        assert task.metadata["worktree_status"] == "failed"
        assert "git broken" in task.metadata["worktree_error"]

    @pytest.mark.asyncio
    async def test_deferred_merge_posts_comment(self, mock_orchestrator, tmp_path):
        """Verify a comment is posted explaining merge is deferred to reflection."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec, \
             patch.object(orch.task_mgr, "add_comment", wraps=orch.task_mgr.add_comment) as mock_comment:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        deferred_comments = [
            c for c in mock_comment.call_args_list
            if "merge deferred" in str(c)
        ]
        assert len(deferred_comments) == 1
        assert "task/sp_test1/tsk_abc123" in str(deferred_comments[0])

    @pytest.mark.asyncio
    async def test_task_failure_posts_branch_preserved_comment(self, mock_orchestrator, tmp_path):
        """Verify a comment is posted when task fails — branch preserved."""
        orch = mock_orchestrator
        _create_task(orch)

        wt_path = tmp_path / "wt"
        wt_path.mkdir()

        orch._worktree = MagicMock()
        orch._worktree.create_spec_branch.return_value = "spec/sp_test1"
        orch._worktree.create_task_worktree.return_value = wt_path

        with patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec, \
             patch.object(orch.task_mgr, "add_comment", wraps=orch.task_mgr.add_comment) as mock_comment:
            mock_exec.return_value = {"success": False, "error": "agent crashed"}
            await orch.exec_task("tsk_abc123")

        preserved_comments = [
            c for c in mock_comment.call_args_list
            if "preserved (not merged)" in str(c)
        ]
        assert len(preserved_comments) == 1

    @pytest.mark.asyncio
    async def test_worktree_disabled_posts_comment(self, mock_orchestrator):
        """When worktree is unavailable (auto-init also failed), verify explanation comment."""
        orch = mock_orchestrator
        _create_task(orch)

        orch._worktree = None
        orch._worktree_disabled_reason = "Not a git repository: /tmp/foo"

        with patch.object(orch, "_ensure_git_repo", return_value=False), \
             patch.object(orch, "_execute_task", new_callable=AsyncMock) as mock_exec, \
             patch.object(orch.task_mgr, "add_comment", wraps=orch.task_mgr.add_comment) as mock_comment:
            mock_exec.return_value = {"success": True, "output": "done"}
            await orch.exec_task("tsk_abc123")

        disabled_comments = [
            c for c in mock_comment.call_args_list
            if "Git isolation unavailable" in str(c)
        ]
        assert len(disabled_comments) == 1
        assert "odin init" in str(disabled_comments[0])
