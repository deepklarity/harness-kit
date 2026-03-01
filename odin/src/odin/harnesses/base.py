"""Abstract base class for agent harnesses."""

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator, List, Dict, Optional

from odin.models import AgentConfig, TaskResult

# 10 MiB — asyncio.StreamReader default is 64 KiB which is too small for
# large JSON stream events emitted by CLI agents (e.g. result events with
# full output can exceed 64 KiB on a single line, causing LimitOverrunError).
SUBPROCESS_STREAM_LIMIT = 10 * 1024 * 1024


class BaseHarness(ABC):
    """Base class for all agent harnesses.

    Subclasses must implement execute() and is_available().
    Optionally override execute_streaming() and execute_conversation_turn()
    for interactive plan mode.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._current_pid: Optional[int] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Harness display name."""
        ...

    @abstractmethod
    async def execute(self, prompt: str, context: dict) -> TaskResult:
        """Execute a task prompt and return result."""
        ...

    async def execute_streaming(self, prompt: str, context: dict) -> AsyncIterator[str]:
        """Execute and yield output chunks as they arrive.

        Default: calls execute() and yields the full output.
        CLI harnesses override to stream subprocess stdout line-by-line.
        """
        result = await self.execute(prompt, context)
        if result.output:
            yield result.output

    async def execute_conversation_turn(
        self, messages: List[Dict[str, str]], context: dict
    ) -> AsyncIterator[str]:
        """Execute a multi-turn conversation and yield output chunks.

        Default: flattens messages into a single prompt and calls
        execute_streaming(). API harnesses override to pass the full
        messages array.
        """
        # Flatten messages into a single prompt
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(content)
        prompt = "\n\n".join(parts)
        async for chunk in self.execute_streaming(prompt, context):
            yield chunk

    def build_execute_command(self, prompt: str, context: dict) -> Optional[List[str]]:
        """Build CLI command list for one-shot task execution.

        Returns None for API harnesses (they don't have a CLI command).
        CLI harnesses return the full command with prompt included.
        """
        return None

    def build_interactive_command(self, system_prompt_file: str, context: dict) -> Optional[List[str]]:
        """Build command for interactive (non-one-shot) CLI mode.

        Args:
            system_prompt_file: Path to a file containing the system prompt.
                Using a file avoids shell escaping issues with long prompts.
            context: Execution context (working_dir, model, etc.)

        Returns None for API harnesses (they can't run interactively in tmux).
        CLI harnesses return the command without -p flag.
        """
        return None

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this harness CLI/API is reachable."""
        ...


async def read_with_tee(
    proc: asyncio.subprocess.Process, output_file: str
) -> str:
    """Read stdout line-by-line, writing each line to output_file and accumulating.

    Used by CLI harnesses when context["output_file"] is set to provide
    tail -f style observability into running tasks.
    """
    lines = []
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            lines.append(line)
            f.write(line)
            f.flush()
    return "".join(lines)


async def read_with_trace(
    proc: asyncio.subprocess.Process,
    output_file: str,
    trace_file: str,
) -> str:
    """Read stdout, writing raw JSON to trace_file and extracted text to output_file.

    The trace_file gets the raw stream-json output (the full execution trace).
    The output_file gets extracted plain text for backward compat with `odin tail`.
    Returns the extracted plain-text output.
    """
    raw_lines: list[str] = []
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(trace_file).parent.mkdir(parents=True, exist_ok=True)

    with open(trace_file, "w") as tf, open(output_file, "w") as of:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            raw_lines.append(line)
            # Write raw JSON line to trace file
            tf.write(line)
            tf.flush()
            # Extract text content and write to output file for tail -f
            text = extract_text_from_line(line)
            if text:
                of.write(text)
                of.flush()

    raw_output = "".join(raw_lines)
    return extract_text_from_stream(raw_output)


def extract_text_from_line(line: str) -> str:
    """Extract displayable text content from a single JSON line.

    Handles both stream-json (Claude/Gemini/Qwen) and opencode/kilo JSON formats.
    Returns empty string if the line has no text content.
    """
    stripped = line.strip()
    if not stripped:
        return ""
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — pass through as plain text
        return line

    # Claude stream-json: {"type": "content_block_delta", "delta": {"text": "..."}}
    if obj.get("type") == "content_block_delta":
        delta = obj.get("delta", {})
        return delta.get("text", "")

    # Claude stream-json: {"type": "result", "result": "..."}
    if obj.get("type") == "result":
        result = obj.get("result", "")
        if isinstance(result, str):
            return result

    # Gemini/GLM/MiniMax stream-json: {"type": "text", "text": "..."}
    if obj.get("type") == "text":
        # opencode/kilo JSON: {"type": "text", "content": "..."}
        if "content" in obj:
            return obj.get("content", "")
        return obj.get("text", "")

    # Gemini CLI: {"type": "message", "role": "assistant", "content": "..."}
    if obj.get("type") == "message" and obj.get("role") == "assistant":
        return obj.get("content", "")

    # Qwen CLI: {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
    if obj.get("type") == "assistant":
        msg = obj.get("message", {})
        if isinstance(msg, dict):
            parts = []
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(text)
            return "".join(parts)

    # opencode/kilo: {"type": "step_finish", "content": "..."}
    if obj.get("type") == "step_finish":
        return obj.get("content", "")

    # Codex CLI: {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    if obj.get("type") == "item.completed":
        item = obj.get("item", {})
        if isinstance(item, dict) and item.get("type") == "agent_message":
            return item.get("text", "")
        return ""

    return ""


def stream_json_is_complete(output_tail: str) -> bool:
    """Check if a stream-json output tail contains a completion signal.

    Scans the last few lines for a JSON object with ``{"type":"result",...}``
    which CLI agents (Claude, Gemini, etc.) emit when the task finishes.
    Also detects error-type results as completion.

    Args:
        output_tail: The last N bytes/chars of the output file (typically 4-8 KiB).

    Returns:
        True if a result line is found, meaning the agent has finished.
    """
    for line in reversed(output_tail.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return True
    return False


def extract_text_from_stream(raw_output: str) -> str:
    """Extract all plain-text content from a raw stream-json output.

    Parses each line and concatenates the text fragments.
    Falls back to returning the raw output if no JSON is detected.
    """
    if not raw_output.strip():
        return ""

    # Check if this looks like JSON stream (first non-empty line starts with '{')
    first_line = ""
    for line in raw_output.splitlines():
        if line.strip():
            first_line = line.strip()
            break

    if not first_line.startswith("{"):
        # Not JSON stream — return as-is (e.g. codex plain text)
        return raw_output

    text_parts: list[str] = []
    for line in raw_output.splitlines():
        text = extract_text_from_line(line)
        if text:
            text_parts.append(text)

    return "".join(text_parts) if text_parts else raw_output
