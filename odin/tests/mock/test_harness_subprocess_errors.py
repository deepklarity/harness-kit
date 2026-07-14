"""Tests for harness subprocess error paths.

Closes gaps tracked in tests/TEST_PLAN.md:
- Harness timeout handling: subprocess killed on timeout → TaskResult.success=False
- Harness stderr / non-zero exit → TaskResult.success=False
- API-harness (minimax / glm) HTTP error paths → TaskResult.success=False

Tags:
- [mock] — mocked subprocess, no real agents, no real HTTP

These tests cover all 5 CLI harnesses (claude, gemini, minimax, glm,
codex) for the universal subprocess error paths (timeout, non-zero exit,
stderr-only output). HTTP error paths are specific to minimax/glm, which
front the model API via the opencode CLI.
"""

import asyncio
from typing import List, Optional
from unittest.mock import patch

import pytest

from odin.harnesses.agy import AgyHarness
from odin.harnesses.base import SUBPROCESS_STREAM_LIMIT
from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.codex import CodexHarness
from odin.harnesses.gemini import GeminiHarness
from odin.harnesses.glm import GLMHarness
from odin.harnesses.minimax import MiniMaxHarness
from odin.models import AgentConfig


ALL_CLI_HARNESSES = [
    (ClaudeHarness, "claude"),
    (GeminiHarness, "gemini"),
    (MiniMaxHarness, "minimax"),
    (GLMHarness, "glm"),
    (CodexHarness, "codex"),
    (AgyHarness, "agy"),
]

API_HARNESSES = [
    (MiniMaxHarness, "minimax"),
    (GLMHarness, "glm"),
]


# ─── Test doubles ────────────────────────────────────────────────────────


class _FakeStream:
    """Async-iterable that yields bytes lines then stops.

    Mirrors the interface of `FakeDelayedStdout` from conftest.py but keeps
    a reference so tests can introspect that the stream was fully consumed.
    """

    def __init__(self, lines: List[bytes], delay: float = 0):
        self._lines = list(lines)
        self._delay = delay
        self._index = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            self.closed = True
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
        self.read_called = False
        self.read_count = 0

    async def read(self):
        self.read_called = True
        self.read_count += 1
        return self.content


class _FakeProcess:
    """Mock asyncio.subprocess.Process with observable side effects.

    - `returncode`: reflects process exit status.
    - `kill()`: marks `kill_called`, sets returncode to non-zero.
    - `wait()`: returns the current returncode, or raises TimeoutError
      if `wait_raises_timeout` is True (simulates the wait_for wrapper
      cancelling the inner wait).
    - `communicate()`: returns (stdout_bytes, stderr_bytes), or raises
      TimeoutError if `communicate_raises_timeout` is True.

    If `wait_raises_timeout=True` it also makes `communicate()` raise
    TimeoutError so tests that don't pass an `output_file` (which causes
    the harness to take the communicate() path) still trigger timeout.
    """

    def __init__(
        self,
        returncode: int = 0,
        stdout_lines: Optional[List[bytes]] = None,
        stderr_content: bytes = b"",
        pid: int = 99999,
        wait_raises_timeout: bool = False,
        communicate_raises_timeout: bool = False,
    ):
        self.returncode = returncode
        self.pid = pid
        self.kill_called = False
        self.kill_count = 0
        self.wait_called = False
        self.communicate_called = False
        self.stdout = _FakeStream(stdout_lines or [])
        self.stderr = _FakeStderr(stderr_content)
        self._wait_raises_timeout = wait_raises_timeout
        # If the wait_for timeout fires, the harness may have called either
        # wait() or communicate() — make both raise so we cover both paths
        # without the test needing to know which branch the harness picks.
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
        self.kill_count += 1
        # Real subprocess.kill() sets returncode to -9 (SIGKILL) on Unix
        self.returncode = -9

    async def communicate(self):
        self.communicate_called = True
        if self._communicate_raises_timeout:
            raise asyncio.TimeoutError()
        # Drain stdout into a single bytes blob for parity with the real API
        all_lines = []
        async for raw in self.stdout:
            all_lines.append(raw)
        return (b"".join(all_lines), self.stderr.content)


# ─── Gap (a): harness timeout kills subprocess ──────────────────────────


class TestHarnessTimeoutKillsSubprocess:
    """When a harness times out, the subprocess must be killed AND the
    TaskResult must report success=False with a timeout error message.

    These tests pin down the contract: a hanging agent CLI must not leak
    the subprocess to the OS, and the orchestrator must see a failure.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", ALL_CLI_HARNESSES)
    async def test_timeout_kills_subprocess_and_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """A hanging subprocess is killed when wait_for times out, and
        the harness returns a TaskResult with success=False."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        # Simulate wait() raising TimeoutError as if asyncio.wait_for had
        # cancelled it. This is what the harness should see on timeout.
        proc = _FakeProcess(
            returncode=None,
            stdout_lines=[],
            stderr_content=b"",
            wait_raises_timeout=True,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test",
                {
                    "working_dir": str(tmp_path),
                    "timeout_seconds": 1,
                },
            )

        # Result must be a failure
        assert result.success is False, (
            f"{name}: timed-out execution should yield success=False, got "
            f"success={result.success}, error={result.error!r}"
        )
        # Error must mention timeout
        assert result.error is not None
        assert "timed out" in result.error.lower(), (
            f"{name}: error message should mention timeout, got {result.error!r}"
        )
        # The subprocess must have been killed so it does not leak
        assert proc.kill_called, (
            f"{name}: timeout must kill the subprocess to avoid leaks"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", ALL_CLI_HARNESSES)
    async def test_timeout_kills_subprocess_when_communicate_times_out(
        self, harness_cls, name, tmp_path
    ):
        """When the subprocess is read via communicate() and it times out,
        the subprocess must still be killed."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        proc = _FakeProcess(
            returncode=None,
            stdout_lines=[],
            stderr_content=b"",
            communicate_raises_timeout=True,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test",
                {
                    "working_dir": str(tmp_path),
                    "timeout_seconds": 1,
                },
            )

        assert result.success is False
        assert proc.kill_called, (
            f"{name}: communicate-timeout path must also kill the subprocess"
        )


# ─── Gap (b): non-zero exit / stderr-only → TaskResult.success=False ────


class TestHarnessNonZeroExit:
    """When the agent CLI exits with a non-zero status code, the harness
    must report success=False with the stderr text surfaced as the error."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", ALL_CLI_HARNESSES)
    async def test_non_zero_exit_returns_failed_with_stderr(
        self, harness_cls, name, tmp_path
    ):
        """A non-zero exit code yields success=False and the stderr text
        becomes the error field."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stderr_text = "Error: model not available\n"
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False, (
            f"{name}: non-zero exit should yield success=False, got "
            f"success={result.success}, error={result.error!r}"
        )
        # Stderr text must be surfaced as the error
        assert result.error is not None
        assert "model not available" in result.error, (
            f"{name}: stderr text should be surfaced as the error, "
            f"got error={result.error!r}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", ALL_CLI_HARNESSES)
    async def test_stderr_only_output_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """A process that writes only to stderr (empty stdout, non-zero
        exit) still yields success=False with the stderr text."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stderr_text = "panic: runtime error: invalid memory address\n"
        proc = _FakeProcess(
            returncode=2,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert result.error is not None
        assert "panic" in result.error
        # stdout is empty — output should be empty or near-empty
        assert not result.output or result.output.strip() == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", ALL_CLI_HARNESSES)
    async def test_non_zero_exit_with_partial_stdout_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """Partial stdout + non-zero exit: success=False; stdout is
        preserved for debugging, stderr is the error."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stdout_lines = [b"partial output before crash\n"]
        stderr_text = "fatal: command failed\n"
        proc = _FakeProcess(
            returncode=137,
            stdout_lines=stdout_lines,
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "fatal" in (result.error or "")
        # Partial stdout preserved for forensics
        assert "partial output" in result.output


# ─── Gap (c): API-harness HTTP error paths (minimax / glm) ──────────────


class TestApiHarnessHttpErrors:
    """minimax and glm invoke the opencode CLI, which in turn calls the
    model API. When the API returns an HTTP error, opencode propagates it
    via non-zero exit + stderr. The harness must surface this as a failure.

    We mock the subprocess to simulate the opencode CLI's HTTP error
    output for each error class:
      - 5xx server error
      - 4xx auth/permission error
      - network / connection error
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", API_HARNESSES)
    async def test_http_500_server_error_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """HTTP 500 from the model API → TaskResult.success=False with
        the error surfaced."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        # opencode's typical error format for upstream HTTP failures
        stderr_text = (
            "Error: API request failed: 500 Internal Server Error\n"
            "  upstream: model API returned status 500\n"
        )
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "500" in (result.error or ""), (
            f"{name}: HTTP 500 must be visible in the error message, "
            f"got error={result.error!r}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", API_HARNESSES)
    async def test_http_401_auth_error_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """HTTP 401 (invalid / expired API key) → TaskResult.success=False."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stderr_text = (
            "Error: Authentication failed: 401 Unauthorized\n"
            "  hint: check API key in env or config\n"
        )
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "401" in (result.error or "")
        assert "Unauthorized" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", API_HARNESSES)
    async def test_http_429_rate_limit_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """HTTP 429 (rate limit) → TaskResult.success=False. This is a
        non-recoverable failure from the harness's perspective (retry
        logic is the caller's responsibility)."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stderr_text = (
            "Error: Rate limit exceeded: 429 Too Many Requests\n"
            "  retry-after: 30\n"
        )
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "429" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", API_HARNESSES)
    async def test_network_connection_error_returns_failed(
        self, harness_cls, name, tmp_path
    ):
        """Connection refused / DNS failure → TaskResult.success=False
        with the network error surfaced."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stderr_text = (
            "Error: failed to connect to model API\n"
            "  cause: dial tcp: lookup api.example.com: no such host\n"
        )
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=[],
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "connect" in (result.error or "").lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("harness_cls,name", API_HARNESSES)
    async def test_http_error_includes_partial_stdout_for_debugging(
        self, harness_cls, name, tmp_path
    ):
        """When an HTTP error occurs after partial output, the partial
        stdout is preserved for debugging."""
        cfg = AgentConfig(cli_command="fake-cli", capabilities=["writing"])
        harness = harness_cls(cfg)

        stdout_lines = [b'{"type":"text","text":"thinking about it..."}\n']
        stderr_text = "Error: API request failed: 502 Bad Gateway\n"
        proc = _FakeProcess(
            returncode=1,
            stdout_lines=stdout_lines,
            stderr_content=stderr_text.encode("utf-8"),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "test", {"working_dir": str(tmp_path)}
            )

        assert result.success is False
        assert "502" in (result.error or "")
        # Partial stdout is preserved
        assert "thinking about it" in result.output