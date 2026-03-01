"""Tests for stream_json_is_complete — pure logic, no I/O."""

import json

import pytest

from odin.harnesses.base import stream_json_is_complete


class TestStreamJsonIsComplete:
    """Verify detection of result lines in stream-json output tails."""

    def test_result_line_present(self):
        tail = (
            '{"type":"content_block_delta","delta":{"text":"hello"}}\n'
            '{"type":"result","result":"Done."}\n'
        )
        assert stream_json_is_complete(tail) is True

    def test_only_deltas(self):
        tail = (
            '{"type":"content_block_delta","delta":{"text":"hello"}}\n'
            '{"type":"content_block_delta","delta":{"text":" world"}}\n'
        )
        assert stream_json_is_complete(tail) is False

    def test_empty_string(self):
        assert stream_json_is_complete("") is False

    def test_blank_lines_only(self):
        assert stream_json_is_complete("\n\n\n") is False

    def test_malformed_json(self):
        tail = '{"type":"result", broken json\n'
        assert stream_json_is_complete(tail) is False

    def test_error_result(self):
        """Error results are still completion signals."""
        obj = {"type": "result", "is_error": True, "result": "Something failed"}
        tail = json.dumps(obj) + "\n"
        assert stream_json_is_complete(tail) is True

    def test_result_not_last_line(self):
        """Result line buried in the middle of the tail (trailing blank lines)."""
        tail = (
            '{"type":"result","result":"Done."}\n'
            "\n"
            "\n"
        )
        assert stream_json_is_complete(tail) is True

    def test_non_json_plain_text(self):
        tail = "All done!\nExiting...\n"
        assert stream_json_is_complete(tail) is False

    def test_result_type_in_non_dict(self):
        """A JSON array with 'result' shouldn't match."""
        tail = '["result"]\n'
        assert stream_json_is_complete(tail) is False

    def test_type_field_wrong_value(self):
        tail = '{"type":"message","result":"Done."}\n'
        assert stream_json_is_complete(tail) is False

    def test_real_claude_tail(self):
        """Simulate a realistic tail with many content deltas then result."""
        lines = [
            json.dumps({"type": "content_block_delta", "delta": {"text": f"word{i}"}})
            for i in range(20)
        ]
        lines.append(json.dumps({
            "type": "result",
            "subtype": "success",
            "result": "Task completed successfully.",
            "is_error": False,
            "cost_usd": 0.05,
            "duration_ms": 45000,
        }))
        tail = "\n".join(lines) + "\n"
        assert stream_json_is_complete(tail) is True
