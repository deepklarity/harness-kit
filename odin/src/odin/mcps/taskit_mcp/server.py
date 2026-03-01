"""TaskIt MCP server — exposes TaskIt operations as MCP tools.

Tools:
  taskit_add_comment  — Post a status update or blocking question
  taskit_add_attachment — Attach file references or proof metadata

Run:
  taskit-mcp              # stdio transport (default)
  TASKIT_URL=... taskit-mcp  # custom backend URL
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Annotated

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

from odin.tools.core import TaskItToolClient


mcp = FastMCP(
    "taskit",
    instructions=(
        "Tools for interacting with the TaskIt task board. "
        "Use taskit_add_comment for ALL lifecycle messages: status updates, proof of work, and blocking questions. "
        "Use taskit_add_attachment only for file-only attachments (not proof)."
    ),
)


_auth_token: str | None = None


def _resolve_auth_token() -> str:
    """Resolve auth token from env vars.

    Priority:
      1. TASKIT_AUTH_TOKEN (explicit, e.g. from orchestrator MCP config)
      2. ODIN_ADMIN_USER + ODIN_ADMIN_PASSWORD (same .env odin uses)
      3. Empty string (no credentials configured — unauthenticated)

    Raises TaskItAuthError if credentials are present but auth fails.
    """
    token = os.environ.get("TASKIT_AUTH_TOKEN", "")
    if token:
        return token

    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if email and password:
        from odin.backends.taskit import TaskItAuth
        base_url = os.environ.get("TASKIT_URL", "http://localhost:8000")
        auth = TaskItAuth(f"{base_url}/auth/login/", email, password)
        return auth.get_token()  # raises TaskItAuthError on failure

    return ""


def _get_auth_token() -> str:
    """Return the cached auth token (resolved once at startup)."""
    global _auth_token
    if _auth_token is None:
        _auth_token = _resolve_auth_token()
    return _auth_token


def _make_client(task_id: str) -> TaskItToolClient:
    """Create a TaskItToolClient from environment variables.

    Reads TASKIT_* env vars for direct config, falls back to ODIN_* env vars
    for authentication (same .env file odin uses).
    """
    return TaskItToolClient(
        base_url=os.environ.get("TASKIT_URL", "http://localhost:8000"),
        task_id=task_id,
        auth_token=_get_auth_token(),
        author_email=os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent"),
        author_label=os.environ.get("TASKIT_AUTHOR_LABEL", ""),
    )


class CommentType(str, Enum):
    status_update = "status_update"
    proof = "proof"
    question = "question"


class AttachmentType(str, Enum):
    file = "file"
    proof = "proof"


def _coerce_list(val: str | list | None) -> list[str] | None:
    """Coerce a value to list[str] or None.

    Some agents (notably Qwen) send JSON-encoded strings like ``'["/tmp/a.png"]'``
    instead of actual arrays.  This normalises both forms.
    """
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        import json
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        # Single bare path
        return [val]
    if isinstance(val, list):
        return [str(x) for x in val] if val else None
    return None


def _extract_screenshot_paths(content: str) -> list[str] | None:
    """Extract screenshot file paths mentioned in proof text.

    Agents sometimes write screenshot paths in the content instead of passing
    them as the ``screenshot_paths`` parameter.  This looks for common patterns
    like ``/tmp/proof_370.png`` and returns existing files.
    """
    import re
    from pathlib import Path

    matches = re.findall(r"(/(?:tmp|var|Users)\S+\.(?:png|jpg|jpeg|gif|webp))", content)
    found = [m for m in matches if Path(m).is_file()]
    if matches and not found:
        logger.warning(
            "Screenshot paths referenced in proof but not found on disk: %s",
            matches,
        )
    return found or None


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
)
def taskit_add_comment(
    content: Annotated[str, "The comment text or question to ask"],
    task_id: Annotated[str | None, "Task ID (defaults to TASKIT_TASK_ID env var)"] = None,
    comment_type: Annotated[
        CommentType, "Type: 'status_update' (default), 'proof', or 'question'"
    ] = CommentType.status_update,
    file_paths: Annotated[
        list[str] | str | None,
        "File paths for proof evidence (only used with comment_type='proof')",
    ] = None,
    screenshot_paths: Annotated[
        list[str] | str | None,
        "Screenshot image paths to upload as visual proof evidence (only used with comment_type='proof')",
    ] = None,
    metadata: Annotated[
        dict | None, "Optional metadata to attach to the comment"
    ] = None,
) -> dict:
    """Add a lifecycle message to a task.

    Expected lifecycle (2-4 messages per task):
      1. Start:     comment_type="status_update" — what you're about to do
      2. Milestone:  comment_type="status_update" — significant progress
      3. Completed: comment_type="status_update" — what you accomplished
      4. Proof:     comment_type="proof" — verification evidence (with optional file_paths and screenshot_paths)
      5. Blocked:   comment_type="question" — pauses until human replies

    task_id defaults to the TASKIT_TASK_ID environment variable, which is
    set automatically by Odin's MCP config generation.

    comment_type="status_update": posts a comment and returns immediately.
    comment_type="proof": posts proof of work (use file_paths for file references,
      screenshot_paths for images to upload and display inline).
    comment_type="question": posts a question and BLOCKS until a human replies
    on the TaskIt board. The agent's context is frozen during the wait — no
    tokens consumed while polling.
    """
    # Normalise list params — some agents send JSON-encoded strings instead of arrays
    file_paths = _coerce_list(file_paths)
    screenshot_paths = _coerce_list(screenshot_paths)

    resolved_id = task_id or os.environ.get("TASKIT_TASK_ID", "")
    if not resolved_id:
        return {"error": "No task_id provided and TASKIT_TASK_ID not set"}
    client = _make_client(resolved_id)

    ct_value = comment_type.value if isinstance(comment_type, CommentType) else str(comment_type)

    if ct_value == CommentType.question.value:
        result = client.ask_question(content, wait=True, timeout=0)
        return {"comment_id": result["id"], "reply": result.get("reply")}
    elif ct_value == CommentType.proof.value:
        # Upload screenshots if provided, collect their URLs and IDs.
        # Fallback: if agent didn't pass screenshot_paths but mentioned a
        # /tmp/proof*.png path in the content text, extract and upload it.
        if not screenshot_paths:
            screenshot_paths = _extract_screenshot_paths(content)
        screenshot_urls = None
        attachment_ids = None
        upload_warning = None
        if screenshot_paths:
            try:
                uploaded = client.upload_screenshots(screenshot_paths)
                screenshot_urls = [att["url"] for att in uploaded]
                attachment_ids = [att["id"] for att in uploaded]
            except (FileNotFoundError, ValueError, Exception) as exc:
                logger.warning("Screenshot upload failed for task %s: %s", resolved_id, exc)
                # Non-fatal: proceed with text-only proof
                screenshot_urls = None
                attachment_ids = None
                upload_warning = f"Screenshot upload failed: {exc}"
        result = client.submit_proof(
            summary=content, files=file_paths,
            screenshot_urls=screenshot_urls, attachment_ids=attachment_ids,
        )
        ret = {"comment_id": result["id"], "screenshots_attached": len(attachment_ids or [])}
        if upload_warning:
            ret["screenshot_warning"] = upload_warning
        return ret
    else:
        result = client.post_comment(content, comment_type=ct_value)
        return {"comment_id": result["id"]}


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
)
def taskit_add_attachment(
    content: Annotated[str, "Description or summary of the attachment"],
    task_id: Annotated[str | None, "Task ID (defaults to TASKIT_TASK_ID env var)"] = None,
    file_paths: Annotated[
        list[str] | str | None,
        "File paths or URLs to reference (not uploaded, stored as metadata)",
    ] = None,
    attachment_type: Annotated[
        AttachmentType, "Type: 'file' (default) or 'proof'"
    ] = AttachmentType.file,
) -> dict:
    """Attach file path/URL references to a task.

    task_id defaults to the TASKIT_TASK_ID environment variable, which is
    set automatically by Odin's MCP config generation.

    Does NOT upload files — stores paths/URLs as metadata on a comment.
    For proof of work, prefer taskit_add_comment with comment_type="proof"
    instead — it creates a first-class proof comment with distinct UI treatment.
    """
    file_paths = _coerce_list(file_paths)
    resolved_id = task_id or os.environ.get("TASKIT_TASK_ID", "")
    if not resolved_id:
        return {"error": "No task_id provided and TASKIT_TASK_ID not set"}
    client = _make_client(resolved_id)

    if attachment_type == AttachmentType.proof:
        result = client.submit_proof(summary=content, files=file_paths)
    else:
        result = client.post_comment(content)
    return {"comment_id": result["id"]}


def _configure_logging():
    """Route all MCP server logs to a file, keep stderr quiet.

    MCP hosts (Gemini CLI, Claude Code, etc.) treat any stderr output as
    errors — so FastMCP's default INFO logging causes misleading "MCP ERROR"
    warnings even when tool calls succeed.

    Fix: redirect all logs to .odin/logs/mcp_server.log (DEBUG level for full
    traceability), and raise stderr threshold to WARNING (real problems only).
    """
    import logging
    from pathlib import Path

    log_dir = Path.cwd() / ".odin" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mcp_server.log"

    # File handler — full debug trace for post-hoc inspection.
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Reconfigure FastMCP's logger: file gets everything, stderr gets WARNING+.
    fastmcp_logger = logging.getLogger("fastmcp")
    fastmcp_logger.setLevel(logging.DEBUG)
    fastmcp_logger.addHandler(file_handler)

    # Clamp existing stderr handlers (installed by FastMCP at import time)
    # to WARNING so only real problems show up in the MCP host's output.
    for handler in fastmcp_logger.handlers:
        if handler is not file_handler:
            handler.setLevel(logging.WARNING)


def main():
    """Entry point for taskit-mcp CLI.

    Loads .env from cwd (same as odin's load_config) so ODIN_ADMIN_USER,
    ODIN_ADMIN_PASSWORD, and TASKIT_URL are available for authentication.

    Installs SIGTERM/SIGINT handlers so the process exits cleanly when the
    parent (claude -p) is killed or when stdin closes — rather than lingering
    as an orphan MCP child process.
    """
    import signal
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    _configure_logging()

    def _handle_shutdown(signum, frame):
        logger.info("Received signal %s — shutting down cleanly", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    try:
        mcp.run(transport="stdio")
    except (EOFError, BrokenPipeError):
        # Parent closed stdin — clean exit
        logger.info("stdin closed — exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
