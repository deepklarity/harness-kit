"""Live integration tests for screenshot upload.

Requires a running TaskIt backend at TASKIT_URL (default: http://localhost:8000).
Skipped automatically if the backend is not reachable.
"""

import os
import tempfile

import httpx
import pytest

from odin.tools.core import TaskItToolClient

_TASKIT_URL = os.environ.get("TASKIT_URL", "http://localhost:8000")


def _health_ok() -> bool:
    """Check if TaskIt backend is reachable."""
    try:
        resp = httpx.get(f"{_TASKIT_URL}/health/", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _get_auth_token() -> str:
    """Get auth token from env vars (same as MCP server)."""
    token = os.environ.get("TASKIT_AUTH_TOKEN", "")
    if token:
        return token
    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if email and password:
        from odin.backends.taskit import TaskItAuth
        auth = TaskItAuth(f"{_TASKIT_URL}/auth/login/", email, password)
        return auth.get_token()
    return ""


pytestmark = pytest.mark.skipif(
    not _health_ok(),
    reason=f"TaskIt backend not reachable at {_TASKIT_URL}",
)


@pytest.fixture(scope="module")
def auth_token():
    return _get_auth_token()


@pytest.fixture(scope="module")
def board(auth_token):
    """Create a test board, yield it, then delete."""
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    resp = httpx.post(
        f"{_TASKIT_URL}/boards/",
        json={"name": "Screenshot Test Board"},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    board_data = resp.json()
    yield board_data
    # Cleanup
    httpx.delete(f"{_TASKIT_URL}/boards/{board_data['id']}/", headers=headers, timeout=10)


@pytest.fixture
def task(board, auth_token):
    """Create a test task, yield it, then delete."""
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    resp = httpx.post(
        f"{_TASKIT_URL}/tasks/",
        json={
            "board_id": board["id"],
            "title": "Screenshot upload test",
            "created_by": "test@odin.agent",
        },
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    task_data = resp.json()
    yield task_data
    httpx.delete(f"{_TASKIT_URL}/tasks/{task_data['id']}/", headers=headers, timeout=10)


@pytest.fixture
def client(task, auth_token):
    return TaskItToolClient(
        base_url=_TASKIT_URL,
        task_id=str(task["id"]),
        auth_token=auth_token,
        author_email="test+integration@odin.agent",
    )


@pytest.fixture
def png_file():
    """Create a minimal 1x1 pixel PNG in a temp file."""
    # Minimal valid 1x1 PNG
    png_data = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_data)
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestScreenshotUploadLive:
    """Live tests for screenshot upload via TaskItToolClient."""

    def test_upload_screenshot_and_verify_url(self, client, png_file, auth_token):
        """Upload a real PNG, then GET the returned URL → 200."""
        result = client.upload_screenshots([png_file])
        assert len(result) == 1
        att = result[0]
        assert "url" in att
        assert att["original_filename"].endswith(".png")
        assert att["content_type"] == "image/png"

        # Verify the URL serves the file
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        get_resp = httpx.get(att["url"], headers=headers, timeout=10)
        assert get_resp.status_code == 200

    def test_proof_with_screenshots_e2e(self, client, png_file):
        """Upload screenshot → submit proof with screenshot_urls → verify in comments."""
        # Upload
        uploaded = client.upload_screenshots([png_file])
        screenshot_urls = [att["url"] for att in uploaded]

        # Submit proof referencing the URLs
        proof_result = client.submit_proof(
            summary="Task completed successfully",
            screenshot_urls=screenshot_urls,
        )
        assert "id" in proof_result

        # Verify via context
        context = client.get_context()
        comments = context["comments"]
        proof_comments = [c for c in comments if c.get("comment_type") == "proof"]
        assert len(proof_comments) >= 1
        latest_proof = proof_comments[-1]

        # Check that attachments contain screenshots
        attachments = latest_proof.get("attachments", [])
        assert any(
            isinstance(a, dict) and a.get("screenshots")
            for a in attachments
        ), f"No screenshots in proof attachments: {attachments}"

        # Check file_attachments are present (not linked to comment yet in this flow,
        # but they exist on the task)

    def test_add_comment_proof_with_screenshots_via_mcp(self, task, auth_token, png_file):
        """Full MCP tool flow: taskit_add_comment with screenshot_paths."""
        import os
        from unittest.mock import patch

        from odin.mcps.taskit_mcp.server import taskit_add_comment
        from odin.mcps.taskit_mcp import server as server_mod

        # Set up env vars for the MCP server
        env = {
            "TASKIT_URL": _TASKIT_URL,
            "TASKIT_TASK_ID": str(task["id"]),
            "TASKIT_AUTH_TOKEN": auth_token,
            "TASKIT_AUTHOR_EMAIL": "test+mcp@odin.agent",
        }

        old_token = server_mod._auth_token
        server_mod._auth_token = auth_token

        try:
            with patch.dict(os.environ, env):
                result = taskit_add_comment(
                    content="MCP proof with screenshot",
                    task_id=str(task["id"]),
                    comment_type="proof",
                    screenshot_paths=[png_file],
                )
        finally:
            server_mod._auth_token = old_token

        assert "error" not in result, f"MCP call returned error: {result}"
        assert "comment_id" in result
