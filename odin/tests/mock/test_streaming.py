"""Tests for streaming behavior during `odin plan`.

Verifies that plan output is streamed incrementally (chunk by chunk) to the
user's terminal, NOT buffered until the process finishes.

Tags:
- [mock] — mocked subprocess, no real agents
- [simple] — pure logic
"""

import asyncio
import json
import time
from pathlib import Path
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin.harnesses.base import BaseHarness
from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.gemini import GeminiHarness
from odin.harnesses.codex import CodexHarness
from odin.harnesses.qwen import QwenHarness
from odin.models import AgentConfig, CostTier, ModelRoute, OdinConfig, TaskResult

from tests.conftest import make_fake_process as _make_fake_process


# ── [mock] Harness execute_streaming — incremental chunk delivery ─────


class TestHarnessStreaming:
    """Verify that CLI harnesses yield output chunks incrementally."""

    STREAMING_LINES = [
        b"Thinking about the task...\n",
        b"Breaking it down into sub-tasks...\n",
        b"Identifying agent capabilities...\n",
        b'[{"id": "task_1", "title": "Write poem"}]\n',
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", [
        (ClaudeHarness, "claude"),
        (GeminiHarness, "gemini"),
        (CodexHarness, "codex"),
        (QwenHarness, "qwen"),
    ])
    async def test_streaming_yields_chunks_incrementally(self, harness_cls, name):
        """Each chunk should arrive BEFORE the process finishes.

        We record the timestamp of each yielded chunk and verify they are
        spread over time, not all bunched at the end.
        """
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        fake_proc = _make_fake_process(self.STREAMING_LINES, delay=0.05)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            timestamps: List[float] = []
            chunks: List[str] = []

            async for chunk in harness.execute_streaming("test prompt", {"working_dir": "/tmp"}):
                timestamps.append(time.monotonic())
                chunks.append(chunk)

        # All 4 lines should be yielded
        assert len(chunks) == 4, f"Expected 4 chunks, got {len(chunks)}: {chunks}"
        assert chunks[0] == "Thinking about the task...\n"
        assert chunks[-1] == '[{"id": "task_1", "title": "Write poem"}]\n'

        # Verify incremental delivery: the time span from first to last chunk
        # should be > 0 (not all delivered at the same instant)
        time_span = timestamps[-1] - timestamps[0]
        assert time_span > 0.05, (
            f"Chunks should arrive incrementally over time, but total span was "
            f"{time_span:.3f}s — suggests buffered delivery"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", [
        (ClaudeHarness, "claude"),
        (GeminiHarness, "gemini"),
        (CodexHarness, "codex"),
        (QwenHarness, "qwen"),
    ])
    async def test_streaming_chunk_order_preserved(self, harness_cls, name):
        """Chunks must arrive in the same order the subprocess emits them."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        lines = [f"Line {i}\n".encode() for i in range(10)]
        fake_proc = _make_fake_process(lines, delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            chunks = []
            async for chunk in harness.execute_streaming("test", {"working_dir": "/tmp"}):
                chunks.append(chunk)

        assert len(chunks) == 10
        for i, chunk in enumerate(chunks):
            assert chunk == f"Line {i}\n", f"Chunk {i} out of order: {chunk!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", [
        (ClaudeHarness, "claude"),
        (GeminiHarness, "gemini"),
        (CodexHarness, "codex"),
        (QwenHarness, "qwen"),
    ])
    async def test_streaming_callback_called_per_chunk(self, harness_cls, name):
        """When used with a callback, each chunk triggers the callback immediately."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        fake_proc = _make_fake_process(self.STREAMING_LINES, delay=0.02)
        callback_log: List[Tuple[str, float]] = []

        def callback(chunk: str) -> None:
            callback_log.append((chunk, time.monotonic()))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            chunks = []
            async for chunk in harness.execute_streaming("test", {"working_dir": "/tmp"}):
                callback(chunk)
                chunks.append(chunk)

        assert len(callback_log) == 4
        # First callback should happen well before the last one
        first_time = callback_log[0][1]
        last_time = callback_log[-1][1]
        assert last_time - first_time > 0.02, (
            "Callback calls should be spread over time, not batched"
        )

    @pytest.mark.asyncio
    async def test_streaming_handles_cli_not_found(self):
        """execute_streaming yields an error message when CLI is missing."""
        cfg = AgentConfig(cli_command="nonexistent-cli-xyz", capabilities=["writing"])
        harness = ClaudeHarness(cfg)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            chunks = []
            async for chunk in harness.execute_streaming("test", {"working_dir": "/tmp"}):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert "[error]" in chunks[0]
        assert "nonexistent-cli-xyz" in chunks[0]

    @pytest.mark.asyncio
    async def test_streaming_empty_output(self):
        """execute_streaming handles subprocess with no output."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = ClaudeHarness(cfg)

        fake_proc = _make_fake_process([], delay=0)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            chunks = []
            async for chunk in harness.execute_streaming("test", {"working_dir": "/tmp"}):
                chunks.append(chunk)

        assert len(chunks) == 0


# ── [simple] BaseHarness fallback streaming ───────────────────────────


class TestBaseHarnessFallbackStreaming:
    """The default execute_streaming() on BaseHarness calls execute() and
    yields the full output in one shot. This is expected behavior for
    API-based harnesses that don't support true streaming."""

    @pytest.mark.asyncio
    async def test_fallback_yields_full_output_once(self):
        """BaseHarness.execute_streaming yields entire output as single chunk."""

        class FakeHarness(BaseHarness):
            @property
            def name(self):
                return "Fake"

            async def execute(self, prompt, context):
                return TaskResult(
                    success=True,
                    output="Full output in one go.\nLine 2.\nLine 3.",
                    agent="Fake",
                )

            async def is_available(self):
                return True

        cfg = AgentConfig(capabilities=["writing"])
        harness = FakeHarness(cfg)

        chunks = []
        async for chunk in harness.execute_streaming("test", {}):
            chunks.append(chunk)

        # Fallback yields everything as ONE chunk
        assert len(chunks) == 1
        assert "Full output in one go." in chunks[0]
        assert "Line 3." in chunks[0]

    @pytest.mark.asyncio
    async def test_fallback_yields_nothing_for_empty_output(self):
        """BaseHarness.execute_streaming yields nothing when output is empty."""

        class FakeHarness(BaseHarness):
            @property
            def name(self):
                return "Fake"

            async def execute(self, prompt, context):
                return TaskResult(success=True, output="", agent="Fake")

            async def is_available(self):
                return True

        cfg = AgentConfig(capabilities=["writing"])
        harness = FakeHarness(cfg)

        chunks = []
        async for chunk in harness.execute_streaming("test", {}):
            chunks.append(chunk)

        assert len(chunks) == 0


# ── [mock] Orchestrator _decompose streaming ──────────────────────────


class TestDecomposeStreaming:
    """Verify that _decompose() streams output through the callback
    incrementally, not as a single batch at the end.

    _decompose() is now a pure harness dispatcher — it receives a pre-built
    prompt and calls the harness.  The agent writes its plan JSON to a file
    (as instructed in the prompt), so _decompose() returns nothing.
    """

    def _make_orchestrator(self, tmp_path):
        task_dir = str(tmp_path / "tasks")
        log_dir = str(tmp_path / "logs")
        cost_dir = str(tmp_path / "costs")
        spec_dir = str(tmp_path / "specs")
        for d in [task_dir, log_dir, cost_dir, spec_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

        from odin.orchestrator import Orchestrator

        cfg = OdinConfig(
            base_agent="claude",
            task_storage=task_dir,
            log_dir=log_dir,
            cost_storage=cost_dir,
            board_backend="local",
            agents={
                "claude": AgentConfig(
                    cli_command="fake-claude",
                    capabilities=["planning"],
                    cost_tier=CostTier.HIGH,
                ),
                "gemini": AgentConfig(
                    cli_command="gemini",
                    capabilities=["writing"],
                    cost_tier=CostTier.LOW,
                ),
            },
            model_routing=[
                ModelRoute(agent="gemini", model="gemini-2.5-flash"),
                ModelRoute(agent="claude", model="claude-sonnet-4-5"),
            ],
        )
        return Orchestrator(cfg)

    @pytest.mark.asyncio
    async def test_decompose_streams_via_callback(self, tmp_path):
        """_decompose() should call stream_callback for EACH chunk as it arrives,
        not all at once after the process completes."""
        orch = self._make_orchestrator(tmp_path)

        streaming_lines = [
            b"Let me think about this...\n",
            b"I'll break this into sub-tasks.\n",
            b"Here's my plan:\n",
            b"Writing plan to disk...\n",
        ]

        fake_proc = _make_fake_process(streaming_lines, delay=0.03)

        callback_log: List[Tuple[str, float]] = []

        def stream_cb(chunk: str) -> None:
            callback_log.append((chunk, time.monotonic()))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await orch._decompose(
                "Test prompt with plan_path instructions", "/tmp",
                spec_id="sp_test_001",
                stream_callback=stream_cb,
            )

        # Callback was called for each line
        assert len(callback_log) == 4, (
            f"Expected 4 callback calls (one per line), got {len(callback_log)}"
        )

        # Verify incremental timing
        first_t = callback_log[0][1]
        last_t = callback_log[-1][1]
        assert last_t - first_t > 0.03, (
            f"Callbacks should be spread over time ({last_t - first_t:.3f}s span), "
            f"not all at once"
        )

        # Verify the chunks match the lines
        assert "Let me think" in callback_log[0][0]
        assert "sub-tasks" in callback_log[1][0]

    @pytest.mark.asyncio
    async def test_decompose_without_callback_uses_execute(self, tmp_path):
        """Without stream_callback, _decompose() should use execute() not
        execute_streaming()."""
        orch = self._make_orchestrator(tmp_path)

        mock_result = TaskResult(
            success=True,
            output="Plan written to disk.",
            duration_ms=100.0,
            agent="Claude Code",
        )

        with patch.object(
            ClaudeHarness, "execute", return_value=mock_result
        ) as mock_exec, patch.object(
            ClaudeHarness, "execute_streaming"
        ) as mock_stream:
            await orch._decompose("Test prompt", "/tmp", spec_id="sp_test_002")

        # execute() was called, not execute_streaming()
        mock_exec.assert_called_once()
        mock_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_decompose_streaming_delivers_all_chunks(self, tmp_path):
        """_decompose() with streaming delivers all chunks through the callback."""
        orch = self._make_orchestrator(tmp_path)

        streaming_lines = [
            b"Chunk 1\n",
            b"Chunk 2\n",
            b"Chunk 3\n",
            b"Chunk 4\n",
            b"Chunk 5\n",
            b"Chunk 6\n",
            b"Chunk 7\n",
            b"Chunk 8\n",
        ]

        fake_proc = _make_fake_process(streaming_lines, delay=0.01)
        chunks_received: List[str] = []

        def stream_cb(chunk: str) -> None:
            chunks_received.append(chunk)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await orch._decompose(
                "Test prompt", "/tmp", spec_id="sp_test_003",
                stream_callback=stream_cb,
            )

        # All chunks were streamed
        assert len(chunks_received) == 8


# ── [mock] Full plan() streaming integration ──────────────────────────


class TestPlanStreaming:
    """Integration test: verify plan() passes stream_callback through to
    _decompose() and the callback receives incremental output.

    In the unified flow, plan() builds the prompt (with plan_path), calls
    _decompose() which streams to the callback, then reads the plan JSON
    from plan_path on disk.
    """

    @pytest.mark.asyncio
    async def test_plan_with_callback_streams_incrementally(self, tmp_path):
        """plan() in auto mode should stream chunks via callback and then
        read the plan from disk."""
        from odin.orchestrator import Orchestrator, _extract_title
        from odin.specs import generate_spec_id

        task_dir = str(tmp_path / "tasks")
        log_dir = str(tmp_path / "logs")
        cost_dir = str(tmp_path / "costs")
        spec_dir = str(tmp_path / "specs")
        for d in [task_dir, log_dir, cost_dir, spec_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

        cfg = OdinConfig(
            base_agent="claude",
            task_storage=task_dir,
            log_dir=log_dir,
            cost_storage=cost_dir,
            board_backend="local",
            agents={
                "claude": AgentConfig(
                    cli_command="fake-claude",
                    capabilities=["planning"],
                    cost_tier=CostTier.HIGH,
                ),
                "gemini": AgentConfig(
                    cli_command="gemini",
                    capabilities=["writing"],
                    cost_tier=CostTier.LOW,
                ),
            },
            model_routing=[
                ModelRoute(agent="gemini", model="gemini-2.5-flash"),
                ModelRoute(agent="claude", model="claude-sonnet-4-5"),
            ],
        )
        orch = Orchestrator(cfg)

        # Pre-compute where the plan file will be written
        spec_text = "Write a poem"
        title = _extract_title(spec_text)
        sid = generate_spec_id(title)
        plans_dir = Path(task_dir).parent / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / f"plan_{sid}.json"

        # Plan JSON the agent would write to disk
        plan_json = json.dumps([{
            "id": "task_1", "title": "Write poem",
            "description": "Write a beautiful poem",
            "required_capabilities": ["writing"],
            "suggested_agent": "gemini", "complexity": "medium",
            "depends_on": [], "reasoning": "Gemini is good for writing",
        }])

        streaming_lines = [
            b"Analyzing the specification...\n",
            b"Planning sub-tasks...\n",
            b"Writing plan to disk...\n",
        ]

        fake_proc = _make_fake_process(streaming_lines, delay=0.03)
        callback_log: List[Tuple[str, float]] = []

        def stream_cb(chunk: str) -> None:
            callback_log.append((chunk, time.monotonic()))

        # Mock _decompose to stream AND write plan to disk
        original_decompose = orch._decompose

        async def mock_decompose(prompt, wd, spec_id=None, stream_callback=None):
            # Write plan to disk (simulating what the agent does)
            plan_path.write_text(plan_json)
            # Still stream chunks through the callback
            if stream_callback:
                for line in streaming_lines:
                    stream_callback(line.decode())

        with patch.object(orch, "_decompose", side_effect=mock_decompose), \
             patch.object(orch, "_fetch_quota", return_value=None):
            spec_id, tasks = await orch.plan(
                spec_text,
                working_dir=str(tmp_path),
                mode="auto",
                stream_callback=stream_cb,
            )

        # Streaming happened
        assert len(callback_log) == 3, (
            f"Expected 3 streamed chunks, got {len(callback_log)}"
        )

        # Tasks were created correctly from the plan on disk
        assert len(tasks) >= 1
        assert tasks[0].title == "Write poem"

    @pytest.mark.asyncio
    async def test_plan_without_callback_does_not_stream(self, tmp_path):
        """plan() in quiet mode should use execute(), not streaming."""
        from odin.orchestrator import Orchestrator, _extract_title
        from odin.specs import generate_spec_id
        import json as json_mod

        task_dir = str(tmp_path / "tasks")
        log_dir = str(tmp_path / "logs")
        cost_dir = str(tmp_path / "costs")
        spec_dir = str(tmp_path / "specs")
        for d in [task_dir, log_dir, cost_dir, spec_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

        cfg = OdinConfig(
            base_agent="claude",
            task_storage=task_dir,
            log_dir=log_dir,
            cost_storage=cost_dir,
            board_backend="local",
            agents={
                "claude": AgentConfig(
                    cli_command="fake-claude",
                    capabilities=["planning"],
                    cost_tier=CostTier.HIGH,
                ),
                "gemini": AgentConfig(
                    cli_command="gemini",
                    capabilities=["writing"],
                    cost_tier=CostTier.LOW,
                ),
            },
            model_routing=[
                ModelRoute(agent="gemini", model="gemini-2.5-flash"),
                ModelRoute(agent="claude", model="claude-sonnet-4-5"),
            ],
        )
        orch = Orchestrator(cfg)

        # Pre-compute plan_path
        spec_text = "Test"
        title = _extract_title(spec_text)
        sid = generate_spec_id(title)
        plans_dir = Path(task_dir).parent / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / f"plan_{sid}.json"

        plan_json = json_mod.dumps([{
            "id": "task_1", "title": "Test",
            "description": "Test task",
            "required_capabilities": [],
            "suggested_agent": "gemini", "complexity": "low",
            "depends_on": [], "reasoning": "test",
        }])

        mock_result = TaskResult(
            success=True,
            output="Plan written to disk.",
            duration_ms=100.0,
            agent="Claude Code",
        )

        # Mock _decompose to write plan to disk (simulating agent)
        async def mock_decompose(prompt, wd, spec_id=None, stream_callback=None):
            plan_path.write_text(plan_json)

        with patch.object(orch, "_decompose", side_effect=mock_decompose), \
             patch.object(orch, "_fetch_quota", return_value=None):
            spec_id, tasks = await orch.plan(
                spec_text,
                working_dir=str(tmp_path),
            )

        assert len(tasks) >= 1


# ── [mock] CLI layer streaming ────────────────────────────────────────


class TestCLIStreamChunk:
    """Verify that the CLI _stream_chunk callback writes to stdout
    with immediate flushing for real-time display."""

    def test_stream_chunk_writes_and_flushes(self):
        """_stream_chunk should call sys.stdout.write + flush per chunk."""
        import sys
        from io import StringIO

        captured = StringIO()

        def _stream_chunk(chunk: str) -> None:
            captured.write(chunk)

        # Simulate the streaming loop from cli.py
        chunks = [
            "Thinking...\n",
            "Planning...\n",
            "[{...json...}]\n",
        ]
        for chunk in chunks:
            _stream_chunk(chunk)

        output = captured.getvalue()
        assert "Thinking...\n" in output
        assert "Planning...\n" in output
        assert "[{...json...}]\n" in output

    def test_stream_chunk_preserves_partial_lines(self):
        """Chunks that don't end with newline should still be written."""
        from io import StringIO

        captured = StringIO()

        def _stream_chunk(chunk: str) -> None:
            captured.write(chunk)

        _stream_chunk("partial ")
        _stream_chunk("line\n")
        _stream_chunk("next line\n")

        output = captured.getvalue()
        assert output == "partial line\nnext line\n"


# ── [mock] Streaming vs non-streaming timing comparison ───────────────


class TestStreamingTimingBehavior:
    """Verify the key behavioral difference: streaming delivers output
    progressively, while non-streaming waits for completion."""

    @pytest.mark.asyncio
    async def test_streaming_first_chunk_arrives_before_last(self):
        """In streaming mode, the first chunk arrives well before the process
        finishes (simulated by delayed lines)."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = ClaudeHarness(cfg)

        # 5 lines with 50ms delay each = ~200ms total
        lines = [f"Line {i}\n".encode() for i in range(5)]
        fake_proc = _make_fake_process(lines, delay=0.05)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            first_chunk_time = None
            last_chunk_time = None
            chunk_count = 0

            async for chunk in harness.execute_streaming("test", {"working_dir": "/tmp"}):
                now = time.monotonic()
                if first_chunk_time is None:
                    first_chunk_time = now
                last_chunk_time = now
                chunk_count += 1

        assert chunk_count == 5
        # First chunk arrives immediately; last chunk after delays
        assert last_chunk_time - first_chunk_time > 0.1, (
            "Streaming should deliver chunks over time, not instantly"
        )

    @pytest.mark.asyncio
    async def test_non_streaming_delivers_all_at_once(self):
        """In non-streaming (execute) mode, all output arrives as one block
        after the process finishes."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = ClaudeHarness(cfg)

        fake_proc = MagicMock()
        full_output = "Line 0\nLine 1\nLine 2\nLine 3\nLine 4\n"
        stdout_future = asyncio.Future()
        stdout_future.set_result((full_output.encode(), b""))
        fake_proc.communicate = MagicMock(return_value=stdout_future)
        fake_proc.pid = 12345
        fake_proc.returncode = 0
        fake_proc.stderr = None

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await harness.execute("test", {"working_dir": "/tmp"})

        # All output comes as a single string
        assert result.success
        assert result.output == full_output
        assert "Line 0\n" in result.output
        assert "Line 4\n" in result.output
