"""Integration tests for TaskIt MCP server against a live TaskIt backend.

Requires:
  - TaskIt backend running at TASKIT_URL (default: http://localhost:8000)
  - If auth enabled: ODIN_ADMIN_USER, ODIN_ADMIN_PASSWORD env vars
    (loaded from odin/temp_test_dir/.env via dotenv, or set explicitly)

Run from odin/:
  python -m pytest tests/integration/test_taskit_mcp_live.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

# Load .env from temp_test_dir (where odin is normally run from)
_env_file = Path(__file__).resolve().parents[2] / "temp_test_dir" / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

from odin.mcps.taskit_mcp.server import (
    AttachmentType,
    taskit_add_comment,
    taskit_add_attachment,
)

TASKIT_URL = os.environ.get("TASKIT_URL", "http://localhost:8000")


def _get_auth() -> httpx.Auth | None:
    """Get TaskItAuth if credentials are available, else None."""
    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if not email or not password:
        return None
    from odin.backends.taskit import TaskItAuth
    return TaskItAuth(
        login_url=f"{TASKIT_URL}/auth/login/",
        email=email,
        password=password,
    )


def _get_token(auth: httpx.Auth | None) -> str:
    """Extract Bearer token from TaskItAuth, or return empty."""
    if auth is None:
        return ""
    return auth.get_token()


def _api(path: str, auth: httpx.Auth | None = None, **kwargs) -> httpx.Response:
    """Direct HTTP call to TaskIt API with optional auth."""
    return httpx.request(url=f"{TASKIT_URL}{path}", auth=auth, timeout=10, **kwargs)


def _health_ok() -> bool:
    try:
        resp = httpx.get(f"{TASKIT_URL}/health/", timeout=5)
        return resp.status_code == 200
    except httpx.ConnectError:
        return False


pytestmark = pytest.mark.skipif(
    not _health_ok(),
    reason=f"TaskIt backend not reachable at {TASKIT_URL}",
)


@pytest.fixture(scope="module")
def auth():
    """Get auth handler for the test session."""
    return _get_auth()


@pytest.fixture(scope="module")
def board(auth):
    """Create a test board for the integration test session."""
    resp = _api("/boards/", auth=auth, method="POST", json={"name": "MCP Integration Test"})
    assert resp.status_code == 201, f"Board creation failed: {resp.status_code} {resp.text}"
    board_data = resp.json()
    yield board_data
    # Cleanup
    _api(f"/boards/{board_data['id']}/clear/", auth=auth, method="POST")
    _api(f"/boards/{board_data['id']}/", auth=auth, method="DELETE")


@pytest.fixture
def task(board, auth):
    """Create a fresh task for each test."""
    resp = _api(
        "/tasks/",
        auth=auth,
        method="POST",
        json={
            "board_id": board["id"],
            "title": "MCP integration test task",
            "created_by": "test@integration.test",
        },
    )
    assert resp.status_code == 201, f"Task creation failed: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.fixture(autouse=True)
def set_env(task, auth):
    """Set env vars for MCP tools to find the backend."""
    env_keys = ("TASKIT_URL", "TASKIT_AUTH_TOKEN", "TASKIT_AUTHOR_EMAIL")
    old = {k: os.environ.get(k) for k in env_keys}
    os.environ["TASKIT_URL"] = TASKIT_URL
    os.environ["TASKIT_AUTH_TOKEN"] = _get_token(auth)
    os.environ["TASKIT_AUTHOR_EMAIL"] = "mcp-test@integration.test"
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestCommentViaMCP:
    """Post comments via MCP tool and verify via TaskIt API."""

    def test_post_status_update(self, task, auth):
        result = taskit_add_comment(
            task_id=str(task["id"]),
            content="Integration test status update",
        )
        assert "comment_id" in result

        # Verify via API
        resp = _api(f"/tasks/{task['id']}/comments/", auth=auth, method="GET")
        comments = resp.json()["results"]
        assert any(c["content"] == "Integration test status update" for c in comments)

    def test_post_question_creates_question_comment(self, task, auth):
        """Post a question — don't wait for reply (it would block forever)."""
        from odin.tools.core import TaskItToolClient

        client = TaskItToolClient(
            base_url=TASKIT_URL,
            task_id=str(task["id"]),
            auth_token=_get_token(auth),
            author_email="mcp-test@integration.test",
        )
        result = client.ask_question("Integration test question?", wait=False)
        assert result["id"]
        assert result["attachments"][0]["type"] == "question"
        assert result["attachments"][0]["status"] == "pending"

    def test_question_reply_roundtrip(self, task, auth):
        """Post question, simulate human reply, verify reply is found by polling."""
        from odin.tools.core import TaskItToolClient

        token = _get_token(auth)
        client = TaskItToolClient(
            base_url=TASKIT_URL,
            task_id=str(task["id"]),
            auth_token=token,
            author_email="mcp-test@integration.test",
        )

        # Post question (no wait)
        question = client.ask_question("What color?", wait=False)
        question_id = question["id"]

        # Simulate human reply via API
        _api(
            f"/tasks/{task['id']}/comments/{question_id}/reply/",
            auth=auth,
            method="POST",
            json={
                "author_email": "human@example.com",
                "content": "Blue!",
            },
        )

        # Now poll — should find the reply quickly
        reply = client._poll_for_reply(question_id, timeout=15)
        assert reply == "Blue!"


class TestAttachmentViaMCP:
    """Post attachments via MCP tool and verify via TaskIt API."""

    def test_post_proof(self, task, auth):
        result = taskit_add_attachment(
            task_id=str(task["id"]),
            content="All tests pass",
            file_paths=["tests/output.log", "coverage.html"],
            attachment_type=AttachmentType.proof,
        )
        assert "comment_id" in result

        # Verify via API
        resp = _api(f"/tasks/{task['id']}/comments/", auth=auth, method="GET")
        comments = resp.json()["results"]
        proof_comments = [c for c in comments if "Proof:" in c["content"]]
        assert len(proof_comments) == 1

    def test_post_file_attachment(self, task):
        result = taskit_add_attachment(
            task_id=str(task["id"]),
            content="Screenshot of the login page",
        )
        assert "comment_id" in result
