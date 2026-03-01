"""Tests for stream-json output format and trace logging.

Covers:
- build_execute_command() includes correct output format flags
- extract_text_from_line() / extract_text_from_stream() parsing
- read_with_trace() writes trace and output files
- execute() produces plain-text TaskResult.output from JSON streams
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from odin.harnesses.base import (
    extract_text_from_line,
    extract_text_from_stream,
    read_with_trace,
)
from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.gemini import GeminiHarness
from odin.harnesses.qwen import QwenHarness
from odin.harnesses.minimax import MiniMaxHarness
from odin.harnesses.glm import GLMHarness
from odin.harnesses.codex import CodexHarness
from odin.models import AgentConfig

from tests.conftest import make_fake_process


# ---------------------------------------------------------------
# build_execute_command() output format flags
# ---------------------------------------------------------------


class TestBuildCommandOutputFormat:
    """Verify each harness includes the correct output format flag."""

    def _cfg(self, **kw):
        return AgentConfig(capabilities=["writing"], **kw)

    def test_claude_uses_stream_json_verbose(self):
        h = ClaudeHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"
        assert "--verbose" in cmd

    def test_gemini_uses_stream_json(self):
        h = GeminiHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_qwen_uses_stream_json(self):
        h = QwenHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_minimax_uses_format_json(self):
        h = MiniMaxHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--format" in cmd
        idx = cmd.index("--format")
        assert cmd[idx + 1] == "json"

    def test_glm_uses_format_json(self):
        h = GLMHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--format" in cmd
        idx = cmd.index("--format")
        assert cmd[idx + 1] == "json"

    def test_codex_has_no_output_format(self):
        h = CodexHarness(self._cfg())
        cmd = h.build_execute_command("hello", {})
        assert "--output-format" not in cmd
        assert "--format" not in cmd


# ---------------------------------------------------------------
# extract_text_from_line()
# ---------------------------------------------------------------


class TestExtractTextFromLine:
    """Test JSON line parsing for different CLI formats."""

    def test_claude_content_block_delta(self):
        line = json.dumps({
            "type": "content_block_delta",
            "delta": {"text": "Hello world"},
        })
        assert extract_text_from_line(line) == "Hello world"

    def test_claude_result(self):
        line = json.dumps({"type": "result", "result": "Final answer"})
        assert extract_text_from_line(line) == "Final answer"

    def test_gemini_text_event(self):
        line = json.dumps({"type": "text", "text": "Gemini says hi"})
        assert extract_text_from_line(line) == "Gemini says hi"

    def test_opencode_step_finish(self):
        line = json.dumps({"type": "step_finish", "content": "Done"})
        assert extract_text_from_line(line) == "Done"

    def test_unknown_type_returns_empty(self):
        line = json.dumps({"type": "ping", "data": {}})
        assert extract_text_from_line(line) == ""

    def test_non_json_returns_line(self):
        line = "plain text output\n"
        assert extract_text_from_line(line) == "plain text output\n"

    def test_empty_line_returns_empty(self):
        assert extract_text_from_line("") == ""
        assert extract_text_from_line("  \n") == ""

    def test_content_block_delta_empty_text(self):
        line = json.dumps({
            "type": "content_block_delta",
            "delta": {"text": ""},
        })
        assert extract_text_from_line(line) == ""


# ---------------------------------------------------------------
# extract_text_from_stream()
# ---------------------------------------------------------------


class TestExtractTextFromStream:
    """Test full stream parsing into plain text."""

    def test_claude_stream(self):
        lines = [
            json.dumps({"type": "message_start", "message": {}}),
            json.dumps({"type": "content_block_delta", "delta": {"text": "Hello "}}),
            json.dumps({"type": "content_block_delta", "delta": {"text": "world"}}),
            json.dumps({"type": "message_stop"}),
        ]
        raw = "\n".join(lines)
        assert extract_text_from_stream(raw) == "Hello world"

    def test_gemini_stream(self):
        lines = [
            json.dumps({"type": "text", "text": "Part 1"}),
            json.dumps({"type": "text", "text": " Part 2"}),
        ]
        raw = "\n".join(lines)
        assert extract_text_from_stream(raw) == "Part 1 Part 2"

    def test_plain_text_passthrough(self):
        raw = "This is plain text\nNo JSON here\n"
        assert extract_text_from_stream(raw) == raw

    def test_empty_input(self):
        assert extract_text_from_stream("") == ""
        assert extract_text_from_stream("  \n  ") == ""

    def test_all_non_text_events_returns_raw(self):
        lines = [
            json.dumps({"type": "ping"}),
            json.dumps({"type": "message_start", "message": {}}),
        ]
        raw = "\n".join(lines)
        # No text extracted → falls back to raw
        assert extract_text_from_stream(raw) == raw


# ---------------------------------------------------------------
# read_with_trace()
# ---------------------------------------------------------------


class TestReadWithTrace:
    """Test that read_with_trace writes to both trace and output files."""

    @pytest.mark.asyncio
    async def test_writes_trace_and_output_files(self, tmp_path):
        trace_file = str(tmp_path / "task.trace.jsonl")
        output_file = str(tmp_path / "task.out")

        # Simulate Claude stream-json output
        stream_lines = [
            json.dumps({"type": "content_block_delta", "delta": {"text": "Hello "}}).encode() + b"\n",
            json.dumps({"type": "content_block_delta", "delta": {"text": "world"}}).encode() + b"\n",
            json.dumps({"type": "message_stop"}).encode() + b"\n",
        ]
        proc = make_fake_process(stream_lines, delay=0.01)

        result = await read_with_trace(proc, output_file, trace_file)

        # Trace file has raw JSON lines
        trace_content = Path(trace_file).read_text()
        assert "content_block_delta" in trace_content
        assert "message_stop" in trace_content
        lines = [l for l in trace_content.strip().splitlines() if l.strip()]
        assert len(lines) == 3

        # Output file has extracted text
        output_content = Path(output_file).read_text()
        assert "Hello " in output_content
        assert "world" in output_content

        # Return value is extracted text
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_handles_non_text_events(self, tmp_path):
        trace_file = str(tmp_path / "task.trace.jsonl")
        output_file = str(tmp_path / "task.out")

        stream_lines = [
            json.dumps({"type": "ping"}).encode() + b"\n",
            json.dumps({"type": "content_block_delta", "delta": {"text": "data"}}).encode() + b"\n",
        ]
        proc = make_fake_process(stream_lines, delay=0.01)

        result = await read_with_trace(proc, output_file, trace_file)

        # Trace has both lines
        trace_lines = Path(trace_file).read_text().strip().splitlines()
        assert len(trace_lines) == 2

        # Output has only text content
        assert result == "data"


# ---------------------------------------------------------------
# execute() with trace file — integration test
# ---------------------------------------------------------------


class TestExecuteWithTrace:
    """Test that harness execute() writes trace files and returns plain text."""

    @pytest.mark.asyncio
    async def test_claude_execute_writes_trace(self, tmp_path):
        cfg = AgentConfig(cli_command="fake-claude", capabilities=["writing"])
        harness = ClaudeHarness(cfg)

        output_file = str(tmp_path / "task.out")
        trace_file = str(tmp_path / "task.trace.jsonl")

        stream_lines = [
            json.dumps({"type": "content_block_delta", "delta": {"text": "Result text"}}).encode() + b"\n",
            json.dumps({"type": "message_stop"}).encode() + b"\n",
        ]
        fake_proc = make_fake_process(stream_lines, delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await harness.execute("test", {
                "working_dir": str(tmp_path),
                "output_file": output_file,
                "trace_file": trace_file,
            })

        assert result.success
        assert result.output == "Result text"
        assert Path(trace_file).exists()
        assert "content_block_delta" in Path(trace_file).read_text()

    @pytest.mark.asyncio
    async def test_execute_without_trace_still_extracts_text(self, tmp_path):
        """When no trace_file in context, still extract text from JSON."""
        cfg = AgentConfig(cli_command="fake-gemini", capabilities=["writing"])
        harness = GeminiHarness(cfg)

        raw_output = "\n".join([
            json.dumps({"type": "text", "text": "Hello "}),
            json.dumps({"type": "text", "text": "Gemini"}),
        ]) + "\n"

        fake_proc = MagicMock()
        fake_proc.pid = 99
        fake_proc.returncode = 0

        async def fake_communicate():
            return (raw_output.encode(), b"")

        fake_proc.communicate = fake_communicate
        fake_proc.stderr = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await harness.execute("test", {"working_dir": str(tmp_path)})

        assert result.success
        assert result.output == "Hello Gemini"

    @pytest.mark.asyncio
    async def test_codex_execute_unchanged(self, tmp_path):
        """Codex doesn't use JSON output — output should pass through as-is."""
        cfg = AgentConfig(cli_command="fake-codex", capabilities=["writing"])
        harness = CodexHarness(cfg)

        output_file = str(tmp_path / "task.out")
        plain_output = b"Plain codex output\nLine 2\n"
        fake_proc = make_fake_process([plain_output], delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await harness.execute("test", {
                "working_dir": str(tmp_path),
                "output_file": output_file,
            })

        assert result.success
        assert "Plain codex output" in result.output
