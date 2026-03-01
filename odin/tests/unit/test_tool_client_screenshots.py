"""Tests for TaskItToolClient screenshot upload and proof-with-screenshots."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from odin.tools.core import TaskItToolClient


@pytest.fixture
def client():
    return TaskItToolClient(
        base_url="http://localhost:8000",
        task_id="42",
        auth_token="test-token",
        author_email="claude+sonnet@odin.agent",
    )


@pytest.fixture
def mock_response():
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = 201
    resp.raise_for_status = MagicMock()
    return resp


class TestUploadScreenshots:
    """Tests for TaskItToolClient.upload_screenshots()."""

    @patch("odin.tools.core.httpx.post")
    def test_upload_sends_multipart_post(self, mock_post, client, mock_response, tmp_path):
        """upload_screenshots sends a multipart POST with files."""
        mock_response.json.return_value = [
            {"id": 1, "url": "http://localhost:8000/media/screenshots/2026/02/shot.png",
             "original_filename": "shot.png", "content_type": "image/png"}
        ]
        mock_post.return_value = mock_response

        # Create a temp file
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\ntest")

        result = client.upload_screenshots([str(img)])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["files"] is not None or call_kwargs[1].get("files") is not None
        # URL should target screenshots endpoint
        url_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["url"]
        assert url_arg == "http://localhost:8000/tasks/42/screenshots/"

    @patch("odin.tools.core.httpx.post")
    def test_upload_returns_attachment_metadata(self, mock_post, client, mock_response, tmp_path):
        """upload_screenshots returns list of dicts with id, url."""
        expected = [
            {"id": 1, "url": "http://localhost:8000/media/screenshots/2026/02/a.png",
             "original_filename": "a.png", "content_type": "image/png"},
            {"id": 2, "url": "http://localhost:8000/media/screenshots/2026/02/b.png",
             "original_filename": "b.png", "content_type": "image/png"},
        ]
        mock_response.json.return_value = expected
        mock_post.return_value = mock_response

        imgs = []
        for name in ["a.png", "b.png"]:
            img = tmp_path / name
            img.write_bytes(b"\x89PNG\r\n\x1a\n")
            imgs.append(str(img))

        result = client.upload_screenshots(imgs)
        assert result == expected
        assert len(result) == 2

    def test_upload_nonexistent_file_raises(self, client):
        """upload_screenshots raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError, match="Screenshot file not found"):
            client.upload_screenshots(["/nonexistent/path/img.png"])

    def test_upload_empty_list_raises(self, client):
        """upload_screenshots raises ValueError for empty list."""
        with pytest.raises(ValueError, match="must not be empty"):
            client.upload_screenshots([])

    @patch("odin.tools.core.httpx.post")
    def test_upload_uses_auth_headers_without_content_type(self, mock_post, client, mock_response, tmp_path):
        """upload_screenshots sets auth header but not Content-Type (httpx sets it for multipart)."""
        mock_response.json.return_value = [{"id": 1}]
        mock_post.return_value = mock_response

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")

        client.upload_screenshots([str(img)])

        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-token"
        assert "Content-Type" not in headers


class TestSubmitProofWithScreenshots:
    """Tests for submit_proof with screenshot_urls parameter."""

    @patch("odin.tools.core.httpx.post")
    def test_submit_proof_with_screenshot_urls(self, mock_post, client, mock_response):
        """screenshot_urls appear in the proof attachment dict."""
        mock_response.json.return_value = {"id": 10}
        mock_post.return_value = mock_response

        urls = ["http://localhost/media/screenshots/2026/02/a.png"]
        client.submit_proof(summary="Task complete", screenshot_urls=urls)

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        attachments = payload["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["screenshots"] == urls
        assert attachments[0]["type"] == "proof"

    @patch("odin.tools.core.httpx.post")
    def test_submit_proof_without_screenshots_unchanged(self, mock_post, client, mock_response):
        """Without screenshot_urls, proof attachment has no 'screenshots' key."""
        mock_response.json.return_value = {"id": 10}
        mock_post.return_value = mock_response

        client.submit_proof(summary="Done")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        attachments = payload["attachments"]
        assert "screenshots" not in attachments[0]
