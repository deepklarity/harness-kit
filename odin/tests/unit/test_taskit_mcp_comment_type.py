"""Tests for TaskIt MCP server comment_type support."""

from unittest.mock import patch, MagicMock

import pytest


def test_comment_type_enum_values():
    """CommentType enum includes status_update, proof, and question (telemetry removed)."""
    from odin.mcps.taskit_mcp.server import CommentType

    values = {e.value for e in CommentType}
    assert "status_update" in values
    assert "proof" in values
    assert "question" in values
    assert "telemetry" not in values


@patch("odin.mcps.taskit_mcp.server._make_client")
def test_status_update_calls_post_comment_with_type(mock_make_client):
    """taskit_add_comment with default type passes status_update."""
    from odin.mcps.taskit_mcp.server import taskit_add_comment, CommentType

    mock_client = MagicMock()
    mock_client.post_comment.return_value = {"id": 1}
    mock_make_client.return_value = mock_client

    taskit_add_comment(
        task_id="42",
        content="Making progress",
        comment_type=CommentType.status_update,
    )

    mock_client.post_comment.assert_called_once_with(
        "Making progress", comment_type="status_update"
    )


@patch("odin.mcps.taskit_mcp.server._make_client")
def test_string_comment_type_works(mock_make_client):
    """taskit_add_comment accepts string comment_type (FastMCP compat)."""
    from odin.mcps.taskit_mcp.server import taskit_add_comment

    mock_client = MagicMock()
    mock_client.post_comment.return_value = {"id": 1}
    mock_make_client.return_value = mock_client

    result = taskit_add_comment(
        task_id="42",
        content="Progress update",
        comment_type="status_update",
    )

    mock_client.post_comment.assert_called_once_with(
        "Progress update", comment_type="status_update"
    )
    assert result == {"comment_id": 1}
