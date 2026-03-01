"""Unit tests for Codex JSON extraction in base.py and orchestrator.py.

Tests:
- extract_text_from_line() handles Codex item.completed with agent_message
- extract_text_from_line() skips Codex item.completed with reasoning type
- extract_text_from_line() skips Codex thread.started, turn.started, turn.completed
- extract_text_from_stream() extracts clean text from full Codex output
- Orchestrator._extract_agent_text() handles Codex item.completed format
"""

import json

from odin.harnesses.base import extract_text_from_line, extract_text_from_stream
from odin.orchestrator import Orchestrator


CODEX_STREAM = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "thread_abc123"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "Let me think about this..."}}),
    json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "## Task Summary\nThe task was completed successfully."}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 200}}),
])


class TestExtractTextFromLineCodex:
    """extract_text_from_line() Codex format handling."""

    def test_agent_message_extracted(self):
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hello world."}})
        assert extract_text_from_line(line) == "Hello world."

    def test_reasoning_skipped(self):
        line = json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking..."}})
        assert extract_text_from_line(line) == ""

    def test_thread_started_skipped(self):
        line = json.dumps({"type": "thread.started", "thread_id": "t1"})
        assert extract_text_from_line(line) == ""

    def test_turn_started_skipped(self):
        line = json.dumps({"type": "turn.started"})
        assert extract_text_from_line(line) == ""

    def test_turn_completed_skipped(self):
        line = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100}})
        assert extract_text_from_line(line) == ""

    def test_item_completed_missing_item_field(self):
        line = json.dumps({"type": "item.completed"})
        assert extract_text_from_line(line) == ""

    def test_item_completed_empty_text(self):
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": ""}})
        assert extract_text_from_line(line) == ""


class TestExtractTextFromStreamCodex:
    """extract_text_from_stream() with full Codex output."""

    def test_extracts_agent_message_from_codex_stream(self):
        result = extract_text_from_stream(CODEX_STREAM)
        assert result == "## Task Summary\nThe task was completed successfully."

    def test_excludes_reasoning_from_codex_stream(self):
        result = extract_text_from_stream(CODEX_STREAM)
        assert "Let me think about this" not in result


class TestExtractAgentTextCodex:
    """Orchestrator._extract_agent_text() Codex format handling."""

    def test_codex_item_completed_agent_message(self):
        """Codex item.completed with agent_message is extracted."""
        extracted = Orchestrator._extract_agent_text(CODEX_STREAM)
        assert "## Task Summary" in extracted
        assert "completed successfully" in extracted

    def test_codex_reasoning_excluded(self):
        """Codex reasoning items are not included in extracted text."""
        extracted = Orchestrator._extract_agent_text(CODEX_STREAM)
        assert "Let me think about this" not in extracted

    def test_codex_full_pipeline_with_envelope(self):
        """Full pipeline: Codex stream -> extract -> parse envelope."""
        stream = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                "text": "Done.\n\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nFixed the bug."}}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ])
        agent_text = Orchestrator._extract_agent_text(stream)
        _, success, summary = Orchestrator._parse_envelope(agent_text)
        assert success is True
        assert summary == "Fixed the bug."
