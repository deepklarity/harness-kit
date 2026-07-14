"""Gemini CLI harness."""

import asyncio
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from odin.harnesses.base import BaseHarness, read_with_tee, read_with_trace, extract_text_from_stream, extract_token_usage, SUBPROCESS_STREAM_LIMIT, terminate_subprocess, validate_odin_status, validate_odin_status_full
from odin.harnesses.registry import register_harness
from odin.models import AgentConfig, TaskResult


@register_harness("gemini")
class GeminiHarness(BaseHarness):
    """Harness for Google Gemini CLI."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self._cli = config.cli_command or "gemini"

    @property
    def name(self) -> str:
        return "Gemini"

    def build_execute_command(self, prompt: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "-p", prompt, "--output-format", "stream-json"]
        extra = self.config.execute_args or "--yolo"
        cmd.extend(shlex.split(extra))
        if context.get("model"):
            cmd.extend(["--model", context["model"]])
        # Gemini CLI has no --mcp-config flag. MCP servers are configured via
        # .gemini/settings.json in the working directory (auto-discovered).
        return cmd

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        start = time.monotonic()
        working_dir = context.get("working_dir")
        output_file = context.get("output_file")
        trace_file = context.get("trace_file")
        timeout_seconds = context.get("timeout_seconds", 300)
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        proc: asyncio.subprocess.Process | None = None
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

            usage = {}
            if trace_file and output_file:
                stdout_text = await read_with_trace(proc, output_file, trace_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                try:
                    usage = extract_token_usage(Path(trace_file).read_text(encoding="utf-8"))
                except OSError:
                    usage = {}
            elif output_file:
                stdout_text = await read_with_tee(proc, output_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                usage = extract_token_usage(stdout_text)
            else:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                raw_str = stdout_bytes.decode("utf-8", errors="replace")
                stdout_text = extract_text_from_stream(raw_str)
                usage = extract_token_usage(raw_str)

            duration = (time.monotonic() - start) * 1000
            stderr_text = ""
            if proc.stderr:
                try:
                    remaining = await asyncio.wait_for(proc.stderr.read(), timeout=5)
                    stderr_text = remaining.decode("utf-8", errors="replace")
                except (asyncio.TimeoutError, Exception):
                    pass

            self._current_pid = None
            meta = {"usage": usage} if usage else {}
            if proc.returncode == 0:
                if context.get("validate_status", True):
                    status = validate_odin_status_full(
                        stdout_text,
                        worktree_path=context.get("working_dir"),
                    )
                    agent_success, agent_error = status.as_legacy_tuple()
                else:
                    agent_success, agent_error = True, None
                    status = None
                if status is not None and status.raw_block is not None:
                    meta["malformed_status"] = {
                        "raw_block": status.raw_block,
                        "inferred": status.inferred,
                        "inference_reason": status.inference_reason,
                    }
                return TaskResult(
                    success=agent_success,
                    output=stdout_text,
                    error=agent_error,
                    duration_ms=round(duration, 1),
                    agent=self.name,
                    metadata=meta,
                )
            else:
                return TaskResult(
                    success=False,
                    output=stdout_text,
                    error=stderr_text,
                    duration_ms=round(duration, 1),
                    agent=self.name,
                    metadata=meta,
                )
        except asyncio.TimeoutError:
            self._current_pid = None
            if proc is not None:
                await terminate_subprocess(proc)
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

    @property
    def supports_system_prompt_flag(self) -> bool:
        return False

    def build_interactive_command(self, system_prompt_file: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "--prompt-interactive", f"__FILE__:{system_prompt_file}"]
        model = context.get("model")
        extra = self.config.execute_args or "--yolo"
        cmd.extend(shlex.split(extra))
        if model:
            cmd.extend(["--model", model])
        # Gemini CLI auto-discovers MCP from .gemini/settings.json — no flag needed.
        return cmd

    async def is_available(self) -> bool:
        return shutil.which(self._cli) is not None
