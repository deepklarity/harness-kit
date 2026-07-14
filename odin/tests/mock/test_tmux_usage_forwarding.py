"""Tests that token usage survives the tmux execution path.

Root cause: Orchestrator._execute_via_tmux() is the dominant CLI execution
path (used whenever tmux is on PATH — virtually always). It never extracted
token usage nor set TaskResult.metadata, so every downstream consumer that
reads metadata["usage"] (CostTracker, the inline "N tokens" comment text)
saw nothing regardless of what claude.py's own extractor could do — that
extractor is unreachable in production because tmux bypasses harness.execute()
entirely.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from odin.orchestrator import Orchestrator


@pytest.fixture
def mock_orchestrator(odin_dirs, make_config, tmp_path):
    config = make_config(
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        board_backend="local",
    )
    return Orchestrator(config=config)


class TestExecuteViaTmuxUsageForwarding:
    @pytest.mark.asyncio
    async def test_claude_model_usage_forwarded(self, mock_orchestrator, tmp_path):
        output_file = str(tmp_path / "task.out")
        Path(output_file).write_text(
            json.dumps({"type": "content_block_delta", "delta": {"text": "hi"}}) + "\n"
            + json.dumps({"modelUsage": {"claude-sonnet-5": {
                "inputTokens": 300, "outputTokens": 120,
            }}}) + "\n"
        )

        with patch("odin.tmux.launch", new=AsyncMock(return_value="odin-abc")), \
             patch("odin.tmux.wait_for_exit", new=AsyncMock(return_value=0)):
            result = await mock_orchestrator._execute_via_tmux(
                task_id="t1",
                cmd=["claude", "-p", "test"],
                working_dir=str(tmp_path),
                output_file=output_file,
                agent_name="Claude Code",
            )

        assert result.metadata.get("usage", {}).get("input_tokens") == 300
        assert result.metadata.get("usage", {}).get("output_tokens") == 120

    @pytest.mark.asyncio
    async def test_codex_turn_completed_usage_forwarded(self, mock_orchestrator, tmp_path):
        output_file = str(tmp_path / "task.out")
        Path(output_file).write_text(
            json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "done",
            }}) + "\n"
            + json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 500, "output_tokens": 200,
            }}) + "\n"
        )

        with patch("odin.tmux.launch", new=AsyncMock(return_value="odin-abc")), \
             patch("odin.tmux.wait_for_exit", new=AsyncMock(return_value=0)):
            result = await mock_orchestrator._execute_via_tmux(
                task_id="t2",
                cmd=["codex", "exec", "test"],
                working_dir=str(tmp_path),
                output_file=output_file,
                agent_name="Codex",
            )

        assert result.metadata.get("usage", {}).get("input_tokens") == 500
        assert result.metadata.get("usage", {}).get("output_tokens") == 200

    @pytest.mark.asyncio
    async def test_no_usage_data_yields_empty_metadata(self, mock_orchestrator, tmp_path):
        output_file = str(tmp_path / "task.out")
        Path(output_file).write_text("plain text output, no JSON\n")

        with patch("odin.tmux.launch", new=AsyncMock(return_value="odin-abc")), \
             patch("odin.tmux.wait_for_exit", new=AsyncMock(return_value=0)):
            result = await mock_orchestrator._execute_via_tmux(
                task_id="t3",
                cmd=["codex", "exec", "test"],
                working_dir=str(tmp_path),
                output_file=output_file,
                agent_name="Codex",
            )

        assert result.metadata.get("usage", {}) == {}
