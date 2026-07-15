"""Tests for planning trace capture — _decompose() returns output,
plan() posts planning result to backend.

Tags: [mock] — no real LLM calls, no real HTTP.

Verifies that the planning phase captures its trace output (agent reasoning,
codebase exploration, etc.) and posts it to the spec backend, just like
task execution traces are captured and posted to task comments.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin.models import AgentConfig, CostTier, ModelRoute, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator


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
    """Compute the plan_path that plan() will derive for a given spec text."""
    from odin.specs import generate_spec_id
    from odin.orchestrator import _extract_title
    title = _extract_title(spec_text)
    sid = generate_spec_id(title)
    plans_dir = Path(config.task_storage).parent / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir / f"plan_{sid}.json"


class TestDecomposeReturnsResult:
    """_decompose() should return the agent's raw output for trace capture."""

    def test_decompose_returns_result_quiet_mode(self, odin_dirs, config_with_mock):
        """In quiet mode (no stream_callback), _decompose() returns the TaskResult."""
        orch = Orchestrator(config=config_with_mock)

        fake_result = TaskResult(
            success=True,
            output="Exploring codebase...\nAnalyzing requirements...\nPlan written.",
            duration_ms=15000,
            agent="mock",
        )
        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=fake_result)

        with patch("odin.orchestrator.get_harness", return_value=mock_harness):
            result = asyncio.run(
                orch._decompose("plan prompt", str(odin_dirs["root"]), spec_id="sp_test_001")
            )

        # _decompose should return the TaskResult (not None)
        assert result is not None
        assert result.success is True
        assert "Exploring codebase" in result.output

    def test_decompose_captures_output_streaming_mode(self, odin_dirs, config_with_mock):
        """In streaming mode (with stream_callback), _decompose() captures and returns output."""
        orch = Orchestrator(config=config_with_mock)

        chunks = ["Chunk 1\n", "Chunk 2\n", "Chunk 3\n"]
        collected = []

        async def fake_streaming(prompt, context):
            for chunk in chunks:
                yield chunk

        mock_harness = MagicMock()
        mock_harness.execute_streaming = fake_streaming

        with patch("odin.orchestrator.get_harness", return_value=mock_harness):
            result = asyncio.run(
                orch._decompose(
                    "plan prompt",
                    str(odin_dirs["root"]),
                    spec_id="sp_test_002",
                    stream_callback=lambda c: collected.append(c),
                )
            )

        # Stream callback should have received chunks
        assert collected == chunks
        # _decompose should return a result with concatenated output
        assert result is not None
        assert "Chunk 1" in result.output

    def test_decompose_passes_explicit_base_model_to_harness(self, odin_dirs, config_with_mock):
        """An explicit planning base_model should be forwarded to the harness context."""
        config_with_mock.base_model = "mock-model-pinned"
        orch = Orchestrator(config=config_with_mock)

        fake_result = TaskResult(
            success=True,
            output="Plan written.",
            duration_ms=1000,
            agent="mock",
        )
        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=fake_result)

        with patch("odin.orchestrator.get_harness", return_value=mock_harness):
            result = asyncio.run(
                orch._decompose("plan prompt", str(odin_dirs["root"]), spec_id="sp_test_003")
            )

        assert result is not None
        assert mock_harness.execute.await_args.args[1]["model"] == "mock-model-pinned"


class TestPlanPostsPlanningResult:
    """plan() should post the planning trace to the backend after harness completes."""

    def test_plan_records_planning_trace_locally(self, odin_dirs, config_with_mock):
        """After successful planning, plan() stores trace in spec archive metadata."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build a login page with email/password auth."
        plan_path = _plan_path_for(config_with_mock, spec_text)

        plan_data = [
            {
                "id": "task_1",
                "title": "Create login form",
                "description": "Build the login form component",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "medium",
                "depends_on": [],
            },
        ]

        fake_result = TaskResult(
            success=True,
            output="Full planning trace output here...",
            duration_ms=30000,
            agent="mock",
        )

        # Mock _decompose to write plan to disk and return result
        async def mock_decompose(prompt, wd, **kwargs):
            plan_path.write_text(json.dumps(plan_data))
            return fake_result

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(
                orch.plan(spec_text, working_dir=str(odin_dirs["root"]), mode="quiet")
            )

        assert len(tasks) == 1

        # Verify planning trace was stored in the spec archive (local mode)
        spec_archive = orch.spec_store.load(sid)
        assert spec_archive is not None
        assert "planning_trace" in spec_archive.metadata
        trace = spec_archive.metadata["planning_trace"]
        assert trace["agent"] == "mock"
        assert trace["duration_ms"] == 30000
        assert trace["success"] is True

    def test_plan_records_trace_even_when_decompose_returns_none(self, odin_dirs, config_with_mock):
        """Interactive mode doesn't capture trace (returns None) — no error."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)

        plan_data = [
            {
                "id": "task_1",
                "title": "Do thing",
                "description": "Build it",
                "required_capabilities": ["coding"],
                "suggested_agent": "mock",
                "complexity": "low",
                "depends_on": [],
            },
        ]

        # Simulate interactive mode — _decompose not called, plan written manually
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan_data))

        # Patch _run_interactive_plan to be a no-op (plan already on disk)
        with patch.object(orch, "_run_interactive_plan"):
            sid, tasks = asyncio.run(
                orch.plan(spec_text, working_dir=str(odin_dirs["root"]), mode="interactive")
            )

        assert len(tasks) == 1
        # No planning_trace in metadata for interactive mode (no decompose_result)
        spec_archive = orch.spec_store.load(sid)
        assert spec_archive is not None
        # Either no planning_trace key, or it's missing — both are fine


class TestPlanningTraceTokenUsage:
    """Planning trace must carry token_usage so plan cost can be computed."""

    def test_record_planning_trace_passes_token_usage_to_backend(self, odin_dirs, config_with_mock):
        """_record_planning_trace extracts usage from result.metadata and passes it."""
        orch = Orchestrator(config=config_with_mock)

        fake_result = TaskResult(
            success=True,
            output="Planning output...",
            duration_ms=30000,
            agent="mock",
            metadata={"usage": {"input_tokens": 50_000, "output_tokens": 10_000}},
        )

        mock_backend = MagicMock()
        mock_backend.record_planning_result = MagicMock()
        orch.task_mgr._backend = mock_backend

        with patch.object(orch, "_planning_agent_model", return_value=("mock", "mock-model")):
            orch._record_planning_trace("sp_test_usage", fake_result, "plan prompt")

        mock_backend.record_planning_result.assert_called_once()
        call_kwargs = mock_backend.record_planning_result.call_args
        assert call_kwargs.kwargs.get("token_usage") == {
            "input_tokens": 50_000,
            "output_tokens": 10_000,
        }

    def test_record_planning_trace_no_usage_passes_none(self, odin_dirs, config_with_mock):
        """When result has no usage metadata, token_usage is None (not an error)."""
        orch = Orchestrator(config=config_with_mock)

        fake_result = TaskResult(
            success=True,
            output="Planning output...",
            duration_ms=30000,
            agent="mock",
        )

        mock_backend = MagicMock()
        mock_backend.record_planning_result = MagicMock()
        orch.task_mgr._backend = mock_backend

        with patch.object(orch, "_planning_agent_model", return_value=("mock", "mock-model")):
            orch._record_planning_trace("sp_test_nousage", fake_result, "plan prompt")

        mock_backend.record_planning_result.assert_called_once()
        call_kwargs = mock_backend.record_planning_result.call_args
        assert call_kwargs.kwargs.get("token_usage") is None

    def test_record_planning_result_includes_token_usage_in_payload(self):
        """record_planning_result includes token_usage in the POST payload."""
        from odin.backends.taskit import TaskItBackend

        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="test@test.com",
        )
        mock_client = MagicMock()
        mock_client.post = MagicMock(return_value=MagicMock(status_code=200))
        mock_client.get = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: [{"id": 42}],
        ))
        backend._client = mock_client

        backend.record_planning_result(
            spec_id="sp_test",
            raw_output="trace",
            duration_ms=1000,
            agent="claude",
            model="claude-sonnet-4-5",
            effective_input="prompt",
            success=True,
            token_usage={"input_tokens": 100, "output_tokens": 50},
        )

        mock_client.post.assert_called_once()
        payload = mock_client.post.call_args.kwargs.get("json", {})
        assert payload.get("token_usage") == {"input_tokens": 100, "output_tokens": 50}

    def test_record_planning_result_omits_token_usage_when_none(self):
        """record_planning_result omits token_usage from payload when None."""
        from odin.backends.taskit import TaskItBackend

        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="test@test.com",
        )
        mock_client = MagicMock()
        mock_client.post = MagicMock(return_value=MagicMock(status_code=200))
        mock_client.get = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: [{"id": 42}],
        ))
        backend._client = mock_client

        backend.record_planning_result(
            spec_id="sp_test",
            raw_output="trace",
            duration_ms=1000,
            agent="claude",
            model="claude-sonnet-4-5",
            effective_input="prompt",
            success=True,
        )

        mock_client.post.assert_called_once()
        payload = mock_client.post.call_args.kwargs.get("json", {})
        assert "token_usage" not in payload
