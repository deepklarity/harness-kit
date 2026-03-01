"""Tests for execution I/O debug comments.

Tags: [mock] — no real HTTP, no real LLM.

Verifies that _execute_task() posts debug comments with effective input
and full output, tagged with debug: attachment markers.
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


class TestExecutionDebugComments:
    """Debug comments with effective input and full output."""

    def test_debug_comments_posted_during_execution(self, odin_dirs, config_with_mock):
        """_execute_task() posts debug comments before and after execution."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write a poem",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        # Run exec_task which calls _execute_task internally
        result = asyncio.run(orch.exec_task(task.id))
        assert result["success"] is True

        # Check comments were posted
        comments = orch.task_mgr.get_comments(task.id)
        # Should have: debug:effective_input, execution result comment, trace:execution_jsonl
        assert len(comments) >= 2

        # Find the comments
        input_comments = [
            c for c in comments
            if "debug:effective_input" in c.get("attachments", [])
        ]
        trace_comments = [
            c for c in comments
            if "trace:execution_jsonl" in c.get("attachments", [])
        ]

        assert len(input_comments) == 1
        assert "Effective input" in input_comments[0]["content"]
        assert "Write a poem" in input_comments[0]["content"]

        assert len(trace_comments) == 1

    def test_debug_effective_input_includes_upstream_context(self, odin_dirs, config_with_mock):
        """When upstream context is injected, the debug comment shows the full prompt."""
        orch = Orchestrator(config=config_with_mock)

        # Create completed upstream task
        task_a = orch.task_mgr.create_task(
            title="Upstream task",
            description="Generate data",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task_a.id, "mock")
        orch.task_mgr.update_status(task_a.id, TaskStatus.DONE)
        orch.task_mgr.add_comment(task_a.id, "mock", "Generated data.csv")

        # Create downstream task
        task_b = orch.task_mgr.create_task(
            title="Downstream task",
            description="Analyze data",
            spec_id=None,
        )
        task_b.depends_on = [task_a.id]
        orch.task_mgr.update_task(task_b)
        orch.task_mgr.assign_task(task_b.id, "mock")

        result = asyncio.run(orch.exec_task(task_b.id))
        assert result["success"] is True

        comments = orch.task_mgr.get_comments(task_b.id)
        input_comments = [
            c for c in comments
            if "debug:effective_input" in c.get("attachments", [])
        ]
        assert len(input_comments) == 1
        # Should include both upstream context AND original description
        content = input_comments[0]["content"]
        assert "Context from upstream task" in content or "Generated data.csv" in content

    def test_execution_result_includes_effective_input(self, odin_dirs, config_with_mock):
        """The execution_result payload includes effective_input for backend storage."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Test task",
            description="Write code",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        # Patch record_execution_result to capture the payload
        captured_payloads = []
        original_record = orch.task_mgr.record_execution_result

        def capture_record(task_id, execution_result, status, actor_email):
            captured_payloads.append(execution_result)
            return original_record(task_id, execution_result, status, actor_email)

        orch.task_mgr.record_execution_result = capture_record

        result = asyncio.run(orch.exec_task(task.id))

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert "effective_input" in payload
        assert "Write code" in payload["effective_input"]


    def test_execution_exception_posts_structured_failure_result(self, odin_dirs, config_with_mock):
        """When harness crashes, exec_task records a structured FAILED execution_result."""
        orch = Orchestrator(config=config_with_mock)

        task = orch.task_mgr.create_task(
            title="Crash task",
            description="Trigger crash",
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        captured_payloads = []
        original_record = orch.task_mgr.record_execution_result

        def capture_record(task_id, execution_result, status, actor_email):
            captured_payloads.append((execution_result, status))
            return original_record(task_id, execution_result, status, actor_email)

        orch.task_mgr.record_execution_result = capture_record

        async def boom(self, prompt, context):
            raise RuntimeError("subprocess crashed")

        with patch("odin.harnesses.mock.MockHarness.execute", new=boom):
            with pytest.raises(RuntimeError):
                asyncio.run(orch.exec_task(task.id))

        assert len(captured_payloads) == 1
        payload, status = captured_payloads[0]
        assert status == TaskStatus.FAILED
        assert payload["success"] is False
        assert payload["failure_type"] == "agent_execution_failure"
        assert payload["failure_reason"].startswith("RuntimeError")
        assert payload["failure_origin"] == "orchestrator:task_execution"

    def test_debug_output_truncated_at_8000(self, odin_dirs, config_with_mock):
        """Debug comments truncate content to 8000 chars."""
        orch = Orchestrator(config=config_with_mock)

        # Create task with a very long description
        long_desc = "x" * 10000
        task = orch.task_mgr.create_task(
            title="Long task",
            description=long_desc,
            spec_id=None,
        )
        orch.task_mgr.assign_task(task.id, "mock")

        result = asyncio.run(orch.exec_task(task.id))

        comments = orch.task_mgr.get_comments(task.id)
        input_comments = [
            c for c in comments
            if "debug:effective_input" in c.get("attachments", [])
        ]
        assert len(input_comments) == 1
        # The "Effective input (with upstream context):\n\n" prefix + 8000 chars of content
        # Total should be under ~8050
        assert len(input_comments[0]["content"]) < 8100
