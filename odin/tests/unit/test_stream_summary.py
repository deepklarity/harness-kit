"""Tests for run-close stream metadata extraction (task #330).

A truncated run used to surface as the generic "Likely the model truncated"
message. The stream already carries the real reason (finish_reason + token
counts); ``extract_stream_summary`` pulls it out so the failure record can
name it. Per-harness tolerant — claude and opencode streams differ, and
absence of metadata must never raise.
"""

import pytest

from odin.harnesses.base import (
    extract_stream_summary,
    format_stream_summary_note,
)


class TestExtractStreamSummary:
    """extract_stream_summary returns run-close metadata, tolerant of format."""

    def test_missing_function_is_a_defect(self):
        assert callable(extract_stream_summary), (
            "extract_stream_summary must exist so run-close records carry "
            "the real finish reason instead of a generic truncation guess"
        )

    def test_empty_input_returns_defaults_without_raising(self):
        summary = extract_stream_summary("")
        assert summary["finish_reason"] is None
        assert summary["output_tokens"] is None
        assert summary["result_emitted"] is False
        assert summary["error_events"] == []

    def test_garbage_input_never_raises(self):
        summary = extract_stream_summary("not json at all\n{broken\n<<<>>>")
        assert summary["finish_reason"] is None
        assert summary["output_tokens"] is None

    def test_opencode_truncation_names_real_reason_and_tokens(self):
        """glm/opencode stream that hit the output cap.

        The last step_finish carries reason='length' (output cap) and a low
        output token count — exactly the evidence task #314 lost.
        """
        stream = "\n".join([
            '{"type":"text","timestamp":1783710000000,"part":{"text":"work"}}',
            '{"type":"step_finish","timestamp":1783710002000,"part":{"reason":"length","tokens":{"input":1200,"output":40,"total":1240,"cache":{"read":0,"write":0}}}}',
        ])
        summary = extract_stream_summary(stream)
        assert summary["finish_reason"] == "length"
        assert summary["output_tokens"] == 40
        assert summary["input_tokens"] == 1200
        assert summary["total_tokens"] == 1240
        # last_event_timestamp captured from the stream
        assert summary["last_event_timestamp"] is not None

    def test_opencode_normal_stop_reason(self):
        stream = '{"type":"step_finish","part":{"reason":"stop","tokens":{"input":10,"output":20,"total":30}}}'
        summary = extract_stream_summary(stream)
        assert summary["finish_reason"] == "stop"
        assert summary["output_tokens"] == 20

    def test_claude_result_max_tokens(self):
        """Claude stream-json: message_delta stop_reason + modelUsage tokens."""
        stream = "\n".join([
            '{"type":"content_block_delta","delta":{"text":"partial"}}',
            '{"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
            '{"type":"result","subtype":"success","is_error":false}',
            '{"modelUsage":{"claude":{"inputTokens":1200,"outputTokens":40,"cacheReadInputTokens":0,"cacheCreationInputTokens":0}}}',
        ])
        summary = extract_stream_summary(stream)
        assert summary["finish_reason"] == "max_tokens"
        assert summary["result_emitted"] is True
        assert summary["output_tokens"] == 40

    def test_claude_result_subtype_as_reason_when_no_stop_reason(self):
        stream = '{"type":"result","subtype":"error_max_turns"}'
        summary = extract_stream_summary(stream)
        assert summary["finish_reason"] == "error_max_turns"
        assert summary["result_emitted"] is True

    def test_gemini_result_emitted(self):
        stream = '{"type":"result","stats":{"total_tokens":300,"input_tokens":200,"output_tokens":100}}'
        summary = extract_stream_summary(stream)
        assert summary["result_emitted"] is True
        assert summary["output_tokens"] == 100

    def test_codex_turn_completed(self):
        stream = '{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":60}}'
        summary = extract_stream_summary(stream)
        assert summary["result_emitted"] is True
        assert summary["output_tokens"] == 60

    def test_error_events_captured(self):
        stream = "\n".join([
            '{"type":"error","message":"rate limited"}',
            '{"type":"result","is_error":true,"result":"boom"}',
        ])
        summary = extract_stream_summary(stream)
        assert "rate limited" in summary["error_events"]
        assert "boom" in summary["error_events"]

    def test_last_event_timestamp_iso_formatted(self):
        stream = '{"type":"step_finish","timestamp":1783710002000,"part":{"reason":"stop"}}'
        summary = extract_stream_summary(stream)
        ts = summary["last_event_timestamp"]
        assert ts is not None and "T" in str(ts)


class TestFormatStreamSummaryNote:
    """format_stream_summary_note turns the summary into a human-readable note."""

    def test_truncation_note_names_reason_and_tokens(self):
        note = format_stream_summary_note({
            "finish_reason": "length",
            "output_tokens": 40,
            "input_tokens": 1200,
            "total_tokens": 1240,
        })
        assert "length" in note
        assert "40" in note
        assert "1,200" in note

    def test_no_reason_returns_empty_note(self):
        assert format_stream_summary_note({}) == ""

    def test_missing_tokens_does_not_raise(self):
        note = format_stream_summary_note({"finish_reason": "length"})
        assert "length" in note
