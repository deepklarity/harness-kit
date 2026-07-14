"""Google Antigravity CLI (`agy`) harness.

`agy` replaced the deprecated `gemini-cli` for individuals. It is a
non-drop-in successor: `-p/--print` exists, `--dangerously-skip-permissions`
auto-approves every tool-permission request (the single non-interactive
gate in v1.0.x), and there is no `--output-format json` flag — output is
plain text, so the harness defers to the streaming fallback path in
`extract_text_from_stream()` rather than parsing JSON Lines.

Default command shape::

    agy -p "<prompt>" --dangerously-skip-permissions [--model <model>]

The skip-permissions flag is the documented mechanism for one-shot
non-interactive execution (the legacy `--headless --approve all` pair
does not exist in agy 1.0.x). The MCP config location is left to the
operator's host-side smoke run; auto-discovery (if any) and any
project-local settings path that `agy` reads will be wired in once
observed on a real install.
"""

import asyncio
import shlex
import shutil
import time
from typing import AsyncIterator

from odin.harnesses.base import (
    BaseHarness,
    read_with_tee,
    extract_text_from_stream,
    SUBPROCESS_STREAM_LIMIT,
    terminate_subprocess,
    validate_odin_status,
    validate_odin_status_full,
)
from odin.harnesses.registry import register_harness
from odin.models import AgentConfig, TaskResult
from typing import Any, Dict


# agy 1.0.x exposes a single non-interactive gate:
# `--dangerously-skip-permissions` auto-approves every tool-permission
# request so the one-shot prompt runs to completion without prompts.
AGY_DEFAULT_EXECUTE_ARGS = "--dangerously-skip-permissions"


@register_harness("agy")
class AgyHarness(BaseHarness):
    """Harness for the Google Antigravity CLI (`agy`)."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self._cli = config.cli_command or "agy"

    @property
    def name(self) -> str:
        return "Agy"

    def build_execute_command(self, prompt: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "-p", prompt]
        extra = self.config.execute_args or AGY_DEFAULT_EXECUTE_ARGS
        cmd.extend(shlex.split(extra))
        if context.get("model"):
            cmd.extend(["--model", context["model"]])
        # agy (v1.0.x) emits plain text — not stream-json — so we don't
        # request structured framing. The base extract_text_from_stream()
        # handles plain output correctly: it falls back to passthrough
        # when the first non-empty line doesn't start with '{'.
        return cmd

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        start = time.monotonic()
        working_dir = context.get("working_dir")
        output_file = context.get("output_file")
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

            if output_file:
                stdout_text = await read_with_tee(proc, output_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            else:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_text = extract_text_from_stream(
                    stdout_bytes.decode("utf-8", errors="replace")
                )

            duration = (time.monotonic() - start) * 1000
            stderr_text = ""
            if proc.stderr:
                try:
                    remaining = await asyncio.wait_for(
                        proc.stderr.read(), timeout=5
                    )
                    stderr_text = remaining.decode("utf-8", errors="replace")
                except (asyncio.TimeoutError, Exception):
                    pass

            self._current_pid = None
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
                meta: Dict[str, Any] = {}
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
            return TaskResult(
                success=False,
                output=stdout_text,
                error=stderr_text,
                duration_ms=round(duration, 1),
                agent=self.name,
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

    def build_interactive_command(
        self, system_prompt_file: str, context: dict
    ) -> list[str] | None:
        # Interactive mode reuses the --dangerously-skip-permissions gate.
        # Operators who want a different interactive default can override
        # via execute_args on the AgentConfig.
        cmd = [
            self._cli,
            "-i",
            f"__FILE__:{system_prompt_file}",
        ]
        extra = self.config.execute_args or AGY_DEFAULT_EXECUTE_ARGS
        cmd.extend(shlex.split(extra))
        model = context.get("model")
        if model:
            cmd.extend(["--model", model])
        return cmd

    async def is_available(self) -> bool:
        return shutil.which(self._cli) is not None
