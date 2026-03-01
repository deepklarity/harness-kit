"""Claude Code CLI harness."""

import asyncio
import json
import shlex
import shutil
import time
from pathlib import Path
from typing import AsyncIterator

from odin.harnesses.base import BaseHarness, read_with_tee, read_with_trace, extract_text_from_stream, SUBPROCESS_STREAM_LIMIT
from odin.harnesses.registry import register_harness
from odin.models import AgentConfig, TaskResult


def _extract_token_usage(raw_output: str) -> dict:
    """Extract token usage from Claude stream-json output.

    Handles two formats:
    - modelUsage event (Claude Code CLI): aggregate usage in the final line
      {"modelUsage":{"model-name":{"inputTokens":N,"outputTokens":M,...}}}
    - step_finish events (opencode/kilo CLIs): per-step tokens summed
      {"type":"step_finish","part":{"tokens":{"input":N,"output":M,...}}}

    modelUsage is preferred when present (more accurate aggregate).
    """
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    model_usage_found = False

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Claude Code CLI: final line has modelUsage with aggregate counts
        model_usage = obj.get("modelUsage")
        if isinstance(model_usage, dict):
            agg_in = agg_out = agg_cache_read = agg_cache_write = 0
            for model_data in model_usage.values():
                if isinstance(model_data, dict):
                    agg_in += model_data.get("inputTokens", 0)
                    agg_out += model_data.get("outputTokens", 0)
                    agg_cache_read += model_data.get("cacheReadInputTokens", 0)
                    agg_cache_write += model_data.get("cacheCreationInputTokens", 0)
            if agg_in or agg_out:
                totals = {
                    "input_tokens": agg_in,
                    "output_tokens": agg_out,
                    "cache_read_tokens": agg_cache_read,
                    "cache_write_tokens": agg_cache_write,
                }
                model_usage_found = True
            continue

        # Fallback: step_finish events (opencode/kilo format)
        if obj.get("type") != "step_finish":
            continue
        tokens = (obj.get("part") or {}).get("tokens") or (obj.get("result", {}) or {}).get("tokens", {})
        if not tokens:
            continue
        if not model_usage_found:
            totals["input_tokens"] += tokens.get("input", 0)
            totals["output_tokens"] += tokens.get("output", 0)
            cache = tokens.get("cache", {})
            if cache:
                totals["cache_read_tokens"] += cache.get("read", 0)
                totals["cache_write_tokens"] += cache.get("write", 0)

    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals if totals["total_tokens"] > 0 else {}


@register_harness("claude")
class ClaudeHarness(BaseHarness):
    """Harness for Claude Code CLI."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self._cli = config.cli_command or "claude"

    @property
    def name(self) -> str:
        return "Claude Code"

    def build_execute_command(self, prompt: str, context: dict) -> list[str] | None:
        cmd = [self._cli, "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if self.config.execute_args:
            cmd.extend(shlex.split(self.config.execute_args))
        if context.get("model"):
            cmd.extend(["--model", context["model"]])
        if context.get("mcp_config"):
            cmd.extend(["--mcp-config", context["mcp_config"]])
        if context.get("mcp_allowed_tools"):
            cmd.extend(["--allowedTools", ",".join(context["mcp_allowed_tools"])])
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

            usage = {}
            if trace_file and output_file:
                stdout_text = await read_with_trace(proc, output_file, trace_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                try:
                    raw_str = Path(trace_file).read_text(encoding="utf-8")
                    usage = _extract_token_usage(raw_str)
                except OSError:
                    usage = {}
            elif output_file:
                raw_str = await read_with_tee(proc, output_file)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                stdout_text = raw_str
                usage = _extract_token_usage(raw_str)
            else:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                raw_str = stdout_bytes.decode("utf-8", errors="replace")
                stdout_text = extract_text_from_stream(raw_str)
                usage = _extract_token_usage(raw_str)

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
                return TaskResult(
                    success=True,
                    output=stdout_text,
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
        if context.get("mcp_config"):
            cmd.extend(["--mcp-config", context["mcp_config"]])
        if context.get("mcp_allowed_tools"):
            cmd.extend(["--allowedTools", ",".join(context["mcp_allowed_tools"])])
        return cmd

    async def is_available(self) -> bool:
        return shutil.which(self._cli) is not None
