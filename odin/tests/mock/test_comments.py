"""Tests for comment bridge and metrics composition.

Tags: [mock] — no real HTTP calls, no LLM.

Tests:
  - Orchestrator._extract_agent_text() structured output parsing
  - TaskItBackend.add_comment() HTTP call
  - TaskManager._format_actor_email() identity formatting
  - TaskManager._format_actor_label() display labels
  - Orchestrator._compose_comment() metrics-inline formatting
  - TaskManager.add_comment() routing through backend
"""

import json

import httpx
import pytest

from odin.backends.taskit import TaskItBackend
from odin.models import TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.manager import TaskManager
from odin.taskit.models import Task, TaskStatus


# ── Orchestrator._extract_agent_text() ────────────────────────────────


class TestExtractAgentText:
    """Verify structured CLI output is correctly parsed to plain text."""

    def test_plain_text_passthrough(self):
        """Non-JSON output is returned as-is."""
        raw = "Mock execution completed.\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone."
        assert Orchestrator._extract_agent_text(raw) == raw

    def test_empty_input(self):
        assert Orchestrator._extract_agent_text("") == ""
        assert Orchestrator._extract_agent_text("  ") == "  "

    def test_claude_code_jsonl(self):
        """Claude Code JSONL: extract text from {"type":"text"} events."""
        lines = [
            json.dumps({"type": "step_start", "timestamp": 1000, "sessionID": "s1", "part": {"id": "p1", "type": "step-start"}}),
            json.dumps({"type": "tool_use", "timestamp": 1001, "sessionID": "s1", "part": {"id": "p2", "tool": "write", "state": {"status": "completed"}}}),
            json.dumps({"type": "step_finish", "timestamp": 1002, "sessionID": "s1", "part": {"id": "p3", "type": "step-finish", "reason": "tool-calls", "tokens": {"total": 100}}}),
            json.dumps({"type": "text", "timestamp": 1003, "sessionID": "s1", "part": {"id": "p4", "text": "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCreated the file.", "time": {"start": 1003, "end": 1003}}}),
            json.dumps({"type": "step_finish", "timestamp": 1004, "sessionID": "s1", "part": {"id": "p5", "type": "step-finish", "reason": "stop", "tokens": {"total": 200}}}),
        ]
        raw = "\n".join(lines)
        extracted = Orchestrator._extract_agent_text(raw)
        assert extracted == "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCreated the file."

    def test_claude_code_envelope_parses_cleanly(self):
        """Full pipeline: JSONL → extract → parse envelope → clean summary."""
        lines = [
            json.dumps({"type": "step_start", "timestamp": 1000, "sessionID": "s1", "part": {}}),
            json.dumps({"type": "text", "timestamp": 1001, "sessionID": "s1", "part": {"text": "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nAssembled final HTML.", "time": {"start": 1001, "end": 1001}}}),
            json.dumps({"type": "step_finish", "timestamp": 1002, "sessionID": "s1", "part": {"type": "step-finish", "tokens": {"total": 14024, "input": 81, "output": 150}}}),
        ]
        raw = "\n".join(lines)
        agent_text = Orchestrator._extract_agent_text(raw)
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Assembled final HTML."

    def test_qwen_cli_result_field(self):
        """Qwen CLI: extract text from {"type":"result","subtype":"success","result":"..."}."""
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "abc", "model": "coder-model"}),
            json.dumps({"type": "result", "subtype": "success", "session_id": "abc",
                         "result": "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCreated qwen.html.",
                         "usage": {"input_tokens": 25540, "output_tokens": 276, "total_tokens": 25816},
                         "permission_denials": []}),
        ]
        raw = "\n".join(lines)
        agent_text = Orchestrator._extract_agent_text(raw)
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Created qwen.html."

    def test_multiple_text_events_concatenated(self):
        """Multiple text events are joined with newlines."""
        lines = [
            json.dumps({"type": "text", "part": {"text": "First part."}}),
            json.dumps({"type": "step_finish", "part": {}}),
            json.dumps({"type": "text", "part": {"text": "Second part.\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone."}}),
        ]
        raw = "\n".join(lines)
        agent_text = Orchestrator._extract_agent_text(raw)
        assert "First part." in agent_text
        assert "Second part." in agent_text
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Done."

    def test_qwen_with_warning_prefix(self):
        """Qwen CLI: non-JSON warning line before JSON events."""
        lines = [
            "Unsupported Qwen OAuth model 'qwen3-coder', falling back to 'coder-model'.",
            json.dumps({"type": "system", "subtype": "init", "session_id": "abc", "model": "coder-model"}),
            json.dumps({"type": "result", "subtype": "success", "session_id": "abc",
                         "result": "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCreated qwen.html.",
                         "usage": {"total_tokens": 25816},
                         "permission_denials": []}),
        ]
        raw = "\n".join(lines)
        agent_text = Orchestrator._extract_agent_text(raw)
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Created qwen.html."

    def test_pure_plain_text_no_json(self):
        """Plain text with no JSON at all passes through."""
        raw = "some plain text\n-------ODIN-STATUS-------\nSUCCESS"
        extracted = Orchestrator._extract_agent_text(raw)
        assert extracted == raw

    def test_gemini_stream_json(self):
        """Gemini stream-json: {"type":"text","text":"..."} (no nested part)."""
        lines = [
            json.dumps({"type": "text", "text": "Working on the task."}),
            json.dumps({"type": "tool_use", "timestamp": "2026-02-19T21:40:59.907Z",
                         "tool_name": "write_file", "tool_id": "write_file-123",
                         "parameters": {"content": "<h2>Table B</h2>"}}),
            json.dumps({"type": "text", "text": "\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nWrote table HTML."}),
        ]
        raw = "\n".join(lines)
        agent_text = Orchestrator._extract_agent_text(raw)
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Wrote table HTML."

    def test_gemini_tool_use_only_no_text(self):
        """Gemini output with only tool_use events and no text — returns raw."""
        lines = [
            json.dumps({"type": "tool_use", "tool_name": "write_file", "parameters": {"content": "x"}}),
        ]
        raw = "\n".join(lines)
        extracted = Orchestrator._extract_agent_text(raw)
        # No text events found, falls back to raw output
        assert extracted == raw

    def test_content_block_delta(self):
        """Claude stream-json delta format — {"type":"content_block_delta"}."""
        lines = [
            json.dumps({"type": "content_block_delta", "delta": {"text": "Hello "}}),
            json.dumps({"type": "content_block_delta", "delta": {"text": "world."}}),
        ]
        raw = "\n".join(lines)
        extracted = Orchestrator._extract_agent_text(raw)
        assert extracted == "Hello \nworld."

    def test_result_type_event(self):
        """Stream-json result event — {"type":"result","result":"..."}."""
        lines = [
            json.dumps({"type": "result", "result": "Final answer."}),
        ]
        raw = "\n".join(lines)
        extracted = Orchestrator._extract_agent_text(raw)
        assert extracted == "Final answer."


# ── Helpers ───────────────────────────────────────────────────────────


def _make_backend_with_transport(handler):
    backend = TaskItBackend(
        base_url="http://localhost:8000", board_id=1, created_by="odin@harness.kit"
    )
    backend._client = httpx.Client(
        base_url="http://localhost:8000",
        timeout=30,
        transport=httpx.MockTransport(handler),
    )
    return backend


# ── TaskItBackend.add_comment() ───────────────────────────────────────


class TestTaskItBackendAddComment:
    """Verify add_comment() POSTs to /tasks/:id/comments/."""

    def test_posts_comment_to_correct_url(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        backend = _make_backend_with_transport(handler)
        backend.add_comment(
            task_id="42",
            author_email="minimax+MiniMax-M2.5@odin.agent",
            content="Completed in 12.3s\n\nAssembled HTML.",
            author_label="minimax (MiniMax-M2.5)",
        )

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/tasks/42/comments/")
        assert captured["body"]["author_email"] == "minimax+MiniMax-M2.5@odin.agent"
        assert captured["body"]["content"] == "Completed in 12.3s\n\nAssembled HTML."
        assert captured["body"]["author_label"] == "minimax (MiniMax-M2.5)"

    def test_includes_attachments_when_provided(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        backend = _make_backend_with_transport(handler)
        backend.add_comment(
            task_id="42",
            author_email="test@odin.agent",
            content="See attached.",
            attachments=[{"type": "file", "path": "/tmp/out.log"}],
        )

        assert captured["body"]["attachments"] == [{"type": "file", "path": "/tmp/out.log"}]

    def test_omits_attachments_when_none(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        backend = _make_backend_with_transport(handler)
        backend.add_comment(
            task_id="42",
            author_email="test@odin.agent",
            content="Hello.",
        )

        assert "attachments" not in captured["body"]

    def test_raises_on_http_error(self):
        def handler(request):
            return httpx.Response(500, text="Internal Server Error")

        backend = _make_backend_with_transport(handler)
        with pytest.raises(httpx.HTTPStatusError):
            backend.add_comment(
                task_id="42",
                author_email="test@odin.agent",
                content="Hello.",
            )


# ── Actor identity formatting ────────────────────────────────────────


class TestActorIdentity:
    """TaskManager._format_actor_email() and _format_actor_label()."""

    def test_agent_with_model(self):
        email = TaskManager._format_actor_email("minimax", "MiniMax-M2.5")
        assert email == "minimax+MiniMax-M2.5@odin.agent"

    def test_agent_without_model(self):
        email = TaskManager._format_actor_email("gemini")
        assert email == "gemini@odin.agent"

    def test_odin_without_model(self):
        email = TaskManager._format_actor_email("odin")
        assert email == "odin@harness.kit"

    def test_odin_with_model(self):
        """When odin has a model (e.g. planning), uses agent format."""
        email = TaskManager._format_actor_email("odin", "sonnet-4-5")
        assert email == "odin+sonnet-4-5@odin.agent"

    def test_label_with_model(self):
        label = TaskManager._format_actor_label("claude", "sonnet-4-5")
        assert label == "claude (sonnet-4-5)"

    def test_label_without_model(self):
        label = TaskManager._format_actor_label("odin")
        assert label == "odin"


# ── Metrics-inline comment composition ────────────────────────────────


class TestComposeComment:
    """Orchestrator._compose_comment() formats metrics into comments."""

    def test_with_duration_and_tokens(self):
        result = TaskResult(
            success=True,
            output="done",
            duration_ms=12345.6,
            metadata={
                "usage": {
                    "total_tokens": 8420,
                    "input_tokens": 5200,
                    "output_tokens": 3220,
                }
            },
        )
        comment = Orchestrator._compose_comment("Completed", result, "Assembled final HTML.")
        assert comment == (
            "Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)\n\n"
            "Assembled final HTML."
        )

    def test_with_duration_only(self):
        result = TaskResult(success=True, output="done", duration_ms=45000.0)
        comment = Orchestrator._compose_comment("Completed", result, "Done.")
        assert comment == "Completed in 45.0s\n\nDone."

    def test_with_no_metrics(self):
        result = TaskResult(success=True, output="done")
        comment = Orchestrator._compose_comment("Completed", result, "All good.")
        assert comment == "All good."

    def test_failed_verb(self):
        result = TaskResult(
            success=False, error="syntax error", duration_ms=23100.0,
            metadata={"usage": {"total_tokens": 15200, "input_tokens": 10000, "output_tokens": 5200}},
        )
        comment = Orchestrator._compose_comment("Failed", result, "Error: syntax error in Python.")
        assert comment.startswith("Failed in 23.1s")
        assert "15,200 tokens" in comment

    def test_prompt_completion_token_keys(self):
        """Some providers use prompt_tokens/completion_tokens instead of input/output."""
        result = TaskResult(
            success=True,
            output="done",
            duration_ms=5000.0,
            metadata={
                "usage": {
                    "total_tokens": 1000,
                    "prompt_tokens": 700,
                    "completion_tokens": 300,
                }
            },
        )
        comment = Orchestrator._compose_comment("Completed", result, "Summary.")
        assert "700 in / 300 out" in comment

    def test_total_tokens_without_breakdown(self):
        """When only total_tokens is available, no breakdown shown."""
        result = TaskResult(
            success=True,
            output="done",
            duration_ms=3000.0,
            metadata={"usage": {"total_tokens": 500}},
        )
        comment = Orchestrator._compose_comment("Completed", result, "Done.")
        assert "500 tokens" in comment
        assert "in /" not in comment


# ── TaskManager.add_comment() routing ─────────────────────────────────


class TestTaskManagerCommentRouting:
    """TaskManager.add_comment() routes through backend when available."""

    def test_routes_through_backend_with_formatted_identity(self, tmp_path):
        captured = {}

        def handler(request):
            if request.method == "POST" and "/comments/" in str(request.url):
                captured["body"] = json.loads(request.content)
                return httpx.Response(201, json={"id": 1})
            # load_task after comment
            if request.method == "GET" and "/tasks/" in str(request.url):
                return httpx.Response(200, json={
                    "id": 42, "title": "Test", "description": "",
                    "status": "DONE", "assignee": None, "depends_on": [],
                    "metadata": {},
                })
            return httpx.Response(404)

        backend = _make_backend_with_transport(handler)
        mgr = TaskManager(str(tmp_path), backend=backend)
        mgr.add_comment(
            task_id="42",
            author="minimax",
            content="Assembled HTML.",
            model_name="MiniMax-M2.5",
        )

        assert captured["body"]["author_email"] == "minimax+MiniMax-M2.5@odin.agent"
        assert captured["body"]["author_label"] == "minimax (MiniMax-M2.5)"
        assert captured["body"]["content"] == "Assembled HTML."

    def test_falls_back_to_local_comment_without_backend(self, tmp_path):
        """Without a backend, comments are stored locally on the task."""
        mgr = TaskManager(str(tmp_path))
        # Create a task first
        task = mgr.create_task(title="Test", description="Desc")
        result = mgr.add_comment(task.id, "odin", "Hello")
        assert result is not None
        assert len(result.comments) == 1
        assert result.comments[0].content == "Hello"
