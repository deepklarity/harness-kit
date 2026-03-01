"""Qwen CLI harness."""

import asyncio
import shlex
import shutil
import time
from typing import AsyncIterator

from odin.harnesses.base import BaseHarness, read_with_tee, read_with_trace, extract_text_from_stream, SUBPROCESS_STREAM_LIMIT
from odin.harnesses.registry import register_harness
from odin.models import AgentConfig, TaskResult


@register_harness("qwen")
class QwenHarness(BaseHarness):
    """Harness for Alibaba Qwen CLI."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self._cli = config.cli_command or "qwen"

    @property
    def name(self) -> str:
        return "Qwen"

    def build_execute_command(self, prompt: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "-p", prompt, "--output-format", "stream-json"]
        extra = self.config.execute_args or "--yolo"
        cmd.extend(shlex.split(extra))
        if context.get("model"):
            cmd.extend(["--model", context["model"]])
        # Qwen CLI has no --mcp-config flag. MCP servers are configured via
        # .qwen/settings.json in the working directory (auto-discovered).
        return cmd

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        start = time.monotonic()
        working_dir = context.get("working_dir")
        output_file = context.get("output_file")
        trace_file = context.get("trace_file")
        timeout_seconds = context.get("timeout_seconds", 300)
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        try:
            cmd = self.build_execute_command(prompt, context)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                limit=SUBPROCESS_STREAM_LIMIT,
            )
            self._current_pid = proc.pid

            if trace_file and output_file:
                stdout_text = await read_with_trace(proc, output_file, trace_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            elif output_file:
                stdout_text = await read_with_tee(proc, output_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            else:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_text = extract_text_from_stream(
                    stdout_bytes.decode("utf-8", errors="replace")
                )

            duration = (time.monotonic() - start) * 1000
            stderr_text = ""
            if proc.stderr:
                try:
                    remaining = await asyncio.wait_for(proc.stderr.read(), timeout=5)
                    stderr_text = remaining.decode("utf-8", errors="replace")
                except (asyncio.TimeoutError, Exception):
                    pass

            self._current_pid = None
            if proc.returncode == 0:
                return TaskResult(
                    success=True,
                    output=stdout_text,
                    duration_ms=round(duration, 1),
                    agent=self.name,
                )
            else:
                return TaskResult(
                    success=False,
                    output=stdout_text,
                    error=stderr_text,
                    duration_ms=round(duration, 1),
                    agent=self.name,
                )
        except asyncio.TimeoutError:
            self._current_pid = None
            timeout_msg = (
                f"Command timed out after {timeout_seconds}s"
                if timeout_seconds and timeout_seconds > 0
                else "Command timed out"
            )
            return TaskResult(
                success=False,
                error=timeout_msg,
                duration_ms=(time.monotonic() - start) * 1000,
                agent=self.name,
            )
        except FileNotFoundError:
            self._current_pid = None
            return TaskResult(
                success=False,
                error=f"CLI '{self._cli}' not found on PATH",
                agent=self.name,
            )

    async def execute_streaming(self, prompt: str, context: dict) -> AsyncIterator[str]:
        working_dir = context.get("working_dir")
        cmd = self.build_execute_command(prompt, context)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                limit=SUBPROCESS_STREAM_LIMIT,
            )
            self._current_pid = proc.pid
            async for line in proc.stdout:
                yield line.decode("utf-8", errors="replace")
            await proc.wait()
            self._current_pid = None
        except FileNotFoundError:
            self._current_pid = None
            yield f"[error] CLI '{self._cli}' not found on PATH\n"

    def build_interactive_command(self, system_prompt_file: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "--system-prompt", f"__FILE__:{system_prompt_file}"]
        model = context.get("model")
        if model:
            cmd.extend(["--model", model])
        # Qwen CLI auto-discovers MCP from .qwen/settings.json — no flag needed.
        return cmd

    async def is_available(self) -> bool:
        return shutil.which(self._cli) is not None
