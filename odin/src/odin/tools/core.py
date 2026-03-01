"""Core API client for taskit-tool operations.

Provides TaskItToolClient — shared between CLI and MCP server.
Configuration can come from explicit parameters, CLI flags, or env vars.
"""

from __future__ import annotations

import os
import time

import httpx


def client_from_args(
    task_id: str | None = None,
    url: str | None = None,
    auth_token: str | None = None,
    author_email: str | None = None,
    author_label: str | None = None,
) -> "TaskItToolClient":
    """Create a TaskItToolClient from explicit args with env var fallbacks.

    Priority: explicit args > environment variables > defaults.
    """
    resolved_url = url or os.environ.get("TASKIT_URL", "http://localhost:8000")
    resolved_task_id = task_id or os.environ.get("TASKIT_TASK_ID", "")
    resolved_token = auth_token or os.environ.get("TASKIT_AUTH_TOKEN", "")
    resolved_email = author_email or os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent")
    resolved_label = author_label or os.environ.get("TASKIT_AUTHOR_LABEL", "")
    if not resolved_task_id:
        raise ValueError(
            "Task ID is required. Pass --task-id or set TASKIT_TASK_ID env var."
        )
    return TaskItToolClient(
        base_url=resolved_url,
        task_id=resolved_task_id,
        auth_token=resolved_token,
        author_email=resolved_email,
        author_label=resolved_label,
    )


# Backward compat alias
client_from_env = client_from_args


class TaskItToolClient:
    """API client for taskit-tool operations."""

    def __init__(
        self,
        base_url: str,
        task_id: str,
        auth_token: str = "",
        author_email: str = "agent@odin.agent",
        author_label: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.task_id = task_id
        self.auth_token = auth_token
        self.author_email = author_email
        self.author_label = author_label

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _auth_headers(self) -> dict:
        """Auth-only headers (no Content-Type) for multipart requests."""
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _task_url(self, path: str = "") -> str:
        return f"{self.base_url}/tasks/{self.task_id}/{path}"

    def post_comment(self, content: str, comment_type: str = "status_update") -> dict:
        """Post a comment to the task with an explicit type."""
        resp = httpx.post(
            self._task_url("comments/"),
            json={
                "author_email": self.author_email,
                "author_label": self.author_label,
                "content": content,
                "comment_type": comment_type,
            },
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def ask_question(
        self, content: str, wait: bool = False, timeout: int = 300
    ) -> dict:
        """Ask a question. If wait=True, poll for a reply."""
        resp = httpx.post(
            self._task_url("question/"),
            json={
                "author_email": self.author_email,
                "author_label": self.author_label,
                "content": content,
            },
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        if not wait:
            return result

        comment_id = result["id"]
        reply = self._poll_for_reply(comment_id, timeout)
        result["reply"] = reply
        return result

    def submit_proof(
        self,
        summary: str,
        steps: list[str] | None = None,
        files: list[str] | None = None,
        handover: str | None = None,
        screenshot_urls: list[str] | None = None,
        attachment_ids: list[int] | None = None,
    ) -> dict:
        """Submit structured proof of work.

        When *attachment_ids* are provided (from a prior ``upload_screenshots``
        call), the backend links those ``CommentAttachment`` records to the
        newly created proof comment so the frontend can render them inline.
        """
        proof_attachment: dict = {"type": "proof", "summary": summary}
        if steps:
            proof_attachment["steps"] = steps
        if files:
            proof_attachment["files"] = files
        if handover:
            proof_attachment["handover"] = handover
        if screenshot_urls:
            proof_attachment["screenshots"] = screenshot_urls

        payload: dict = {
            "author_email": self.author_email,
            "author_label": self.author_label,
            "content": f"Proof: {summary}",
            "comment_type": "proof",
            "attachments": [proof_attachment],
        }
        if attachment_ids:
            payload["attachment_ids"] = attachment_ids

        resp = httpx.post(
            self._task_url("comments/"),
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def upload_screenshots(self, file_paths: list[str]) -> list[dict]:
        """Upload screenshot files to the task's screenshots endpoint.

        Returns list of attachment metadata dicts (id, url, original_filename, etc.).
        Raises FileNotFoundError if any path doesn't exist.
        Raises ValueError if file_paths is empty.
        """
        if not file_paths:
            raise ValueError("file_paths must not be empty")

        import mimetypes
        from pathlib import Path

        upload_files = []
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                raise FileNotFoundError(f"Screenshot file not found: {fp}")
            mime = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            upload_files.append(("files", (p.name, p.read_bytes(), mime)))

        resp = httpx.post(
            self._task_url("screenshots/"),
            files=upload_files,
            data={"author_email": self.author_email},
            headers=self._auth_headers(),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def get_context(self) -> dict:
        """Get task details and upstream comments."""
        resp = httpx.get(
            self._task_url("detail/"),
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "task_id": data.get("id"),
            "title": data.get("title"),
            "description": data.get("description"),
            "status": data.get("status"),
            "metadata": data.get("metadata"),
            "comments": data.get("comments", []),
        }

    def _poll_for_reply(self, comment_id: int, timeout: int) -> str | None:
        """Poll for a reply to a question comment.

        If timeout=0, poll indefinitely (no deadline).
        """
        deadline = time.time() + timeout if timeout > 0 else None
        poll_interval = 5

        while deadline is None or time.time() < deadline:
            time.sleep(poll_interval)
            resp = httpx.get(
                self._task_url(f"comments/?after={comment_id}"),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            for comment in results:
                attachments = comment.get("attachments", [])
                for att in attachments:
                    if isinstance(att, dict) and att.get("type") == "reply" and att.get("reply_to") == comment_id:
                        return comment["content"]

        return None
