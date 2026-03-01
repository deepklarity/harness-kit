"""Mock E2E tests for odin reflect command.

Tests the reflect_task() flow with mocked HTTP and harness calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from odin.models import TaskResult
from odin.reflection import reflect_task


MOCK_TASK_DETAIL = {
    "id": 42,
    "title": "Implement user login",
    "description": "Add JWT-based login endpoint",
    "status": "REVIEW",
    "model_name": "claude-sonnet-4-5",
    "metadata": {
        "working_dir": "/tmp/project",
        "selected_model": "claude-sonnet-4-5",
        "full_output": "Created endpoint successfully",
        "last_duration_ms": 30000,
        "last_usage": {"input_tokens": 5000, "output_tokens": 8000, "total_tokens": 13000},
    },
    "comments": [
        {"content": "Starting implementation", "comment_type": "status_update"},
        {"content": "Completed in 30s", "comment_type": "status_update"},
    ],
    "depends_on": [],
    "assignee": {"name": "claude-agent"},
}

MOCK_AGENT_OUTPUT = """### Quality Assessment
The code is well-structured.

### Slop Detection
No slop found.

### Actionable Improvements
1. Add input validation

### Agent Optimization
Model tier was appropriate.

### Verdict
PASS
Looks good overall.
"""


@pytest.fixture
def mock_http():
    """Mock HTTP calls to TaskIt API."""
    with patch("odin.reflection.httpx") as mock_requests:
        running_resp = MagicMock()
        running_resp.status_code = 200
        running_resp.json.return_value = {"status": "RUNNING"}

        detail_resp = MagicMock()
        detail_resp.status_code = 200
        detail_resp.json.return_value = MOCK_TASK_DETAIL

        complete_resp = MagicMock()
        complete_resp.status_code = 200
        complete_resp.json.return_value = {"status": "COMPLETED"}

        mock_requests.patch.side_effect = [running_resp, complete_resp]
        mock_requests.get.return_value = detail_resp

        yield mock_requests


@pytest.fixture
def mock_harness():
    """Mock harness execution returning a TaskResult."""
    with patch("odin.reflection.get_harness") as mock_get:
        harness = MagicMock()
        harness.execute = AsyncMock(return_value=TaskResult(
            success=True,
            output=MOCK_AGENT_OUTPUT,
            duration_ms=15000,
            metadata={"usage": {"input_tokens": 2000, "output_tokens": 3000}},
        ))
        mock_get.return_value = harness
        yield harness


class TestReflectTask:
    """reflect_task() orchestrates the full reflection flow."""

    def test_reflect_task_updates_report_to_running(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        first_patch_call = mock_http.patch.call_args_list[0]
        assert "/reflections/1/" in first_patch_call[0][0]
        assert first_patch_call[1]["json"]["status"] == "RUNNING"

    def test_reflect_task_gathers_context_from_api(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        mock_http.get.assert_called_once()
        assert "/tasks/42/detail/" in mock_http.get.call_args[0][0]

    def test_reflect_task_calls_harness_correctly(self, mock_http, mock_harness):
        """Harness called with (prompt, context) positional args."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        mock_harness.execute.assert_called_once()
        args = mock_harness.execute.call_args[0]
        prompt, context = args[0], args[1]
        assert "auditing a task executed by an AI agent" in prompt
        assert context["working_dir"] == "/tmp/project"
        assert context["model"] == "claude-opus-4-6"

    def test_reflect_task_submits_parsed_report(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "PASS"
        assert "well-structured" in payload["quality_assessment"]

    def test_reflect_task_report_has_correct_sections(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        for key in ("quality_assessment", "slop_detection", "improvements",
                     "agent_optimization", "verdict", "verdict_summary", "raw_output"):
            assert key in payload

    def test_reflect_task_handles_harness_exception(self, mock_http):
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(side_effect=RuntimeError("Agent crashed"))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        last_patch_call = mock_http.patch.call_args_list[-1]
        payload = last_patch_call[1]["json"]
        assert payload["status"] == "FAILED"
        assert "Agent crashed" in payload["error_message"]

    def test_reflect_task_handles_harness_failure_result(self, mock_http):
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=False, output="", error="Timeout",
            ))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        last_patch_call = mock_http.patch.call_args_list[-1]
        payload = last_patch_call[1]["json"]
        assert payload["status"] == "FAILED"
        assert payload["error_message"] == "Timeout"

    def test_reflect_task_patches_assembled_prompt(self, mock_http, mock_harness):
        """The RUNNING patch should include the assembled prompt."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        first_patch_call = mock_http.patch.call_args_list[0]
        payload = first_patch_call[1]["json"]
        assert payload["status"] == "RUNNING"
        assert "assembled_prompt" in payload
        assert "auditing a task executed by an AI agent" in payload["assembled_prompt"]
        assert "Implement user login" in payload["assembled_prompt"]

    def test_reflect_task_includes_token_usage(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["token_usage"] == {"input_tokens": 2000, "output_tokens": 3000}

    def test_reflect_task_with_custom_model_override(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="gemini-2.5-pro",
            agent="gemini", taskit_url="http://localhost:8000",
        )
        mock_harness.execute.assert_called_once()
        context = mock_harness.execute.call_args[0][1]
        assert context["model"] == "gemini-2.5-pro"
