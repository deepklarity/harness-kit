"""Mock tests for the `agy` harness — no real `agy` binary needed.

Tags: [mock] — mocked subprocess, no real agents.

Each test patches `asyncio.create_subprocess_exec` so the harness sees a
synthetic `_FakeSubprocess` (defined inline; mirrors the helpers used
across `tests/mock/test_harness_subprocess_errors.py` and
`tests/conftest.py`). We verify the universal subprocess error paths:
timeout, non-zero exit, stderr-only, and the happy path with the
ODIN-STATUS envelope extracted from plain-text output.
"""

import asyncio
import time
from typing import List, Optional
from unittest.mock import patch

import pytest

from odin.harnesses.agy import AgyHarness
from odin.harnesses.base import SUBPROCESS_STREAM_LIMIT
from odin.models import AgentConfig


# ── Test doubles ────────────────────────────────────────────────


class _FakeStream:
    """Async-iterable that yields bytes lines, with optional per-line delay."""

    def __init__(self, lines: List[bytes], delay: float = 0):
        self._lines = list(lines)
        self._delay = delay
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        if self._index > 0 and self._delay:
            await asyncio.sleep(self._delay)
        line = self._lines[self._index]
        self._index += 1
        return line


class _FakeStderr:
    """Async-readable stderr that returns fixed content once."""

    def __init__(self, content: bytes = b""):
        self.content = content

    async def read(self):
        return self.content


class _FakeProcess:
    """Mock asyncio.subprocess.Process for agy harness tests."""

    def __init__(
        self,
        returncode: int = 0,
        stdout_lines: Optional[List[bytes]] = None,
        stderr_content: bytes = b"",
        pid: int = 91234,
        wait_raises_timeout: bool = False,
        communicate_raises_timeout: bool = False,
    ):
        self.returncode = returncode
        self.pid = pid
        self.kill_called = False
        self.wait_called = False
        self.communicate_called = False
        self.stdout = _FakeStream(stdout_lines or [])
        self.stderr = _FakeStderr(stderr_content)
        self._wait_raises_timeout = wait_raises_timeout
        self._communicate_raises_timeout = (
            communicate_raises_timeout or wait_raises_timeout
        )

    async def wait(self):
        self.wait_called = True
        if self._wait_raises_timeout:
            raise asyncio.TimeoutError()
        return self.returncode

    def kill(self):
        self.kill_called = True
        self.returncode = -9

    async def communicate(self):
        self.communicate_called = True
        if self._communicate_raises_timeout:
            raise asyncio.TimeoutError()
        all_lines = []
        async for raw in self.stdout:
            all_lines.append(raw)
        return (b"".join(all_lines), self.stderr.content)


# ── Happy path ────────────────────────────────────────────────


class TestAgyExecuteSuccess:
    """Plain-text success path: agy exits 0 and stdout contains a
    properly-formed ODIN-STATUS block."""

    @pytest.mark.asyncio
    async def test_success_extracts_odin_envelope(self, tmp_path):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = (
            "Hello from agy.\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "Did the thing.\n"
        )
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "Reply with exactly: OK",
                {"working_dir": str(tmp_path)},
            )

        assert result.success is True
        assert "ODIN-STATUS" in result.output
        assert "SUCCESS" in result.output
        assert result.agent == "Agy"

    @pytest.mark.asyncio
    async def test_success_with_output_file_writes_to_disk(self, tmp_path):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        out_path = tmp_path / "agy.out"
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[
                b"line one\n",
                b"line two\n",
                b"-------ODIN-STATUS-------\n",
                b"SUCCESS\n",
                b"-------ODIN-SUMMARY-------\n",
                b"done\n",
            ],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "do it",
                {
                    "working_dir": str(tmp_path),
                    "output_file": str(out_path),
                },
            )

        assert result.success is True
        assert out_path.exists()
        # The plain text output is preserved verbatim — agy emits plain
        # text, not JSONL, so extract_text_from_stream() returns it
        # unchanged.
        written = out_path.read_text()
        assert "line one" in written
        assert "line two" in written

    @pytest.mark.asyncio
    async def test_missing_odin_envelope_returns_failed(self, tmp_path):
        """Without an ODIN-STATUS block the harness must NOT report
        success — even when returncode is 0. This guards against
        silent truncation / mid-generation exits."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = "I got distracted and never finished.\n"
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert result.error is not None
        assert "ODIN-STATUS" in result.error

    @pytest.mark.asyncio
    async def test_validate_status_false_skips_envelope_check(self, tmp_path):
        """validate_status=False disables the envelope guard so callers
        that don't expect the ODIN-STATUS protocol can still get a
        success=True from a clean exit."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = "plain response with no envelope\n"
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task",
                {
                    "working_dir": str(tmp_path),
                    "validate_status": False,
                },
            )

        assert result.success is True
        assert result.error is None


# ── Subprocess error paths ────────────────────────────────────


class TestAgyExecuteFailures:
    """The three universal failure paths: timeout, non-zero exit, stderr-only."""

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess_and_returns_failed(self, tmp_path):
        """A hanging agy process must be killed AND return success=False."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        proc = _FakeProcess(
            returncode=None,
            stdout_lines=[],
            wait_raises_timeout=True,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task",
                {"working_dir": str(tmp_path), "timeout_seconds": 1},
            )

        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error.lower()
        assert proc.kill_called, "timeout must kill subprocess to avoid leak"

    @pytest.mark.asyncio
    async def test_timeout_via_communicate_also_kills_subprocess(self, tmp_path):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        proc = _FakeProcess(
            returncode=None,
            stdout_lines=[],
            communicate_raises_timeout=True,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task",
                {"working_dir": str(tmp_path), "timeout_seconds": 1},
            )

        assert result.success is False
        assert proc.kill_called

    @pytest.mark.asyncio
    async def test_non_zero_exit_returns_failed_with_stderr(self, tmp_path):
        """A non-zero exit from agy surfaces stderr text as the error."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stderr = b"agy: API key not configured\n"
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert result.error is not None
        assert "API key" in result.error

    @pytest.mark.asyncio
    async def test_stderr_only_output_returns_failed(self, tmp_path):
        """Empty stdout + non-zero exit still yields success=False with
        the stderr text surfaced."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stderr = b"panic: model provider returned 500\n"
        proc = _FakeProcess(
            returncode=2,
            stdout_lines=[],
            stderr_content=stderr,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "panic" in (result.error or "")
        assert not result.output or not result.output.strip()

    @pytest.mark.asyncio
    async def test_non_zero_exit_with_partial_stdout_preserves_output(
        self, tmp_path
    ):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        proc = _FakeProcess(
            returncode=137,
            stdout_lines=[b"partial output before crash\n"],
            stderr_content=b"fatal: command failed\n",
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "fatal" in (result.error or "")
        assert "partial output" in result.output

    @pytest.mark.asyncio
    async def test_cli_not_found_returns_file_not_found_error(self, tmp_path):
        """shutil.which returned nothing → asyncio.create_subprocess_exec
        raises FileNotFoundError → harness surfaces a clean error."""
        cfg = AgentConfig(cli_command="agy-missing-on-path")
        harness = AgyHarness(cfg)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("agy-missing-on-path not on PATH"),
        ):
            result = await harness.execute(
                "task", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "agy-missing-on-path" in (result.error or "")


# ── Streaming parity ──────────────────────────────────────────


class TestAgyStreaming:
    """execute_streaming() yields stdout chunks incrementally.

    The non-streaming `execute()` filters through extract_text_from_stream
    and may also use the output_file path; the streaming path emits raw
    stdout lines. Both paths must surface the same content (modulo
    formatting); this test pins the streaming delivery.
    """

    @pytest.mark.asyncio
    async def test_streaming_yields_lines_incrementally(self, tmp_path):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        lines = [
            b"first chunk\n",
            b"second chunk\n",
            b"third chunk\n",
        ]
        proc = _FakeProcess(stdout_lines=lines)
        proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            chunks: List[str] = []
            async for chunk in harness.execute_streaming(
                "p", {"working_dir": str(tmp_path)}
            ):
                chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0] == "first chunk\n"
        assert chunks[1] == "second chunk\n"
        assert chunks[2] == "third chunk\n"

    @pytest.mark.asyncio
    async def test_streaming_yields_error_when_cli_missing(self, tmp_path):
        cfg = AgentConfig(cli_command="missing-agy")
        harness = AgyHarness(cfg)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError(),
        ):
            chunks: List[str] = []
            async for chunk in harness.execute_streaming(
                "p", {"working_dir": str(tmp_path)}
            ):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert "[error]" in chunks[0]
        assert "missing-agy" in chunks[0]


# ── Cross-harness parametric consistency ──────────────────────


class TestAgyParityWithOtherHarnesses:
    """Pin the parts of agy's contract that must match every other CLI
    harness so the conformance suite can be retargeted."""

    @pytest.mark.asyncio
    async def test_timeout_seconds_zero_is_treated_as_no_timeout(self, tmp_path):
        """A timeout_seconds of 0 / falsy means 'no timeout' (matches
        gemini, codex, claude). We exercise this by giving a process
        that exits 0 and verifying success (no spurious timeout)."""
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = (
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "ok\n"
        )
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task",
                {
                    "working_dir": str(tmp_path),
                    "timeout_seconds": 0,
                },
            )

        assert result.success is True
        assert "timed out" not in (result.error or "").lower()
