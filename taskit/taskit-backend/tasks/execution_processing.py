"""Execution result processing — extract, parse, and compose from raw agent output.

Ported from odin/src/odin/orchestrator.py to live alongside the data it produces
(TaskComment, TaskHistory). The orchestrator sends raw execution payloads; this
module owns all text extraction and formatting.
"""

import json
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("taskit.execution_processing")


def extract_agent_text(raw_output: str) -> Tuple[str, dict]:
    """Extract human-readable agent text from structured CLI output.

    Returns (extracted_text, usage_dict).

    Agent CLIs (Claude Code, Qwen, etc.) stream structured JSON where the
    agent's text response is embedded inside JSON string values.  The
    ODIN-STATUS envelope lives inside those values, so plain-text search
    on the raw output crosses JSON boundaries and produces broken content.

    Formats handled:
    - **Claude Code JSONL**: ``{"type":"text","part":{"text":"..."}}``
    - **Gemini/GLM stream-json**: ``{"type":"text","text":"..."}``
    - **Gemini CLI message**: ``{"type":"message","role":"assistant","content":"..."}``
    - **Claude stream-json deltas**: ``{"type":"content_block_delta","delta":{"text":"..."}}``
    - **Claude/Gemini result**: ``{"type":"result","result":"...","stats":{...}}``
    - **Qwen CLI assistant**: ``{"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}``
    - **Qwen CLI result**: ``{"subtype":"success","result":"...","usage":{...}}``
    - **Codex JSONL**: ``{"type":"item.completed","item":{"type":"agent_message","text":"..."}}``
    - **Codex usage**: ``{"type":"turn.completed","usage":{"input_tokens":...,"output_tokens":...}}``
    - **MiniMax/opencode**: ``{"type":"text","text":"..."}`` and ``{"type":"step_finish"}``
    - **Codex plain text** (fallback): detected by ``OpenAI Codex v`` header, extracts ``codex`` blocks.
    - **Plain text**: returned as-is (mock harness, direct execution).
    """
    if not raw_output or not raw_output.strip():
        return (raw_output, {})

    lines = raw_output.strip().splitlines()

    text_parts: list[str] = []
    json_line_count = 0
    extracted_usage: dict = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(obj, dict):
            continue

        json_line_count += 1
        event_type = obj.get("type")

        # Claude Code: last line has "modelUsage":{"model-name":{"inputTokens":...}}
        model_usage = obj.get("modelUsage")
        if isinstance(model_usage, dict):
            total_input = total_output = total_cache_read = total_cache_write = 0
            for model_data in model_usage.values():
                if isinstance(model_data, dict):
                    total_input += model_data.get("inputTokens", 0)
                    total_output += model_data.get("outputTokens", 0)
                    total_cache_read += model_data.get("cacheReadInputTokens", 0)
                    total_cache_write += model_data.get("cacheCreationInputTokens", 0)
            if total_input or total_output:
                extracted_usage = {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                    "cache_read_input_tokens": total_cache_read,
                    "cache_creation_input_tokens": total_cache_write,
                }
            if not event_type:
                continue

        # MiniMax/GLM (opencode/kilo): step_finish events with "tokens":{...}
        if event_type == "step_finish":
            part = obj.get("part", {})
            tokens = part.get("tokens") if isinstance(part, dict) else None
            if isinstance(tokens, dict) and tokens.get("total"):
                if not extracted_usage:
                    extracted_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                extracted_usage["input_tokens"] += tokens.get("input", 0)
                extracted_usage["output_tokens"] += tokens.get("output", 0)
                extracted_usage["total_tokens"] += tokens.get("total", 0)
            continue

        # Claude Code JSONL: {"type":"text","part":{"text":"..."}}
        if event_type == "text":
            part = obj.get("part", {})
            text = part.get("text", "")
            if text:
                text_parts.append(text)
                continue
            # Gemini/GLM/MiniMax stream-json: {"type":"text","text":"..."}
            text = obj.get("text", "")
            if text:
                text_parts.append(text)
                continue

        # Gemini CLI: {"type":"message","role":"assistant","content":"...","delta":true}
        if event_type == "message":
            if obj.get("role") == "assistant":
                text = obj.get("content", "")
                if text:
                    text_parts.append(text)
                    continue

        # Qwen CLI: {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
        if event_type == "assistant":
            msg = obj.get("message", {})
            if isinstance(msg, dict):
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            text_parts.append(text)
            continue

        # Claude stream-json: {"type":"content_block_delta","delta":{"text":"..."}}
        if event_type == "content_block_delta":
            delta = obj.get("delta", {})
            text = delta.get("text", "")
            if text:
                text_parts.append(text)
                continue

        # Claude/Gemini stream-json: {"type":"result","result":"...","stats":{...}}
        if event_type == "result":
            result_text = obj.get("result", "")
            if isinstance(result_text, str) and result_text:
                text_parts.append(result_text)

            # Gemini: {"type":"result","stats":{"total_tokens":...}}
            stats = obj.get("stats", {})
            if isinstance(stats, dict) and stats.get("total_tokens"):
                extracted_usage = {
                    "total_tokens": stats.get("total_tokens"),
                    "input_tokens": stats.get("input_tokens"),
                    "output_tokens": stats.get("output_tokens"),
                }

            # Qwen: {"type":"result","subtype":"success","usage":{...}}
            usage = obj.get("usage", {})
            if isinstance(usage, dict) and usage.get("total_tokens"):
                extracted_usage = dict(usage)

            continue

        # Codex JSONL: {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        if event_type == "item.completed":
            item = obj.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    text_parts.append(text)
            continue

        # Codex JSONL: {"type":"turn.completed","usage":{"input_tokens":...,"output_tokens":...}}
        if event_type == "turn.completed":
            usage = obj.get("usage", {})
            if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
                input_t = usage.get("input_tokens", 0) or 0
                output_t = usage.get("output_tokens", 0) or 0
                extracted_usage = {
                    "total_tokens": input_t + output_t,
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                }
            continue

        # Qwen CLI fallback: {"subtype":"success","result":"..."}
        if obj.get("subtype") == "success" and "result" in obj:
            result_text = obj.get("result", "")
            if result_text:
                text_parts.append(result_text)
            # Also check for usage here
            usage = obj.get("usage", {})
            if isinstance(usage, dict) and usage.get("total_tokens"):
                extracted_usage = dict(usage)

    if json_line_count > 0 and text_parts:
        extracted = "\n".join(text_parts)
        logger.debug(
            "extract_agent_text: raw_length=%d, json_lines=%d, format=JSON, extracted_length=%d",
            len(raw_output), json_line_count, len(extracted),
        )
        return (extracted, extracted_usage)

    # Codex plain-text format: detect by "OpenAI Codex v" header line.
    # Extract only "codex" blocks (agent messages), skip header, user,
    # thinking, exec blocks, and the "tokens used" footer.
    codex_text = _extract_codex_text(lines)
    if codex_text is not None:
        logger.debug(
            "extract_agent_text: raw_length=%d, format=codex, extracted_length=%d",
            len(raw_output), len(codex_text),
        )
        return (codex_text, extracted_usage)

    logger.debug(
        "extract_agent_text: raw_length=%d, format=plain_text",
        len(raw_output),
    )
    return (raw_output, extracted_usage)


def compose_comment(
    verb: str,
    duration_ms: Optional[float],
    metadata: dict,
    summary_text: str,
) -> str:
    """Compose a metrics-inline comment from execution data.

    Output format:
      "Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)\\n\\nSummary text"
    """
    metrics_parts: list[str] = []
    if duration_ms:
        metrics_parts.append(f"{duration_ms / 1000:.1f}s")
    usage = metadata.get("usage", {})
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total:
            input_t = usage.get("input_tokens") or usage.get("prompt_tokens")
            output_t = usage.get("output_tokens") or usage.get("completion_tokens")
            if input_t and output_t:
                metrics_parts.append(
                    f"{total:,} tokens ({input_t:,} in / {output_t:,} out)"
                )
            else:
                metrics_parts.append(f"{total:,} tokens")

    if metrics_parts:
        metrics_line = f"{verb} in " + " · ".join(metrics_parts)
        return f"{metrics_line}\n\n{summary_text}"
    return summary_text


def _extract_codex_text(lines: list[str]) -> Optional[str]:
    """Extract agent messages from Codex CLI plain-text output.

    Codex output has a recognizable block structure::

        OpenAI Codex v0.101.0 (research preview)
        --------
        workdir: ...
        model: ...
        ...
        --------
        user
        <prompt>
        thinking
        <thinking content>
        codex
        <agent message>
        exec
        <shell command + result>
        tokens used
        N
        <possible duplicated final text>

    Returns the concatenated content of all ``codex`` blocks, or None if
    the output does not look like Codex format.
    """
    # Detect Codex format by the header line
    if not any(line.strip().startswith("OpenAI Codex v") for line in lines[:5]):
        return None

    # Known section headers in Codex output
    SECTION_HEADERS = {"user", "thinking", "codex", "exec", "tokens used"}

    # Parse into sections: find header lines, collect content between them
    codex_parts: list[str] = []
    current_section: Optional[str] = None
    content_lines: list[str] = []
    past_header = False  # Past the initial "--------" delimited header block

    # Skip the header block (everything up to and including the second "--------")
    separator_count = 0
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("--------"):
            separator_count += 1
            if separator_count >= 2:
                start_idx = i + 1
                break

    for line in lines[start_idx:]:
        stripped = line.strip()

        # Check if this line is a section header
        if stripped.lower() in SECTION_HEADERS:
            # Save previous section if it was a codex block
            if current_section == "codex" and content_lines:
                codex_parts.append("\n".join(content_lines))
            current_section = stripped.lower()
            content_lines = []
            continue

        # Accumulate content for current section
        if current_section is not None:
            content_lines.append(line)

    # Don't forget the last section
    if current_section == "codex" and content_lines:
        codex_parts.append("\n".join(content_lines))

    if not codex_parts:
        return None

    return "\n\n".join(part.strip() for part in codex_parts if part.strip())


def compute_usage_from_trace(task) -> dict:
    """Compute token usage on-the-fly from the task's trace comment.

    The orchestrator posts raw JSONL as a TaskComment with
    attachments=["trace:execution_jsonl"]. This is the source of truth.
    Re-running extract_agent_text() here means fixing extraction code
    retroactively fixes all historical data.

    Returns the usage dict (or {} if no trace comment found).
    """
    from .models import TaskComment

    # Find the latest trace comment. We avoid JSONField __contains lookup
    # because it's not supported on SQLite (used in tests).
    comments = (
        TaskComment.objects
        .filter(task=task)
        .order_by("-created_at")
    )
    trace_comment = None
    for c in comments:
        if isinstance(c.attachments, list) and "trace:execution_jsonl" in c.attachments:
            trace_comment = c
            break
    if not trace_comment:
        return {}

    _, usage = extract_agent_text(trace_comment.content)
    return usage


def parse_envelope(output: str) -> Tuple[str, Optional[bool], Optional[str]]:
    """Parse the ODIN-STATUS envelope from agent output.

    Returns (clean_output, parsed_success, summary).
    If the envelope is not found, returns (output, None, None).
    """
    separator = "-------ODIN-STATUS-------"
    summary_separator = "-------ODIN-SUMMARY-------"

    idx = output.rfind(separator)
    if idx == -1:
        logger.debug("parse_envelope: no envelope found")
        return (output, None, None)

    clean_output = output[:idx].rstrip()
    tail = output[idx + len(separator):]

    summary_idx = tail.find(summary_separator)
    if summary_idx != -1:
        status_text = tail[:summary_idx].strip().upper()
        summary = tail[summary_idx + len(summary_separator):].strip()
    else:
        status_text = tail.strip().upper()
        summary = None

    parsed_success = None
    if "SUCCESS" in status_text:
        parsed_success = True
    elif "FAILED" in status_text or "FAIL" in status_text:
        parsed_success = False

    logger.debug(
        "parse_envelope: found=%s, parsed_success=%s", True, parsed_success,
    )
    return (clean_output, parsed_success, summary)
