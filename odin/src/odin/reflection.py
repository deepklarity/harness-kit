"""Reflection audit module — structured code review by a stronger model.

Provides:
- build_reflection_prompt(): Generates the structured audit prompt
- parse_reflection_report(): Extracts sections from agent output
- reflect_task(): Orchestrates the full reflection flow
"""

import asyncio
import re
import tempfile
import time
import logging
from pathlib import Path

import httpx

from odin.harnesses import get_harness
from odin.harnesses.base import extract_text_from_stream
from odin.orchestrator import _truncate_trace

logger = logging.getLogger("odin.reflection")


def build_reflection_prompt(task_context: dict, custom_prompt: str = "") -> str:
    """Build a structured reflection audit prompt from task context.

    Args:
        task_context: Dict with keys: title, status, agent, model, duration_ms,
            tokens, description, execution_output, comments, dependencies.
        custom_prompt: Optional additional focus areas from the requester.

    Returns:
        Complete prompt string for the reviewer agent.
    """
    custom_section = ""
    if custom_prompt:
        custom_section = f"""
## ADDITIONAL FOCUS
{custom_prompt}
"""

    return f"""You are auditing a task executed by an AI agent.

## CONSTRAINTS
- READ-ONLY mode. Do NOT modify files, make commits, or run destructive commands.
- You MAY read files and grep to verify the agent's work.
- REPORT ONLY. Your first line of output MUST be "### Quality Assessment". No preamble, no narration, no meta-commentary, no permission requests, no conversational text before or after the report.
- Be concise. Bullet points only. 1-2 lines per finding. If a section has no findings, write "None."
- **Evaluate only the LATEST execution attempt.** This task may have been attempted by multiple agents or models previously. Comments are separated by "--- CURRENT ATTEMPT ---" when prior attempts exist. Use earlier history for understanding context only — do NOT penalize the current agent for failures, quota issues, or code quality problems from previous agents/models. Your verdict must reflect solely the current agent's work.

## TASK UNDER REVIEW
Title: {task_context.get('title', 'Unknown')}
Status: {task_context.get('status', 'Unknown')}
Agent: {task_context.get('agent', 'Unknown')} ({task_context.get('model', 'Unknown')})
Duration: {task_context.get('duration_ms', 'N/A')}ms | Tokens: {task_context.get('tokens', 'N/A')}

## [CTX:description] Task Description
{task_context.get('description', 'No description provided.')}

## [CTX:execution_result] Execution Output
{task_context.get('execution_output', 'No execution output available.')}

## [CTX:comments] Comments & Proof
{task_context.get('comments', 'No comments.')}

## [CTX:dependencies] Dependent Tasks
{task_context.get('dependencies', 'No dependencies.')}

## [CTX:metadata] Task Metadata
{task_context.get('metadata_summary', 'No metadata.')}
{custom_section}
## YOUR REPORT

You MUST output ALL 5 sections below, in this exact order, using these exact headers.
Start immediately with "### Quality Assessment" — no text before it.

### Quality Assessment
For each requirement in the task description, state MET or UNMET with a one-line reason.
Then list defects found (file:line, issue). If none: "No defects found."

### Slop Detection
List specific AI slop found: boilerplate, filler, unnecessary abstractions.
If none: "None."

### Actionable Improvements
Bullet list, max 5 items. Each: what, where (file:line), why.
If clean: "None."

### Agent Optimization
- Description clarity: [clear / missing X]
- Model tier: [overkill / appropriate / insufficient]
- Token efficiency: [efficient / wasted N tokens on X]
- Prompt improvement: [suggestion or "none"]

### Quota / Resource Failure
Check the execution output from the CURRENT attempt for signs of quota exhaustion or rate limiting:
- HTTP 429, "rate limit", "quota exceeded", "usage limit", "out of quota", "too many requests"
- Agent reporting it cannot proceed due to resource limits (not code errors)
IMPORTANT: Only flag quota issues from the current execution output. If comments mention quota failures from a PREVIOUS agent/model, that is historical context — do NOT treat it as a current failure.
If detected in current execution, state: "QUOTA_FAILURE: <agent_name>" (the agent that hit the limit).
If no quota/resource issue in current execution: "None."

### Verdict
Exactly one of: PASS | NEEDS_WORK | FAIL
If the task failed due to quota/resource exhaustion (not code quality), use FAIL with justification mentioning "quota" or "rate limit" so the system can reassign to a different agent.
Single-sentence justification. Nothing else after this.
"""


# Section header pattern: "### <SectionName>"
_SECTION_MAP = {
    "quality assessment": "quality_assessment",
    "slop detection": "slop_detection",
    "actionable improvements": "improvements",
    "agent optimization": "agent_optimization",
    "quota / resource failure": "quota_failure",
    "verdict": "verdict",
}


def _deduplicate_summary(lines: list[str]) -> list[str]:
    """Remove stuttered/duplicated lines from verdict summary.

    Agents sometimes repeat the verdict summary multiple times.
    Returns lines up to the first duplicate.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        normalized = line.strip().lower()
        if not normalized:
            result.append(line)
            continue
        if normalized in seen:
            break
        seen.add(normalized)
        result.append(line)
    return result


def _strip_odin_envelopes(text: str) -> str:
    """Remove all ODIN-STATUS/ODIN-SUMMARY envelopes from agent output.

    Agents sometimes append these envelopes to their reflection output.
    They're protocol framing, not part of the review content.
    """
    separator = "-------ODIN-STATUS-------"
    idx = text.find(separator)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def parse_reflection_report(raw_output: str) -> dict:
    """Parse structured agent output into report sections.

    Splits on ``### `` headers to extract named sections.

    Args:
        raw_output: Full text output from the reviewer agent.

    Returns:
        Dict with keys: quality_assessment, slop_detection, improvements,
        agent_optimization, verdict, verdict_summary. Missing sections
        default to empty strings.
    """
    result = {
        "quality_assessment": "",
        "slop_detection": "",
        "improvements": "",
        "agent_optimization": "",
        "quota_failure": "",
        "verdict": "",
        "verdict_summary": "",
    }

    if not raw_output or not raw_output.strip():
        return result

    # Split into sections by ### headers
    sections = re.split(r"^###\s+", raw_output, flags=re.MULTILINE)

    # If the first chunk (before any ### header) contains checklist-style content
    # (MET/UNMET bullets), treat it as quality_assessment — some agents skip the header
    preamble = sections[0].strip() if sections else ""
    if preamble and not result["quality_assessment"] and re.search(
        r"\b(MET|UNMET)\b", preamble
    ):
        # Strip leading narration line(s) before the first bullet
        bullet_match = re.search(r"^[-*]", preamble, flags=re.MULTILINE)
        if bullet_match:
            result["quality_assessment"] = preamble[bullet_match.start():].strip()

    for section in sections:
        if not section.strip():
            continue

        # First line is the header name, rest is content
        lines = section.split("\n", 1)
        header = lines[0].strip().lower()
        content = lines[1].strip() if len(lines) > 1 else ""

        field_name = _SECTION_MAP.get(header)
        if field_name and field_name != "verdict":
            result[field_name] = content
        elif field_name == "verdict":
            # Extract verdict enum, rest is summary
            verdict_lines = content.strip().split("\n")
            if verdict_lines:
                first_line = verdict_lines[0].strip()
                # Strip markdown formatting (bold, italic, backticks) and leading bullets
                cleaned = re.sub(r"[*_`#]+", "", first_line).strip()
                cleaned = re.sub(r"^[-•]\s*", "", cleaned).strip()
                verdict_match = re.match(r"^(PASS|NEEDS_WORK|FAIL)\b", cleaned)
                if verdict_match:
                    result["verdict"] = verdict_match.group(1)
                    rest_of_first = cleaned[verdict_match.end():].strip()
                    rest_of_first = re.sub(r"^[:\-—–]+\s*", "", rest_of_first)
                    # Only take lines before any duplicate/stuttered summary
                    unique_lines = _deduplicate_summary(verdict_lines[1:])
                    subsequent = "\n".join(unique_lines).strip()
                    summary_parts = [p for p in [rest_of_first, subsequent] if p]
                    result["verdict_summary"] = "\n".join(summary_parts).strip()
                else:
                    # No recognized verdict — put the whole content in summary
                    result["verdict"] = "NEEDS_WORK"
                    result["verdict_summary"] = content.strip()

    return result


def _get_auth_token(taskit_url: str) -> str:
    """Obtain a Bearer token from TaskIt using credentials in env/config."""
    import os
    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if not email or not password:
        return ""
    try:
        from odin.backends.taskit import TaskItAuth
        login_url = f"{taskit_url.rstrip('/')}/auth/login/"
        auth = TaskItAuth(login_url, email, password)
        return auth.get_token() or ""
    except Exception:
        logger.warning("Failed to obtain auth token for reflection", exc_info=True)
        return ""


def reflect_task(
    task_id: str,
    report_id: str,
    model: str,
    agent: str,
    taskit_url: str,
    auth_token: str = "",
    timeout: int = 300,
    log_dir: str | None = None,
):
    """Execute a reflection audit on a completed task.

    Flow:
    1. Update report status → RUNNING
    2. Gather task context from TaskIt API
    3. Build reflection prompt
    4. Execute reviewer agent via harness
    5. Parse and submit results

    Args:
        task_id: TaskIt task ID.
        report_id: ReflectionReport ID.
        model: Reviewer model name (e.g. "claude-opus-4-6").
        agent: Reviewer agent name (e.g. "claude").
        taskit_url: TaskIt backend base URL.
        auth_token: Optional Bearer token (auto-obtained from env if empty).
        timeout: Max seconds for harness execution.
        log_dir: Directory for structured JSONL logs (OdinLogger). None to skip.
    """
    # Structured logger (optional — mirrors orchestrator pattern)
    structured_log = None
    if log_dir:
        from odin.logging import OdinLogger
        structured_log = OdinLogger(log_dir)

    def _slog(action: str, **kwargs):
        if structured_log:
            structured_log.log(action=action, task_id=task_id, agent=agent, **kwargs)

    _slog("reflection_started", metadata={"report_id": report_id, "model": model})

    if not auth_token:
        auth_token = _get_auth_token(taskit_url)

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    def _patch_report(payload: dict):
        resp = httpx.patch(
            f"{taskit_url}/reflections/{report_id}/",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    start_time = time.time()

    try:
        # 1. Gather context
        detail_resp = httpx.get(
            f"{taskit_url}/tasks/{task_id}/detail/",
            headers=headers,
        )
        detail_resp.raise_for_status()
        task_data = detail_resp.json()

        metadata = task_data.get("metadata") or {}
        usage = metadata.get("last_usage", {})
        tokens_str = f"{usage.get('total_tokens', 'N/A'):,}" if usage.get("total_tokens") else "N/A"

        comments_list = task_data.get("comments") or []

        # Find the latest reflection/summary checkpoint to mark the boundary
        # between previous attempts and the current one. Comments before the
        # checkpoint are "history"; comments after are the current attempt.
        checkpoint_idx = None
        for i in range(len(comments_list) - 1, -1, -1):
            if comments_list[i].get("comment_type") in ("reflection", "summary"):
                checkpoint_idx = i
                break

        # Filter and annotate comments — keep all but insert a visible separator
        # so the reviewer can distinguish previous attempts from the current one.
        filtered_comments = []
        separator_inserted = False
        for i, c in enumerate(comments_list):
            ctype = c.get("comment_type", "status")

            # Insert separator after the checkpoint comment
            if checkpoint_idx is not None and i == checkpoint_idx + 1 and not separator_inserted:
                filtered_comments.append(
                    "\n--- CURRENT ATTEMPT (evaluate this) ---\n"
                )
                separator_inserted = True

            content = c.get("content", "")[:2000]
            # Skip status_update comments that echo the task description
            if ctype == "status_update" and content.startswith("Effective input"):
                continue
            # Skip status_update comments that are hook responses (raw JSON system events)
            if ctype == "status_update" and '{"type":"system"' in content:
                continue
            # Strip noise lines: raw JSON, CLI warnings, YOLO messages, etc.
            clean_lines = []
            for ln in content.splitlines():
                stripped = ln.strip()
                # Skip raw JSON stream lines
                if stripped.startswith("{") and stripped.endswith("}"):
                    continue
                # Skip CLI noise patterns
                if any(noise in stripped for noise in [
                    "DeprecationWarning:", "YOLO mode", "Loaded cached credentials",
                    "Loading extension:", "supports tool updates", "--trace-deprecation",
                    "(node:", "Server '",
                ]):
                    continue
                clean_lines.append(ln)
            content = "\n".join(clean_lines).strip()
            if content:
                filtered_comments.append(f"- [{ctype}] {content}")

        # If checkpoint exists but no comments came after it, add separator at end
        if checkpoint_idx is not None and not separator_inserted:
            filtered_comments.append(
                "\n--- CURRENT ATTEMPT (evaluate this) ---\n"
            )

        comments_text = "\n".join(filtered_comments)

        deps_list = task_data.get("depends_on") or []
        deps_text = "\n".join(
            f"- {dep}" for dep in deps_list
        ) or "No dependencies."

        # Clean raw JSONL execution output into human-readable text
        raw_execution = metadata.get("full_output", "")
        if raw_execution:
            execution_output = extract_text_from_stream(raw_execution)
            if not execution_output.strip():
                execution_output = raw_execution[:5000]
        else:
            execution_output = "No execution output available."

        # Metadata summary for the [CTX:metadata] section
        meta_parts = []
        if metadata.get("selected_model"):
            meta_parts.append(f"Model: {metadata['selected_model']}")
        if metadata.get("last_duration_ms"):
            meta_parts.append(f"Duration: {metadata['last_duration_ms']}ms")
        if usage.get("total_tokens"):
            meta_parts.append(f"Tokens: {usage['total_tokens']:,}")
        if metadata.get("working_dir"):
            meta_parts.append(f"Working dir: {metadata['working_dir']}")
        metadata_summary = "\n".join(meta_parts) if meta_parts else "No metadata."

        task_context = {
            "title": task_data.get("title", "Unknown"),
            "status": task_data.get("status", "Unknown"),
            "agent": (task_data.get("assignee") or {}).get("name", agent),
            "model": task_data.get("model_name") or metadata.get("selected_model", model),
            "duration_ms": metadata.get("last_duration_ms", "N/A"),
            "tokens": tokens_str,
            "description": task_data.get("description", ""),
            "execution_output": execution_output,
            "comments": comments_text or "No comments.",
            "dependencies": deps_text,
            "metadata_summary": metadata_summary,
        }

        # Log context sizes for verification
        context_sizes = {
            "description_len": len(task_context["description"]),
            "comments_count": len(comments_list),
            "execution_output_len": len(execution_output),
            "deps_count": len(deps_list),
            "metadata_summary_len": len(metadata_summary),
        }
        _slog("reflection_context_gathered", metadata=context_sizes)

        # 2. Build prompt
        custom_prompt = ""
        prompt = build_reflection_prompt(task_context, custom_prompt=custom_prompt)

        _slog("reflection_prompt_built", metadata={"prompt_length": len(prompt)})

        # 3. Mark as RUNNING and store the assembled prompt for transparency
        _patch_report({"status": "RUNNING", "assembled_prompt": prompt})

        # 4. Resolve working directory and build execution context
        working_dir = metadata.get("working_dir")

        # Create trace/output files for harness capture (mirrors orchestrator pattern)
        if log_dir:
            trace_dir = Path(log_dir)
            trace_dir.mkdir(parents=True, exist_ok=True)
        else:
            trace_dir = Path(tempfile.mkdtemp(prefix="odin_reflect_"))
        trace_file = str(trace_dir / f"reflect_{report_id}.trace.jsonl")
        output_file = str(trace_dir / f"reflect_{report_id}.out")

        context = {
            "working_dir": working_dir,
            "model": model,
            "trace_file": trace_file,
            "output_file": output_file,
        }

        # 5. Execute reviewer via harness
        from odin.config import load_config
        from odin.models import AgentConfig

        cfg = None
        try:
            cfg = load_config()
        except Exception:
            pass

        agent_cfg = cfg.agents[agent] if cfg and agent in cfg.agents else AgentConfig(default_model=model)
        harness = get_harness(agent, agent_cfg)

        _slog("reflection_harness_started", metadata={"model": model, "working_dir": working_dir})

        harness_start = time.time()
        result = asyncio.run(harness.execute(prompt, context))
        duration_ms = int((time.time() - harness_start) * 1000)

        # 6. Cost tracking (local audit trail only — cost is computed dynamically by TaskIt)
        token_usage = result.metadata.get("usage", {})
        try:
            from odin.cost_tracking import CostStore, CostTracker
            pricing = _load_pricing_table()
            cost_store = CostStore(cfg.cost_storage if cfg else ".odin/costs")
            tracker = CostTracker(cost_store, pricing=pricing)
            tracker.record_task(
                task_id=f"reflect_{report_id}",
                spec_id=None,
                result=result,
                model=model,
            )
        except Exception:
            logger.debug("Cost tracking failed for reflection %s", report_id, exc_info=True)

        # Read raw JSONL trace for debugging visibility
        raw_jsonl = ""
        if Path(trace_file).exists():
            raw_jsonl = Path(trace_file).read_text()
        elif Path(output_file).exists():
            raw_jsonl = Path(output_file).read_text()

        # 7. Parse and submit
        if not result.success:
            _patch_report({
                "status": "FAILED",
                "error_message": result.error or "Harness execution failed",
                "raw_output": result.output[:10000],
                "execution_trace": _truncate_trace(raw_jsonl, 50000),
                "duration_ms": duration_ms,
                "token_usage": token_usage,
            })
            _slog("reflection_failed", duration_ms=duration_ms, metadata={
                "error": result.error or "Harness execution failed",
                "token_usage": token_usage,
            })
            return

        clean_output = _strip_odin_envelopes(result.output)
        parsed = parse_reflection_report(clean_output)

        _patch_report({
            "status": "COMPLETED",
            "quality_assessment": parsed["quality_assessment"],
            "slop_detection": parsed["slop_detection"],
            "improvements": parsed["improvements"],
            "agent_optimization": parsed["agent_optimization"],
            "quota_failure": parsed["quota_failure"],
            "verdict": parsed["verdict"],
            "verdict_summary": parsed["verdict_summary"],
            "raw_output": result.output[:10000],
            "execution_trace": _truncate_trace(raw_jsonl, 50000),
            "duration_ms": duration_ms,
            "token_usage": token_usage,
        })

        _slog("reflection_completed", duration_ms=duration_ms, metadata={
            "verdict": parsed["verdict"],
            "token_usage": token_usage,
        })

        logger.info(
            "Reflection completed: task=%s, report=%s, verdict=%s, duration=%sms",
            task_id, report_id, parsed["verdict"], duration_ms,
        )

    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Reflection failed: task=%s, report=%s", task_id, report_id, exc_info=True)
        _slog("reflection_failed", duration_ms=duration_ms, metadata={
            "error": str(exc),
        })
        try:
            _patch_report({
                "status": "FAILED",
                "error_message": str(exc),
                "duration_ms": duration_ms,
            })
        except Exception:
            logger.error("Failed to report reflection failure", exc_info=True)


def _load_pricing_table():
    """Load pricing table — reuses Orchestrator's static method logic."""
    from pathlib import Path
    from odin.cost_tracking.estimator import load_pricing_table
    candidates = [
        Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return load_pricing_table(str(path))
            except Exception:
                return None
    return None
