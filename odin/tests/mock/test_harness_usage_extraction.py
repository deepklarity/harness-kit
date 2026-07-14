"""Token-usage extraction for every non-claude harness.

Only claude used to populate ``TaskResult.metadata["usage"]``. Reflections
reviewed by agy, gemini, codex, glm, minimax therefore stored empty
``token_usage``. These tests pin the fix:

1. A single shared ``extract_token_usage()`` in ``harnesses/base.py`` parses
   every CLI's token format into the uniform shape claude already used
   (``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
   ``cache_write_tokens``, ``total_tokens``).
2. Each harness's ``execute()`` populates ``metadata["usage"]`` from its own
   trace output, so a non-claude reflection stores non-empty token_usage.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from odin.harnesses.base import extract_token_usage
from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.codex import CodexHarness
from odin.harnesses.gemini import GeminiHarness
from odin.harnesses.glm import GLMHarness
from odin.harnesses.minimax import MiniMaxHarness
from odin.models import AgentConfig

from tests.conftest import make_fake_process


# ---------------------------------------------------------------
# Shared extractor — one function, every CLI format
# ---------------------------------------------------------------


class TestExtractTokenUsageFormats:
    """extract_token_usage() understands each harness's token dialect."""

    def test_claude_model_usage(self):
        raw = json.dumps({"modelUsage": {
            "claude-opus-4-6": {
                "inputTokens": 5000, "outputTokens": 1200,
                "cacheReadInputTokens": 3000, "cacheCreationInputTokens": 800,
            }
        }})
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 5000
        assert usage["output_tokens"] == 1200
        assert usage["total_tokens"] == 6200
        assert usage["cache_read_tokens"] == 3000
        assert usage["cache_write_tokens"] == 800

    def test_opencode_step_finish_sums(self):
        """GLM / MiniMax (opencode/kilo) emit step_finish token events."""
        raw = "\n".join([
            json.dumps({"type": "step_finish", "part": {"tokens": {
                "input": 100, "output": 50, "total": 150,
                "cache": {"read": 80, "write": 20}}}}),
            json.dumps({"type": "step_finish", "part": {"tokens": {
                "input": 200, "output": 80, "total": 280,
                "cache": {"read": 150, "write": 30}}}}),
        ])
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 300
        assert usage["output_tokens"] == 130
        assert usage["total_tokens"] == 430
        assert usage["cache_read_tokens"] == 230
        assert usage["cache_write_tokens"] == 50

    def test_opencode_reasoning_total_preserved(self):
        """MiniMax/GLM include reasoning tokens in total > input+output."""
        raw = json.dumps({"type": "step_finish", "part": {"tokens": {
            "input": 100, "output": 50, "total": 500}}})
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        # total must not be silently recomputed as input+output
        assert usage["total_tokens"] == 500

    def test_gemini_result_stats(self):
        raw = "\n".join([
            json.dumps({"type": "text", "text": "hi"}),
            json.dumps({"type": "result", "stats": {
                "total_tokens": 1234, "input_tokens": 1000, "output_tokens": 234}}),
        ])
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 234
        assert usage["total_tokens"] == 1234

    def test_qwen_shaped_result_usage(self):
        """The shared extractor still understands the qwen-shaped result.usage
        format even though the qwen harness itself was retired (task #102) —
        other harnesses may emit an equivalent shape."""
        raw = json.dumps({
            "type": "result", "subtype": "success", "result": "done",
            "usage": {"input_tokens": 800, "output_tokens": 200, "total_tokens": 1000}})
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 800
        assert usage["output_tokens"] == 200
        assert usage["total_tokens"] == 1000

    def test_codex_turn_completed(self):
        raw = "\n".join([
            json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "done"}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 500, "output_tokens": 200}}),
        ])
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 500
        assert usage["output_tokens"] == 200
        assert usage["total_tokens"] == 700

    def test_codex_wrapped_token_count(self):
        """Older codex --json wraps events: {"id":..,"msg":{"type":"token_count"}}."""
        raw = "\n".join([
            json.dumps({"id": "0", "msg": {"type": "agent_message", "message": "hi"}}),
            json.dumps({"id": "1", "msg": {
                "type": "token_count", "input_tokens": 640, "output_tokens": 160}}),
        ])
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 640
        assert usage["output_tokens"] == 160
        assert usage["total_tokens"] == 800

    def test_model_usage_preferred_over_step_finish(self):
        raw = "\n".join([
            json.dumps({"type": "step_finish", "part": {"tokens": {
                "input": 100, "output": 50, "total": 150}}}),
            json.dumps({"modelUsage": {"m": {
                "inputTokens": 9000, "outputTokens": 2000}}}),
        ])
        usage = extract_token_usage(raw)
        assert usage["input_tokens"] == 9000
        assert usage["output_tokens"] == 2000
        assert usage["total_tokens"] == 11000

    def test_no_tokens_returns_empty(self):
        raw = "\n".join([
            json.dumps({"type": "step_start"}),
            json.dumps({"type": "text", "text": "hello"}),
        ])
        assert extract_token_usage(raw) == {}

    def test_non_json_lines_skipped(self):
        raw = "some plain log line\n" + json.dumps({
            "type": "result", "stats": {"total_tokens": 42,
                                         "input_tokens": 30, "output_tokens": 12}})
        usage = extract_token_usage(raw)
        assert usage["total_tokens"] == 42


# ---------------------------------------------------------------
# Per-harness execute() populates metadata["usage"]
# ---------------------------------------------------------------

ODIN_STATUS = "\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nok\n"


async def _run_with_trace(harness, lines, tmp_path):
    """Drive a harness through the production trace_file path."""
    output_file = str(tmp_path / "task.out")
    trace_file = str(tmp_path / "task.trace.jsonl")
    fake_proc = make_fake_process(lines, delay=0.005)
    with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        result = await harness.execute("test", {
            "working_dir": str(tmp_path),
            "output_file": output_file,
            "trace_file": trace_file,
            "validate_status": False,
        })
    return result


def _enc(objs):
    return [json.dumps(o).encode() + b"\n" for o in objs]


class TestHarnessMetadataUsage:
    """Each non-claude harness must fill metadata["usage"] with real counts."""

    def _cfg(self, cli):
        return AgentConfig(cli_command=cli, capabilities=["writing"])

    @pytest.mark.asyncio
    async def test_gemini_populates_usage(self, tmp_path):
        lines = _enc([
            {"type": "text", "text": "Answer" + ODIN_STATUS},
            {"type": "result", "stats": {
                "total_tokens": 1500, "input_tokens": 1200, "output_tokens": 300}},
        ])
        result = await _run_with_trace(GeminiHarness(self._cfg("fake-gemini")), lines, tmp_path)
        assert result.metadata.get("usage"), "gemini must populate metadata['usage']"
        assert result.metadata["usage"]["total_tokens"] == 1500

    @pytest.mark.asyncio
    async def test_glm_populates_usage(self, tmp_path):
        lines = _enc([
            {"type": "text", "text": "Answer" + ODIN_STATUS},
            {"type": "step_finish", "part": {"tokens": {
                "input": 400, "output": 100, "total": 500}}},
        ])
        result = await _run_with_trace(GLMHarness(self._cfg("fake-opencode")), lines, tmp_path)
        assert result.metadata.get("usage"), "glm must populate metadata['usage']"
        assert result.metadata["usage"]["total_tokens"] == 500

    @pytest.mark.asyncio
    async def test_minimax_populates_usage(self, tmp_path):
        lines = _enc([
            {"type": "text", "text": "Answer" + ODIN_STATUS},
            {"type": "step_finish", "part": {"tokens": {
                "input": 700, "output": 300, "total": 1100}}},
        ])
        result = await _run_with_trace(MiniMaxHarness(self._cfg("fake-kilo")), lines, tmp_path)
        assert result.metadata.get("usage"), "minimax must populate metadata['usage']"
        assert result.metadata["usage"]["input_tokens"] == 700
        # reasoning tokens keep total above input+output
        assert result.metadata["usage"]["total_tokens"] == 1100

    @pytest.mark.asyncio
    async def test_codex_populates_usage(self, tmp_path):
        """Codex uses the output_file path (no trace_file)."""
        output_file = str(tmp_path / "task.out")
        lines = _enc([
            {"type": "item.completed", "item": {
                "type": "agent_message", "text": "Answer" + ODIN_STATUS}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 500, "output_tokens": 200}},
        ])
        fake_proc = make_fake_process(lines, delay=0.005)
        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await CodexHarness(self._cfg("fake-codex")).execute("test", {
                "working_dir": str(tmp_path),
                "output_file": output_file,
                "validate_status": False,
            })
        assert result.metadata.get("usage"), "codex must populate metadata['usage']"
        assert result.metadata["usage"]["total_tokens"] == 700

    @pytest.mark.asyncio
    async def test_claude_still_populates_usage(self, tmp_path):
        """Regression: claude keeps working through the shared extractor."""
        lines = _enc([
            {"type": "content_block_delta", "delta": {"text": "Answer" + ODIN_STATUS}},
            {"modelUsage": {"claude-opus-4-6": {
                "inputTokens": 2000, "outputTokens": 500,
                "cacheReadInputTokens": 100, "cacheCreationInputTokens": 50}}},
        ])
        result = await _run_with_trace(ClaudeHarness(self._cfg("fake-claude")), lines, tmp_path)
        assert result.metadata.get("usage")
        assert result.metadata["usage"]["total_tokens"] == 2500
        assert result.metadata["usage"]["cache_read_tokens"] == 100
