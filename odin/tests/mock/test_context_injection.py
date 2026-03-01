"""Tests for upstream context injection in exec_task().

Tags: [mock] — no real HTTP, no real LLM.

Verifies that exec_task() fetches comments from completed upstream tasks
and injects them into the task description before execution. This is the
persistent equivalent of exec_all()'s in-memory completed_outputs dict.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.models import Task, TaskStatus


@pytest.fixture
def config_with_mock(odin_dirs):
    """Config that uses mock harness and local backend."""
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents={
            "mock": AgentConfig(
                cli_command="mock",
                capabilities=["coding"],
                cost_tier=CostTier.LOW,
                enabled=True,
            ),
        },
    )


class TestContextInjection:
    """exec_task() injects upstream context from completed dependency comments."""

    def test_exec_task_injects_upstream_comments(self, odin_dirs, config_with_mock):
        """When task B depends on completed task A, B's prompt includes A's comment."""
        orch = Orchestrator(config=config_with_mock)

        # Create task A (completed with a comment)
        task_a = orch.task_mgr.create_task(
            title="Task A: Generate data",
            description="Generate CSV data",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task_a.id, "mock")
        orch.task_mgr.update_status(task_a.id, TaskStatus.DONE)
        orch.task_mgr.add_comment(
            task_a.id, "mock",
            "Completed in 2.0s\n\nGenerated data.csv with 100 rows."
        )

        # Create task B that depends on A
        task_b = orch.task_mgr.create_task(
            title="Task B: Analyze data",
            description="Analyze the generated data",
            spec_id=None,
        )
        task_b.depends_on = [task_a.id]
        orch.task_mgr.update_task(task_b)
        orch.task_mgr.assign_task(task_b.id, "mock")

        # Track what prompt gets passed to _execute_task
        captured_prompts = []
        original_execute = orch._execute_task

        async def capture_execute(task_id, agent, prompt, wd, sem, mock=False):
            captured_prompts.append(prompt)
            return {
                "task_id": task_id,
                "agent": agent,
                "success": True,
                "output": "Done",
                "error": None,
            }

        orch._execute_task = capture_execute

        result = asyncio.run(orch.exec_task(task_b.id))

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "Context from upstream task" in prompt
        assert "Task A: Generate data" in prompt
        assert "Generated data.csv with 100 rows." in prompt
        assert "Analyze the generated data" in prompt

    def test_exec_task_no_injection_without_deps(self, odin_dirs, config_with_mock):
        """Tasks without dependencies get their original description unchanged."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Independent task",
            description="Do something standalone",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        captured_prompts = []

        async def capture_execute(task_id, agent, prompt, wd, sem, mock=False):
            captured_prompts.append(prompt)
            return {
                "task_id": task_id, "agent": agent,
                "success": True, "output": "Done", "error": None,
            }

        orch._execute_task = capture_execute
        asyncio.run(orch.exec_task(task.id))

        assert len(captured_prompts) == 1
        assert captured_prompts[0] == "Do something standalone"

    def test_exec_task_skips_incomplete_deps(self, odin_dirs, config_with_mock):
        """Only DONE/REVIEW dependencies contribute context."""
        orch = Orchestrator(config=config_with_mock)

        # Task A: still in progress (should NOT contribute context)
        task_a = orch.task_mgr.create_task(
            title="Task A", description="Still running", spec_id=None,
        )
        orch.task_mgr.assign_task(task_a.id, "mock")
        orch.task_mgr.update_status(task_a.id, TaskStatus.DONE)  # Mark done first
        orch.task_mgr.add_comment(task_a.id, "mock", "A is done")

        # Task B: also completed
        task_b = orch.task_mgr.create_task(
            title="Task B", description="Also done", spec_id=None,
        )
        orch.task_mgr.assign_task(task_b.id, "mock")
        orch.task_mgr.update_status(task_b.id, TaskStatus.IN_PROGRESS)
        # IN_PROGRESS — should NOT contribute

        # Task C depends on both A and B
        task_c = orch.task_mgr.create_task(
            title="Task C", description="Merge results", spec_id=None,
        )
        task_c.depends_on = [task_a.id, task_b.id]
        orch.task_mgr.update_task(task_c)
        orch.task_mgr.assign_task(task_c.id, "mock")

        captured_prompts = []

        async def capture_execute(task_id, agent, prompt, wd, sem, mock=False):
            captured_prompts.append(prompt)
            return {
                "task_id": task_id, "agent": agent,
                "success": True, "output": "Done", "error": None,
            }

        orch._execute_task = capture_execute

        # exec_task should fail because B is not done (WAITING dep check)
        result = asyncio.run(orch.exec_task(task_c.id))
        # B is IN_PROGRESS so deps aren't met — task should be skipped
        assert result["success"] is False
        assert "not yet completed" in result.get("error", "")

    def test_exec_task_merges_multiple_upstream(self, odin_dirs, config_with_mock):
        """When task C depends on A and B, both comments are injected."""
        orch = Orchestrator(config=config_with_mock)

        task_a = orch.task_mgr.create_task(
            title="Task A", description="Part 1", spec_id=None,
        )
        orch.task_mgr.assign_task(task_a.id, "mock")
        orch.task_mgr.update_status(task_a.id, TaskStatus.DONE)
        orch.task_mgr.add_comment(task_a.id, "mock", "Output from A")

        task_b = orch.task_mgr.create_task(
            title="Task B", description="Part 2", spec_id=None,
        )
        orch.task_mgr.assign_task(task_b.id, "mock")
        orch.task_mgr.update_status(task_b.id, TaskStatus.DONE)
        orch.task_mgr.add_comment(task_b.id, "mock", "Output from B")

        task_c = orch.task_mgr.create_task(
            title="Task C", description="Merge A + B", spec_id=None,
        )
        task_c.depends_on = [task_a.id, task_b.id]
        orch.task_mgr.update_task(task_c)
        orch.task_mgr.assign_task(task_c.id, "mock")

        captured_prompts = []

        async def capture_execute(task_id, agent, prompt, wd, sem, mock=False):
            captured_prompts.append(prompt)
            return {
                "task_id": task_id, "agent": agent,
                "success": True, "output": "Done", "error": None,
            }

        orch._execute_task = capture_execute
        asyncio.run(orch.exec_task(task_c.id))

        prompt = captured_prompts[0]
        assert "Output from A" in prompt
        assert "Output from B" in prompt
        assert "---" in prompt  # Separator between context and description
        assert "Merge A + B" in prompt
