"""Tests for TaskItToolClient comment_type parameter."""

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def client():
    from odin.tools.core import TaskItToolClient

    return TaskItToolClient(
        base_url="http://localhost:8000",
        task_id="42",
        author_email="agent@odin.agent",
    )


@patch("odin.tools.core.httpx.post")
def test_post_comment_sends_comment_type(mock_post, client):
    """post_comment with comment_type includes it in JSON payload."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 1}
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    client.post_comment("Status data", comment_type="status_update")

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    assert payload["comment_type"] == "status_update"


@patch("odin.tools.core.httpx.post")
def test_post_comment_defaults_to_status_update(mock_post, client):
    """post_comment without comment_type sends status_update."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 1}
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    client.post_comment("Hello")

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    assert payload["comment_type"] == "status_update"


@patch("odin.tools.core.httpx.post")
def test_submit_proof_sends_proof_comment_type(mock_post, client):
    """submit_proof() sends comment_type='proof' in POST body."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 1}
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    client.submit_proof(summary="All tests pass", files=["output.log"])

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    assert payload["comment_type"] == "proof"
    assert payload["content"] == "Proof: All tests pass"
    assert len(payload["attachments"]) == 1
    assert payload["attachments"][0]["type"] == "proof"
