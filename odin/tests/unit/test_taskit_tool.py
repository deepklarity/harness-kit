"""Tests for taskit-tool core library and CLI.

Covers:
- TaskItToolClient.post_comment() calls correct API
- TaskItToolClient.ask_question() with and without wait
- TaskItToolClient.submit_proof() with correct attachments
- TaskItToolClient.get_context() returns task info
- CLI parses args and calls client correctly
- Client reads config from env vars
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from odin.tools.core import TaskItToolClient, client_from_env
from odin.tools.cli import main as cli_main


# ── TaskItToolClient unit tests ─────────────────────────────────────


class TestTaskItToolClientPostComment:
    def test_post_comment_calls_correct_api(self):
        """post_comment() POSTs to /tasks/:id/comments/ with correct body."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
            author_label="claude (sonnet)",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "content": "hello"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=mock_resp) as mock_post:
            result = client.post_comment("hello")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://localhost:8000/tasks/42/comments/"
        body = call_args[1]["json"]
        assert body["author_email"] == "agent@odin.agent"
        assert body["author_label"] == "claude (sonnet)"
        assert body["content"] == "hello"
        assert result == {"id": 1, "content": "hello"}


class TestTaskItToolClientAsk:
    def test_ask_no_wait(self):
        """ask(wait=False) posts question and returns immediately."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 5, "content": "What db?"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=mock_resp):
            result = client.ask_question("What db?", wait=False)

        assert result == {"id": 5, "content": "What db?"}

    def test_ask_with_wait_gets_reply(self):
        """ask(wait=True) polls and returns reply when available."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        # First call: POST question
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "What db?"}
        post_resp.raise_for_status = MagicMock()

        # Second call: GET poll — returns reply
        get_resp = MagicMock()
        get_resp.json.return_value = {
            "results": [
                {
                    "id": 6,
                    "content": "Use PostgreSQL",
                    "attachments": [{"type": "reply", "reply_to": 5}],
                }
            ]
        }
        get_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep"):
            result = client.ask_question("What db?", wait=True, timeout=10)

        assert result["reply"] == "Use PostgreSQL"

    def test_ask_timeout(self):
        """ask(wait=True) times out gracefully when no reply arrives."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "What db?"}
        post_resp.raise_for_status = MagicMock()

        get_resp = MagicMock()
        get_resp.json.return_value = {"results": []}
        get_resp.raise_for_status = MagicMock()

        # Make time.time() return values that exceed timeout immediately
        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", return_value=get_resp), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 999]):
            result = client.ask_question("What db?", wait=True, timeout=5)

        assert result["reply"] is None


class TestTaskItToolClientProof:
    def test_submit_proof(self):
        """Proof posted with correct attachments structure."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 10, "content": "Proof: All tests pass"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=mock_resp) as mock_post:
            result = client.submit_proof(
                summary="All tests pass",
                steps=["pytest -v", "ruff check ."],
                files=["src/auth.py"],
                handover="Use REDIS_URL env var",
            )

        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["attachments"][0]["type"] == "proof"
        assert body["attachments"][0]["summary"] == "All tests pass"
        assert body["attachments"][0]["steps"] == ["pytest -v", "ruff check ."]
        assert body["attachments"][0]["files"] == ["src/auth.py"]
        assert body["attachments"][0]["handover"] == "Use REDIS_URL env var"
        assert result["id"] == 10

    def test_submit_proof_minimal(self):
        """Proof with only summary — no steps/files/handover."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 11, "content": "Proof: Done"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=mock_resp) as mock_post:
            client.submit_proof(summary="Done")

        body = mock_post.call_args[1]["json"]
        att = body["attachments"][0]
        assert att == {"type": "proof", "summary": "Done"}


class TestTaskItToolClientContext:
    def test_get_context(self):
        """get_context() returns task info and comments."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": 42,
            "title": "Fix auth",
            "description": "Fix the auth bug",
            "status": "IN_PROGRESS",
            "metadata": {"model": "sonnet"},
            "comments": [{"id": 1, "content": "Started"}],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(httpx, "get", return_value=mock_resp):
            result = client.get_context()

        assert result["task_id"] == 42
        assert result["title"] == "Fix auth"
        assert result["status"] == "IN_PROGRESS"
        assert len(result["comments"]) == 1


# ── Environment variable config ─────────────────────────────────────


class TestClientFromEnv:
    def test_reads_env_vars(self):
        """client_from_env() reads TASKIT_URL, TASKIT_TASK_ID from env."""
        env = {
            "TASKIT_URL": "http://myhost:9000",
            "TASKIT_TASK_ID": "99",
            "TASKIT_AUTH_TOKEN": "tok123",
            "TASKIT_AUTHOR_EMAIL": "test@odin.agent",
            "TASKIT_AUTHOR_LABEL": "test agent",
        }
        with patch.dict(os.environ, env, clear=False):
            client = client_from_env()

        assert client.base_url == "http://myhost:9000"
        assert client.task_id == "99"
        assert client.auth_token == "tok123"
        assert client.author_email == "test@odin.agent"
        assert client.author_label == "test agent"

    def test_missing_task_id_raises(self):
        """client_from_env() raises ValueError if TASKIT_TASK_ID is not set."""
        env = {"TASKIT_TASK_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TASKIT_TASK_ID", None)
            with pytest.raises(ValueError, match="TASKIT_TASK_ID"):
                client_from_env()


# ── CLI tests ────────────────────────────────────────────────────────


class TestCLIComment:
    def test_cli_comment_command(self, capsys):
        """CLI parses 'comment' args and calls client correctly."""
        mock_client = MagicMock()
        mock_client.post_comment.return_value = {"id": 1}

        with patch("odin.tools.cli.client_from_env", return_value=mock_client):
            cli_main(["comment", "progress update"])

        mock_client.post_comment.assert_called_once_with("progress update")
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["comment_id"] == 1


class TestCLIProof:
    def test_cli_proof_command(self, capsys):
        """CLI parses --summary, --steps, --files correctly."""
        mock_client = MagicMock()
        mock_client.submit_proof.return_value = {"id": 5}

        with patch("odin.tools.cli.client_from_env", return_value=mock_client):
            cli_main([
                "proof",
                "--summary", "All tests pass",
                "--steps", '["pytest -v", "ruff check ."]',
                "--files", '["src/auth.py"]',
            ])

        mock_client.submit_proof.assert_called_once_with(
            summary="All tests pass",
            steps=["pytest -v", "ruff check ."],
            files=["src/auth.py"],
            handover=None,
        )
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True

    def test_cli_proof_with_handover(self, capsys):
        """CLI passes --handover to client."""
        mock_client = MagicMock()
        mock_client.submit_proof.return_value = {"id": 6}

        with patch("odin.tools.cli.client_from_env", return_value=mock_client):
            cli_main([
                "proof",
                "--summary", "Implemented login",
                "--handover", "Session tokens stored in Redis",
            ])

        mock_client.submit_proof.assert_called_once_with(
            summary="Implemented login",
            steps=None,
            files=None,
            handover="Session tokens stored in Redis",
        )


class TestCLIAsk:
    def test_cli_ask_no_wait(self, capsys):
        """CLI 'ask' without --wait returns immediately."""
        mock_client = MagicMock()
        mock_client.ask_question.return_value = {"id": 7, "content": "What?"}

        with patch("odin.tools.cli.client_from_env", return_value=mock_client):
            cli_main(["ask", "What?"])

        mock_client.ask_question.assert_called_once_with("What?", wait=False, timeout=300)


class TestCLIContext:
    def test_cli_context_command(self, capsys):
        """CLI 'context' calls get_context and prints JSON."""
        mock_client = MagicMock()
        mock_client.get_context.return_value = {
            "task_id": 42,
            "title": "Test",
            "status": "TODO",
        }

        with patch("odin.tools.cli.client_from_env", return_value=mock_client):
            cli_main(["context"])

        mock_client.get_context.assert_called_once()
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["task_id"] == 42


# ── Polling edge cases ────────────────────────────────────────


class TestPollEdgeCases:
    """Anti-test cases for polling with malformed or edge-case data."""

    def test_poll_with_malformed_attachments(self):
        """Comments with non-dict attachments don't crash polling."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "Q?"}
        post_resp.raise_for_status = MagicMock()

        # Attachment is a string, not a dict
        get_resp_bad = MagicMock()
        get_resp_bad.json.return_value = {
            "results": [
                {"id": 6, "content": "Noise", "attachments": ["string_attachment", 42, None]},
            ]
        }
        get_resp_bad.raise_for_status = MagicMock()

        # Second poll returns actual reply
        get_resp_good = MagicMock()
        get_resp_good.json.return_value = {
            "results": [
                {"id": 7, "content": "Answer", "attachments": [{"type": "reply", "reply_to": 5}]},
            ]
        }
        get_resp_good.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_resp_bad, get_resp_good]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2]):
            result = client.ask_question("Q?", wait=True, timeout=10)

        assert result["reply"] == "Answer"

    def test_poll_with_empty_attachments(self):
        """Comments with attachments: [] are skipped gracefully."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "Q?"}
        post_resp.raise_for_status = MagicMock()

        get_empty_att = MagicMock()
        get_empty_att.json.return_value = {
            "results": [
                {"id": 6, "content": "No attachments", "attachments": []},
            ]
        }
        get_empty_att.raise_for_status = MagicMock()

        get_reply = MagicMock()
        get_reply.json.return_value = {
            "results": [
                {"id": 7, "content": "Reply", "attachments": [{"type": "reply", "reply_to": 5}]},
            ]
        }
        get_reply.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_empty_att, get_reply]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2]):
            result = client.ask_question("Q?", wait=True, timeout=10)

        assert result["reply"] == "Reply"

    def test_poll_with_missing_reply_to_field(self):
        """Reply attachment without reply_to key doesn't match."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "Q?"}
        post_resp.raise_for_status = MagicMock()

        # Reply attachment missing reply_to
        get_no_reply_to = MagicMock()
        get_no_reply_to.json.return_value = {
            "results": [
                {"id": 6, "content": "Bad reply", "attachments": [{"type": "reply"}]},
            ]
        }
        get_no_reply_to.raise_for_status = MagicMock()

        # Proper reply
        get_good = MagicMock()
        get_good.json.return_value = {
            "results": [
                {"id": 7, "content": "Good reply", "attachments": [{"type": "reply", "reply_to": 5}]},
            ]
        }
        get_good.raise_for_status = MagicMock()

        with patch.object(httpx, "post", return_value=post_resp), \
             patch.object(httpx, "get", side_effect=[get_no_reply_to, get_good]), \
             patch("time.sleep"), \
             patch("odin.tools.core.time.time", side_effect=[0, 0, 1, 2]):
            result = client.ask_question("Q?", wait=True, timeout=10)

        assert result["reply"] == "Good reply"

    def test_ask_question_http_error_raises(self):
        """POST to /question/ returning 500 propagates error."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

        with patch.object(httpx, "post", return_value=post_resp):
            with pytest.raises(httpx.HTTPStatusError):
                client.ask_question("Error?", wait=False)

    def test_poll_http_error_raises(self):
        """GET poll returning 500 propagates error."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.json.return_value = {"id": 5, "content": "Q?"}
        post_resp.raise_for_status = MagicMock()

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

    def test_ask_question_auth_failure(self):
        """401 on POST question raises (not silently ignored)."""
        client = TaskItToolClient(
            base_url="http://localhost:8000",
            task_id="42",
            author_email="agent@odin.agent",
        )
        post_resp = MagicMock()
        post_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        with patch.object(httpx, "post", return_value=post_resp):
            with pytest.raises(httpx.HTTPStatusError):
                client.ask_question("Unauthorized?", wait=False)


class TestCLIMissingEnv:
    def test_cli_exits_on_missing_task_id(self):
        """CLI exits with error when TASKIT_TASK_ID is not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TASKIT_TASK_ID", None)
            with pytest.raises(SystemExit) as exc_info:
                cli_main(["comment", "hello"])
            assert exc_info.value.code == 1
