"""Tests for screenshot_paths parameter in taskit_add_comment MCP tool."""

from unittest.mock import MagicMock, patch

import pytest

from odin.mcps.taskit_mcp import server as server_mod
from odin.mcps.taskit_mcp.server import taskit_add_comment


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.submit_proof.return_value = {"id": 42}
    client.post_comment.return_value = {"id": 43}
    client.ask_question.return_value = {"id": 44, "reply": "yes"}
    client.upload_screenshots.return_value = [
        {"id": 1, "url": "http://localhost/media/screenshots/2026/02/a.png"},
        {"id": 2, "url": "http://localhost/media/screenshots/2026/02/b.png"},
    ]
    return client


@pytest.fixture(autouse=True)
def patch_make_client(mock_client):
    with patch.object(server_mod, "_make_client", return_value=mock_client):
        yield


@pytest.fixture(autouse=True)
def reset_auth_cache():
    old = server_mod._auth_token
    server_mod._auth_token = ""
    yield
    server_mod._auth_token = old


class TestProofWithScreenshots:
    """Tests for screenshot_paths in proof comments."""

    def test_proof_with_screenshot_paths_uploads_then_submits(self, mock_client):
        """When screenshot_paths given, uploads first, then submits proof with URLs and attachment IDs."""
        result = taskit_add_comment(
            content="Task complete",
            task_id="99",
            comment_type="proof",
            screenshot_paths=["/tmp/a.png", "/tmp/b.png"],
        )
        # Should upload first
        mock_client.upload_screenshots.assert_called_once_with(["/tmp/a.png", "/tmp/b.png"])
        # Then submit proof with the returned URLs and attachment IDs
        mock_client.submit_proof.assert_called_once_with(
            summary="Task complete",
            files=None,
            screenshot_urls=[
                "http://localhost/media/screenshots/2026/02/a.png",
                "http://localhost/media/screenshots/2026/02/b.png",
            ],
            attachment_ids=[1, 2],
        )
        assert result == {"comment_id": 42, "screenshots_attached": 2}

    def test_proof_without_screenshot_paths_unchanged(self, mock_client):
        """Without screenshot_paths, submit_proof called without screenshot_urls."""
        result = taskit_add_comment(
            content="Done",
            task_id="99",
            comment_type="proof",
        )
        mock_client.upload_screenshots.assert_not_called()
        mock_client.submit_proof.assert_called_once_with(
            summary="Done", files=None, screenshot_urls=None,
            attachment_ids=None,
        )
        assert result == {"comment_id": 42, "screenshots_attached": 0}

    def test_proof_with_file_paths_and_screenshot_paths(self, mock_client):
        """Both file_paths and screenshot_paths can be used together."""
        result = taskit_add_comment(
            content="Done",
            task_id="99",
            comment_type="proof",
            file_paths=["/code/output.log"],
            screenshot_paths=["/tmp/shot.png"],
        )
        mock_client.upload_screenshots.assert_called_once()
        mock_client.submit_proof.assert_called_once_with(
            summary="Done",
            files=["/code/output.log"],
            screenshot_urls=["http://localhost/media/screenshots/2026/02/a.png",
                            "http://localhost/media/screenshots/2026/02/b.png"],
            attachment_ids=[1, 2],
        )

    def test_screenshot_upload_failure_degrades_to_text_proof(self, mock_client):
        """Upload failure degrades gracefully to text-only proof."""
        mock_client.upload_screenshots.side_effect = FileNotFoundError("not found: /tmp/x.png")
        result = taskit_add_comment(
            content="Done",
            task_id="99",
            comment_type="proof",
            screenshot_paths=["/tmp/x.png"],
        )
        # Should still submit proof (text-only), not return error
        mock_client.submit_proof.assert_called_once_with(
            summary="Done", files=None,
            screenshot_urls=None, attachment_ids=None,
        )
        assert result["comment_id"] == 42
        assert result["screenshots_attached"] == 0
        assert "screenshot_warning" in result
        assert "not found" in result["screenshot_warning"]

    def test_proof_with_string_encoded_screenshot_paths(self, mock_client):
        """Qwen sends list params as JSON strings — coercion handles it."""
        result = taskit_add_comment(
            content="Task complete",
            task_id="99",
            comment_type="proof",
            screenshot_paths='["/tmp/a.png", "/tmp/b.png"]',
        )
        mock_client.upload_screenshots.assert_called_once_with(["/tmp/a.png", "/tmp/b.png"])
        assert result == {"comment_id": 42, "screenshots_attached": 2}

    def test_proof_with_string_encoded_file_paths(self, mock_client):
        """Qwen sends file_paths as JSON string — coercion handles it."""
        taskit_add_comment(
            content="Done",
            task_id="99",
            comment_type="proof",
            file_paths='["/code/main.py"]',
        )
        mock_client.submit_proof.assert_called_once_with(
            summary="Done", files=["/code/main.py"],
            screenshot_urls=None, attachment_ids=None,
        )

    def test_status_update_ignores_screenshot_paths(self, mock_client):
        """Non-proof comment types ignore screenshot_paths."""
        result = taskit_add_comment(
            content="Starting work",
            task_id="99",
            comment_type="status_update",
            screenshot_paths=["/tmp/shot.png"],
        )
        mock_client.upload_screenshots.assert_not_called()
        mock_client.post_comment.assert_called_once_with("Starting work", comment_type="status_update")
        assert result == {"comment_id": 43}

    def test_question_ignores_screenshot_paths(self, mock_client):
        """Question comments ignore screenshot_paths."""
        result = taskit_add_comment(
            content="Is this right?",
            task_id="99",
            comment_type="question",
            screenshot_paths=["/tmp/shot.png"],
        )
        mock_client.upload_screenshots.assert_not_called()
        mock_client.ask_question.assert_called_once()

    def test_screenshot_upload_failure_logs_warning(self, mock_client):
        """Upload failure logs a WARNING with exception details."""
        mock_client.upload_screenshots.side_effect = FileNotFoundError("not found: /tmp/x.png")
        with patch("odin.mcps.taskit_mcp.server.logger") as mock_logger:
            taskit_add_comment(
                content="Done",
                task_id="99",
                comment_type="proof",
                screenshot_paths=["/tmp/x.png"],
            )
            mock_logger.warning.assert_called_once()
            args = mock_logger.warning.call_args[0]
            assert "Screenshot upload failed" in args[0]
            assert "99" == args[1]  # task_id

    def test_extract_paths_logs_when_referenced_but_missing(self):
        """_extract_screenshot_paths logs when paths are in content but files don't exist."""
        with patch("odin.mcps.taskit_mcp.server.logger") as mock_logger:
            result = server_mod._extract_screenshot_paths(
                "Proof attached: /tmp/proof_nonexistent_9999999.png"
            )
            assert result is None
            mock_logger.warning.assert_called_once()
            args = mock_logger.warning.call_args[0]
            assert "not found on disk" in args[0]
            assert "/tmp/proof_nonexistent_9999999.png" in args[1]
