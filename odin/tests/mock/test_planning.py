"""Tests for artifact-aware planning features.

Tags: [mock] — no real LLM calls, no real HTTP.

Verifies that the decomposition prompt includes expected_outputs and assumptions
fields, and that assumptions are posted as initial comments on tasks.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin.models import AgentConfig, CostTier, ModelRoute, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.models import Task, TaskStatus


@pytest.fixture
def config_with_mock(odin_dirs):
    """Config with mock harness for planning tests."""
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents={
            "mock": AgentConfig(
                cli_command="mock",
                capabilities=["coding", "planning"],
                cost_tier=CostTier.LOW,
                enabled=True,
            ),
        },
        model_routing=[
            ModelRoute(agent="mock", model="mock-model"),
        ],
    )


def _plan_path_for(config: OdinConfig, spec_text: str) -> Path:
    """Compute the plan_path that plan() will derive for a given spec text.

    Mirrors the logic in plan(): plans_dir / f"plan_{spec_id}.json"
    """
    from odin.specs import generate_spec_id
    from odin.orchestrator import _extract_title
    title = _extract_title(spec_text)
    sid = generate_spec_id(title)
    plans_dir = Path(config.task_storage).parent / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir / f"plan_{sid}.json"


class TestArtifactAwarePlanning:
    """Verify decomposition includes expected_outputs and assumptions."""

    def test_expected_outputs_stored_in_metadata(self, odin_dirs, config_with_mock):
        """Tasks with expected_outputs have them in metadata."""
        orch = Orchestrator(config=config_with_mock)

        # The plan the agent would write to disk
        plan_data = [
            {
                "id": "task_1",
                "title": "Generate table A",
                "description": "Create table_a.html",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "medium",
                "depends_on": [],
                "expected_outputs": ["table_a.html"],
                "assumptions": [],
            },
            {
                "id": "task_2",
                "title": "Generate table B",
                "description": "Create table_b.html",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "medium",
                "depends_on": [],
                "expected_outputs": ["table_b.html"],
                "assumptions": [],
            },
            {
                "id": "task_3",
                "title": "Merge tables",
                "description": "Combine table_a.html and table_b.html into combined.html",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "low",
                "depends_on": ["task_1", "task_2"],
                "expected_outputs": ["combined.html"],
                "assumptions": [
                    "table_a.html exists from task_1",
                    "table_b.html exists from task_2",
                ],
            },
        ]

        spec_text = "Build two HTML tables and merge them"
        plan_path = _plan_path_for(config_with_mock, spec_text)

        # Mock _decompose to write plan to disk (simulating what the agent does)
        async def mock_decompose(prompt, wd, **kwargs):
            plan_path.write_text(json.dumps(plan_data))

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(spec_text, quick=True))

        assert len(tasks) == 3

        # Task 1: expected_outputs stored
        assert tasks[0].metadata["expected_outputs"] == ["table_a.html"]
        assert "assumptions" not in tasks[0].metadata  # empty list not stored

        # Task 3: both expected_outputs and assumptions stored
        assert tasks[2].metadata["expected_outputs"] == ["combined.html"]
        assert tasks[2].metadata["assumptions"] == [
            "table_a.html exists from task_1",
            "table_b.html exists from task_2",
        ]

    def test_assumptions_posted_as_initial_comment(self, odin_dirs, config_with_mock):
        """Tasks with assumptions get an initial comment with planning assumptions."""
        orch = Orchestrator(config=config_with_mock)

        plan_data = [
            {
                "id": "task_1",
                "title": "Task with assumptions",
                "description": "Do stuff",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "medium",
                "depends_on": [],
                "expected_outputs": ["output.txt"],
                "assumptions": [
                    "input.txt exists",
                    "Python 3.10+ available",
                ],
            },
        ]

        spec_text = "Do stuff"
        plan_path = _plan_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            plan_path.write_text(json.dumps(plan_data))

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(spec_text, quick=True))

        # Check that the assumption comment was posted
        assert len(tasks) == 1
        task = tasks[0]
        comments = orch.task_mgr.get_comments(task.id)
        assert len(comments) == 1
        comment = comments[0]
        assert "Planning assumptions:" in comment["content"]
        assert "- input.txt exists" in comment["content"]
        assert "- Python 3.10+ available" in comment["content"]

    def test_no_assumption_comment_when_empty(self, odin_dirs, config_with_mock):
        """Tasks without assumptions don't get a planning comment."""
        orch = Orchestrator(config=config_with_mock)

        plan_data = [
            {
                "id": "task_1",
                "title": "Simple task",
                "description": "Do it",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "low",
                "depends_on": [],
            },
        ]

        spec_text = "Simple task"
        plan_path = _plan_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            plan_path.write_text(json.dumps(plan_data))

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(spec_text, quick=True))

        comments = orch.task_mgr.get_comments(tasks[0].id)
        assert len(comments) == 0

    def test_plan_prompt_includes_artifact_rules(self, odin_dirs, config_with_mock):
        """The unified plan prompt includes ARTIFACT COORDINATION section."""
        orch = Orchestrator(config=config_with_mock)

        # Test the prompt content directly via _build_plan_prompt
        prompt = orch._build_plan_prompt(
            spec="Test spec",
            plan_path="/tmp/test_plan.json",
            available_agents=[],
        )
        assert "ARTIFACT COORDINATION" in prompt
        assert "expected_outputs" in prompt
        assert "PLANNING PHILOSOPHY" in prompt

    def test_plan_prompt_includes_plan_path(self, odin_dirs, config_with_mock):
        """The unified plan prompt includes the plan_path for file-based output."""
        orch = Orchestrator(config=config_with_mock)

        prompt = orch._build_plan_prompt(
            spec="Test spec",
            plan_path="/tmp/plans/plan_sp_abc123.json",
            available_agents=[],
        )
        assert "/tmp/plans/plan_sp_abc123.json" in prompt
        assert "Write your final plan as a JSON array to:" in prompt

    def test_plan_prompt_includes_quick_instruction(self, odin_dirs, config_with_mock):
        """Quick mode adds the no-exploration instruction."""
        orch = Orchestrator(config=config_with_mock)

        prompt = orch._build_plan_prompt(
            spec="Test spec",
            plan_path="/tmp/test.json",
            available_agents=[],
            quick=True,
        )
        assert "QUICK MODE" in prompt
        assert "Do NOT explore or read the codebase" in prompt
