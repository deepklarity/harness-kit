"""End-to-end test: mock harness → orchestrator → comment posted to TaskIt.

Tags: [mock] — no real HTTP, no real LLM.

Tests the full pipeline:
  MockHarness.execute() → _parse_envelope() → _compose_comment()
  → TaskManager.add_comment() → TaskItBackend.add_comment() → POST /tasks/:id/comments/

Verifies actor identity, metrics, and content arrive correctly.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from odin.backends.taskit import TaskItBackend
from odin.harnesses.mock import MockHarness
from odin.models import AgentConfig, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.manager import TaskManager
from odin.taskit.models import Task, TaskStatus


class TestE2ECommentPipeline:
    """Full pipeline: mock exec → compose comment → post to TaskIt backend."""

    def test_mock_harness_through_orchestrator_compose(self):
        """Simulate what the orchestrator does after getting a mock result."""
        harness = MockHarness(AgentConfig(enabled=True))
        result = asyncio.run(harness.execute("Write a poem", {}))

        # Parse envelope (same as orchestrator does)
        clean_output, parsed_success, summary = Orchestrator._parse_envelope(
            result.output or ""
        )
        assert parsed_success is True
        assert summary == "Mock task completed successfully."

        # Compose comment (same as orchestrator does)
        comment = Orchestrator._compose_comment("Completed", result, summary)

        # Should have metrics line + summary
        assert "Completed in" in comment
        assert "tokens" in comment
        assert "Mock task completed successfully." in comment

    def test_full_pipeline_posts_comment_to_taskit(self, tmp_path):
        """End-to-end: mock harness → orchestrator → POST comment to TaskIt."""
        captured_requests = []

        def handler(request):
            url = str(request.url)
            method = request.method

            # Task update (status change)
            if "/tasks/42/" in url and method == "PUT":
                return httpx.Response(200, json={
                    "id": 42, "title": "Test", "status": "DONE",
                })

            # Load task
            if "/tasks/42" in url and method == "GET":
                return httpx.Response(200, json={
                    "id": 42, "title": "Write poem", "description": "Test",
                    "status": "TODO", "assignee": None, "depends_on": [],
                    "metadata": {"selected_model": "MiniMax-M2.5"},
                })

            # POST comment
            if "/tasks/42/comments/" in url and method == "POST":
                body = json.loads(request.content)
                captured_requests.append(body)
                return httpx.Response(201, json={"id": 1, **body})

            return httpx.Response(200, json=[])

        backend = TaskItBackend(
            base_url="http://localhost:8000", board_id=1,
            created_by="odin@harness.kit",
        )
        backend._client = httpx.Client(
            base_url="http://localhost:8000", timeout=30,
            transport=httpx.MockTransport(handler),
        )

        mgr = TaskManager(str(tmp_path), backend=backend)

        # Simulate what orchestrator does after execution
        harness = MockHarness(AgentConfig(enabled=True))
        result = asyncio.run(harness.execute("Write a poem", {}))

        clean_output, parsed_success, summary = Orchestrator._parse_envelope(
            result.output or ""
        )
        comment_text = Orchestrator._compose_comment(
            "Completed", result, summary or "Completed successfully"
        )

        # Post comment (like orchestrator does)
        mgr.add_comment("42", "minimax", comment_text, model_name="MiniMax-M2.5")

        # Verify the comment was posted correctly
        assert len(captured_requests) == 1
        posted = captured_requests[0]
        assert posted["author_email"] == "minimax+MiniMax-M2.5@odin.agent"
        assert posted["author_label"] == "minimax (MiniMax-M2.5)"
        assert "Completed in" in posted["content"]
        assert "tokens" in posted["content"]
        assert "Mock task completed successfully." in posted["content"]

    def test_failed_task_posts_failure_comment(self, tmp_path):
        """Failed tasks post a failure comment with metrics."""
        captured_requests = []

        def handler(request):
            url = str(request.url)
            if "/tasks/99/comments/" in url and request.method == "POST":
                body = json.loads(request.content)
                captured_requests.append(body)
                return httpx.Response(201, json={"id": 1})
            if "/tasks/99" in url and request.method == "GET":
                return httpx.Response(200, json={
                    "id": 99, "title": "Broken task", "description": "",
                    "status": "FAILED", "assignee": None, "depends_on": [],
                    "metadata": {},
                })
            return httpx.Response(200, json=[])

        backend = TaskItBackend(
            base_url="http://localhost:8000", board_id=1,
            created_by="odin@harness.kit",
        )
        backend._client = httpx.Client(
            base_url="http://localhost:8000", timeout=30,
            transport=httpx.MockTransport(handler),
        )

        mgr = TaskManager(str(tmp_path), backend=backend)

        # Simulate a failed result
        result = TaskResult(
            success=False,
            error="syntax error in generated file",
            duration_ms=23100.0,
            metadata={
                "usage": {
                    "total_tokens": 15200,
                    "input_tokens": 10000,
                    "output_tokens": 5200,
                }
            },
        )
        comment = Orchestrator._compose_comment(
            "Failed", result, "Error: syntax error in generated Python file."
        )
        mgr.add_comment("99", "gemini", comment, model_name="gemini-2.0")

        assert len(captured_requests) == 1
        posted = captured_requests[0]
        assert posted["author_email"] == "gemini+gemini-2.0@odin.agent"
        assert "Failed in 23.1s" in posted["content"]
        assert "15,200 tokens" in posted["content"]
        assert "syntax error" in posted["content"]

    def test_odin_system_comment_uses_harness_kit_email(self, tmp_path):
        """Odin's own comments (e.g. 'Stopped by user') use odin@harness.kit."""
        captured_requests = []

        def handler(request):
            url = str(request.url)
            if "/comments/" in url and request.method == "POST":
                captured_requests.append(json.loads(request.content))
                return httpx.Response(201, json={"id": 1})
            if "/tasks/" in url and request.method == "GET":
                return httpx.Response(200, json={
                    "id": 1, "title": "T", "description": "",
                    "status": "IN_PROGRESS", "assignee": None,
                    "depends_on": [], "metadata": {},
                })
            return httpx.Response(200, json=[])

        backend = TaskItBackend(
            base_url="http://localhost:8000", board_id=1,
            created_by="odin@harness.kit",
        )
        backend._client = httpx.Client(
            base_url="http://localhost:8000", timeout=30,
            transport=httpx.MockTransport(handler),
        )

        mgr = TaskManager(str(tmp_path), backend=backend)
        mgr.add_comment("1", "odin", "Stopped by user")

        assert len(captured_requests) == 1
        assert captured_requests[0]["author_email"] == "odin@harness.kit"
        assert captured_requests[0]["author_label"] == "odin"
