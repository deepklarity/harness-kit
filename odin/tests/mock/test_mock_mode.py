"""Tests for mock mode execution (no backend writes).

Tags: [mock] — no real HTTP, no real LLM.

Verifies that exec_task(mock=True) runs the harness but skips all
backend writes: status changes, comments, cost tracking, metadata.
"""

import asyncio

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator
from odin.taskit.models import TaskStatus


@pytest.fixture
def config_with_mock(odin_dirs):
    """Config with mock harness for execution tests."""
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


class TestMockModeExecution:
    """exec_task(mock=True) runs harness but skips backend writes."""

    def test_mock_no_status_writes(self, odin_dirs, config_with_mock):
        """Mock mode does not change task status."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write a poem",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")
        original_status = orch.task_mgr.get_task(task.id).status

        result = asyncio.run(orch.exec_task(task.id, mock=True))
        assert result["success"] is True

        # Status should NOT have changed
        task_after = orch.task_mgr.get_task(task.id)
        assert task_after.status == original_status

    def test_mock_no_comments(self, odin_dirs, config_with_mock):
        """Mock mode does not post any comments."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write a poem",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        result = asyncio.run(orch.exec_task(task.id, mock=True))
        assert result["success"] is True

        # No comments should have been posted
        comments = orch.task_mgr.get_comments(task.id)
        assert len(comments) == 0

    def test_mock_returns_result(self, odin_dirs, config_with_mock):
        """Mock mode still runs the harness and returns parsed result."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write a poem",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        result = asyncio.run(orch.exec_task(task.id, mock=True))

        assert result["success"] is True
        assert result["task_id"] == task.id
        assert result["agent"] == "mock"
        # Output should contain the mock harness response (envelope parsed)
        assert result["output"] is not None

    def test_mock_executing_transition_skipped(self, odin_dirs, config_with_mock):
        """In mock mode, task stays in original status (no EXECUTING transition)."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write code",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        # Manually set to IN_PROGRESS to simulate queued state
        orch.task_mgr.update_status(task.id, TaskStatus.IN_PROGRESS)

        result = asyncio.run(orch.exec_task(task.id, mock=True))
        assert result["success"] is True

        # Should still be IN_PROGRESS, not EXECUTING or REVIEW
        task_after = orch.task_mgr.get_task(task.id)
        assert task_after.status == TaskStatus.IN_PROGRESS

    def test_mock_no_cost_tracking(self, odin_dirs, config_with_mock):
        """Mock mode does not record cost data."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write code",
            spec_id="test_spec",
        )
        orch.task_mgr.assign_task(task.id, "mock")

        result = asyncio.run(orch.exec_task(task.id, mock=True))
        assert result["success"] is True

        # No cost records should exist
        records = orch.cost_store.load_by_spec("test_spec")
        assert len(records) == 0


class TestExecutingStatusTransition:
    """Normal (non-mock) execution sets EXECUTING status."""

    def test_normal_exec_sets_executing(self, odin_dirs, config_with_mock):
        """_execute_task sets status to EXECUTING (not IN_PROGRESS)."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write code",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        # Track status transitions
        statuses_seen = []
        original_update = orch.task_mgr.update_status

        def track_status(tid, status):
            statuses_seen.append(status)
            return original_update(tid, status)

        orch.task_mgr.update_status = track_status

        result = asyncio.run(orch.exec_task(task.id))
        assert result["success"] is True

        # First status change should be EXECUTING
        assert TaskStatus.EXECUTING in statuses_seen

    def test_already_executing_skips_transition(self, odin_dirs, config_with_mock):
        """When task is already EXECUTING (Celery path), skip the transition."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write code",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")
        orch.task_mgr.update_status(task.id, TaskStatus.EXECUTING)

        statuses_seen = []
        original_update = orch.task_mgr.update_status

        def track_status(tid, status):
            statuses_seen.append(status)
            return original_update(tid, status)

        orch.task_mgr.update_status = track_status

        result = asyncio.run(orch.exec_task(task.id))
        assert result["success"] is True

        # Should NOT have set EXECUTING again (only REVIEW at the end via record_execution_result)
        assert TaskStatus.EXECUTING not in statuses_seen
