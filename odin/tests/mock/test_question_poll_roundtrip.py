"""Full mock-level tests of the question → poll → reply cycle.

Tests verify the TaskItToolClient.ask_question() → _poll_for_reply() roundtrip
with various polling scenarios — correct endpoints, reply matching, timeout,
and error propagation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from odin.tools.core import TaskItToolClient


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def client():
    return TaskItToolClient(
        base_url="http://localhost:8000",
        task_id="42",
        author_email="agent@odin.agent",
        author_label="claude (sonnet)",
    )


def _mock_post_resp(comment_id=5, content="What db?"):
    """Create a mock POST /question/ response."""
    resp = MagicMock()
    resp.json.return_value = {"id": comment_id, "content": content}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_get_resp(results):
    """Create a mock GET /comments/?after= response."""
    resp = MagicMock()
    resp.json.return_value = {"results": results}
    resp.raise_for_status = MagicMock()
    return resp


# ── Endpoint targeting ────────────────────────────────────────


class TestQuestionEndpoint:
    def test_question_posts_to_question_endpoint(self, client):
        """ask_question() POSTs to /tasks/:id/question/ (not /comments/)."""
        with patch.object(httpx, "post", return_value=_mock_post_resp()) as mock_post:
            client.ask_question("What db?", wait=False)

        url = mock_post.call_args[0][0]
        assert url == "http://localhost:8000/tasks/42/question/"
        assert "/comments/" not in url


class TestPollEndpoint:
    def test_poll_hits_comments_after_endpoint(self, client):
        """Polling GETs /tasks/:id/comments/?after=<comment_id>."""
        post_resp = _mock_post_resp(comment_id=5)
        # Return reply on first poll
        get_resp = _mock_get_resp([
            {
                "id": 6,
                "content": "Use PostgreSQL",
                "attachments": [{"type": "reply", "reply_to": 5}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp) as mock_get, \
             patch("time.sleep"):
            client.ask_question("What db?", wait=True, timeout=10)

        url = mock_get.call_args[0][0]
        assert url == "http://localhost:8000/tasks/42/comments/?after=5"


# ── Reply matching ────────────────────────────────────────────


class TestPollReplyMatching:
    def test_poll_finds_reply_by_attachment_type(self, client):
        """Reply detected via attachment.type == 'reply' && reply_to == comment_id."""
        post_resp = _mock_post_resp(comment_id=10)
        get_resp = _mock_get_resp([
            {
                "id": 11,
                "content": "Use Redis",
                "attachments": [{"type": "reply", "reply_to": 10}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep"):
            result = client.ask_question("What cache?", wait=True, timeout=10)

        assert result["reply"] == "Use Redis"

    def test_poll_ignores_non_reply_comments(self, client):
        """Status updates posted after question don't satisfy the poll."""
        post_resp = _mock_post_resp(comment_id=10)

        # First poll: status update (not a reply)
        get_first = _mock_get_resp([
            {
                "id": 11,
                "content": "I'm working on it",
                "attachments": [{"type": "status"}],
            }
        ])
        # Second poll: actual reply
        get_second = _mock_get_resp([
            {
                "id": 11,
                "content": "I'm working on it",
                "attachments": [{"type": "status"}],
            },
            {
                "id": 12,
                "content": "Use JWT",
                "attachments": [{"type": "reply", "reply_to": 10}],
            },
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_first, get_second]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2]):
            result = client.ask_question("Auth method?", wait=True, timeout=10)

        assert result["reply"] == "Use JWT"

    def test_poll_ignores_reply_to_different_question(self, client):
        """Reply to a different question ID is skipped."""
        post_resp = _mock_post_resp(comment_id=10)

        # Poll returns reply to question 99, not question 10
        get_wrong = _mock_get_resp([
            {
                "id": 11,
                "content": "Wrong answer",
                "attachments": [{"type": "reply", "reply_to": 99}],
            }
        ])
        # Second poll: correct reply
        get_right = _mock_get_resp([
            {
                "id": 11,
                "content": "Wrong answer",
                "attachments": [{"type": "reply", "reply_to": 99}],
            },
            {
                "id": 12,
                "content": "Right answer",
                "attachments": [{"type": "reply", "reply_to": 10}],
            },
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_wrong, get_right]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2]):
            result = client.ask_question("Correct?", wait=True, timeout=10)

        assert result["reply"] == "Right answer"


# ── Timeout and polling behavior ──────────────────────────────


class TestPollTimingBehavior:
    def test_indefinite_poll_no_deadline(self, client):
        """timeout=0 sets deadline=None (no time-based exit)."""
        post_resp = _mock_post_resp(comment_id=5)
        # Reply arrives on first poll
        get_resp = _mock_get_resp([
            {
                "id": 6,
                "content": "Eventually",
                "attachments": [{"type": "reply", "reply_to": 5}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep") as mock_sleep:
            result = client.ask_question("Wait forever?", wait=True, timeout=0)

        assert result["reply"] == "Eventually"
        # Should have slept at least once
        mock_sleep.assert_called()

    def test_poll_interval_is_5_seconds(self, client):
        """time.sleep(5) called between poll attempts."""
        post_resp = _mock_post_resp(comment_id=5)
        get_resp = _mock_get_resp([
            {
                "id": 6,
                "content": "Reply",
                "attachments": [{"type": "reply", "reply_to": 5}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep") as mock_sleep:
            client.ask_question("Interval?", wait=True, timeout=10)

        mock_sleep.assert_called_with(5)


# ── MCP tool return format ────────────────────────────────────


class TestMcpReturnFormat:
    def test_mcp_question_returns_reply_content(self, client):
        """Full ask_question(wait=True) returns {"id": ..., "reply": "..."}."""
        post_resp = _mock_post_resp(comment_id=7)
        get_resp = _mock_get_resp([
            {
                "id": 8,
                "content": "The answer is 42",
                "attachments": [{"type": "reply", "reply_to": 7}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep"):
            result = client.ask_question("The question?", wait=True, timeout=10)

        assert result["id"] == 7
        assert result["reply"] == "The answer is 42"

    def test_mcp_question_blocks_until_reply(self, client):
        """MCP tool doesn't return until poll finds reply (multiple polls)."""
        post_resp = _mock_post_resp(comment_id=5)

        # Two empty polls, then a reply
        get_empty1 = _mock_get_resp([])
        get_empty2 = _mock_get_resp([])
        get_reply = _mock_get_resp([
            {
                "id": 6,
                "content": "Finally!",
                "attachments": [{"type": "reply", "reply_to": 5}],
            }
        ])

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_empty1, get_empty2, get_reply]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2, 3]):
            result = client.ask_question("Wait?", wait=True, timeout=60)

        assert result["reply"] == "Finally!"


# ── Error propagation ─────────────────────────────────────────


class TestErrorPropagation:
    def test_network_error_during_poll_propagates(self, client):
        """HTTP error during polling raises (agent can retry)."""
        post_resp = _mock_post_resp(comment_id=5)

        # GET poll raises HTTP error
        get_error = MagicMock()
        get_error.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_error), \
             patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client.ask_question("Error?", wait=True, timeout=10)
