"""Unit tests for TaskIt MCP server tools.

Tests use mocked TaskItToolClient — no network required.
"""

from unittest.mock import MagicMock, patch

import pytest

import odin.mcps.taskit_mcp.server as server_mod
from odin.mcps.taskit_mcp.server import (
    AttachmentType,
    CommentType,
    _get_auth_token,
    _make_client,
    _resolve_auth_token,
    taskit_add_comment,
    taskit_add_attachment,
)


@pytest.fixture
def mock_client():
    """Return a mocked TaskItToolClient."""
    client = MagicMock()
    client.post_comment.return_value = {"id": 1, "content": "test"}
    client.ask_question.return_value = {
        "id": 2,
        "content": "question?",
        "reply": "answer!",
    }
    client.submit_proof.return_value = {"id": 3, "content": "Proof: done"}
    return client


@pytest.fixture(autouse=True)
def patch_make_client(mock_client):
    """Patch _make_client to return the mock for all tests."""
    with patch(
        "odin.mcps.taskit_mcp.server._make_client", return_value=mock_client
    ):
        yield


@pytest.fixture
def reset_auth_cache():
    """Reset the module-level auth token cache between tests."""
    server_mod._auth_token = None
    yield
    server_mod._auth_token = None


# ── taskit_add_comment ──────────────────────────────────────────


class TestAddComment:
    def test_status_update_calls_post_comment(self, mock_client):
        result = taskit_add_comment(task_id="42", content="Progress update")
        mock_client.post_comment.assert_called_once_with(
            "Progress update", comment_type="status_update"
        )
        assert result == {"comment_id": 1}

    def test_question_calls_ask_question_blocking(self, mock_client):
        result = taskit_add_comment(
            task_id="42",
            content="How should I proceed?",
            comment_type=CommentType.question,
        )
        mock_client.ask_question.assert_called_once_with(
            "How should I proceed?", wait=True, timeout=0
        )
        assert result == {"comment_id": 2, "reply": "answer!"}

    def test_question_timeout_returns_none_reply(self, mock_client):
        mock_client.ask_question.return_value = {
            "id": 5,
            "content": "question",
            "reply": None,
        }
        result = taskit_add_comment(
            task_id="42",
            content="Anyone there?",
            comment_type=CommentType.question,
        )
        assert result == {"comment_id": 5, "reply": None}

    def test_default_comment_type_is_status_update(self, mock_client):
        taskit_add_comment(task_id="42", content="update")
        mock_client.post_comment.assert_called_once()
        mock_client.ask_question.assert_not_called()

    def test_string_comment_type_status_update(self, mock_client):
        """CommentType enum accepts string values."""
        result = taskit_add_comment(
            task_id="42", content="update", comment_type="status_update"
        )
        mock_client.post_comment.assert_called_once()
        assert "comment_id" in result

    def test_string_comment_type_question(self, mock_client):
        result = taskit_add_comment(
            task_id="42", content="question?", comment_type="question"
        )
        mock_client.ask_question.assert_called_once()
        assert "reply" in result

    def test_proof_calls_submit_proof(self, mock_client):
        """comment_type=proof routes to client.submit_proof()."""
        result = taskit_add_comment(
            task_id="42",
            content="All tests pass",
            comment_type=CommentType.proof,
        )
        mock_client.submit_proof.assert_called_once_with(
            summary="All tests pass", files=None, screenshot_urls=None,
            attachment_ids=None,
        )
        assert result == {"comment_id": 3, "screenshots_attached": 0}

    def test_proof_with_file_paths(self, mock_client):
        """file_paths are forwarded to submit_proof()."""
        taskit_add_comment(
            task_id="42",
            content="Tests pass",
            comment_type=CommentType.proof,
            file_paths=["tests/output.log", "coverage.html"],
        )
        mock_client.submit_proof.assert_called_once_with(
            summary="Tests pass", files=["tests/output.log", "coverage.html"],
            screenshot_urls=None, attachment_ids=None,
        )

    def test_string_comment_type_proof(self, mock_client):
        """String 'proof' is accepted as comment_type."""
        result = taskit_add_comment(
            task_id="42", content="Done", comment_type="proof"
        )
        mock_client.submit_proof.assert_called_once()
        assert result == {"comment_id": 3, "screenshots_attached": 0}


# ── taskit_add_attachment ───────────────────────────────────────


class TestAddAttachment:
    def test_proof_calls_submit_proof(self, mock_client):
        result = taskit_add_attachment(
            task_id="42",
            content="Tests pass",
            file_paths=["tests/output.log"],
            attachment_type=AttachmentType.proof,
        )
        mock_client.submit_proof.assert_called_once_with(
            summary="Tests pass", files=["tests/output.log"]
        )
        assert result == {"comment_id": 3}

    def test_file_calls_post_comment(self, mock_client):
        result = taskit_add_attachment(
            task_id="42",
            content="See screenshot",
        )
        mock_client.post_comment.assert_called_once_with("See screenshot")
        assert result == {"comment_id": 1}

    def test_proof_without_files(self, mock_client):
        taskit_add_attachment(
            task_id="42",
            content="Manual verification",
            attachment_type=AttachmentType.proof,
        )
        mock_client.submit_proof.assert_called_once_with(
            summary="Manual verification", files=None
        )

    def test_default_attachment_type_is_file(self, mock_client):
        taskit_add_attachment(task_id="42", content="info")
        mock_client.post_comment.assert_called_once()
        mock_client.submit_proof.assert_not_called()


# ── _make_client env var resolution ─────────────────────────────


class TestMakeClient:
    def test_defaults(self, reset_auth_cache):
        with patch.dict("os.environ", {}, clear=True):
            client = _make_client("99")
            assert client.base_url == "http://localhost:8000"
            assert client.task_id == "99"
            assert client.auth_token == ""
            assert client.author_email == "agent@odin.agent"
            assert client.author_label == ""

    def test_env_vars_override(self, reset_auth_cache):
        env = {
            "TASKIT_URL": "https://taskit.example.com",
            "TASKIT_AUTH_TOKEN": "tok123",
            "TASKIT_AUTHOR_EMAIL": "bot@ci.com",
            "TASKIT_AUTHOR_LABEL": "CI Bot",
        }
        with patch.dict("os.environ", env, clear=True):
            client = _make_client("7")
            assert client.base_url == "https://taskit.example.com"
            assert client.task_id == "7"
            assert client.auth_token == "tok123"
            assert client.author_email == "bot@ci.com"
            assert client.author_label == "CI Bot"


# ── _resolve_auth_token ─────────────────────────────────────────


class TestResolveAuthToken:
    def test_explicit_token_used_directly(self):
        """TASKIT_AUTH_TOKEN is returned without hitting TaskItAuth."""
        with patch.dict("os.environ", {"TASKIT_AUTH_TOKEN": "explicit-tok"}, clear=True):
            assert _resolve_auth_token() == "explicit-tok"

    def test_no_credentials_returns_empty(self):
        """No credentials configured → empty string (unauthenticated)."""
        with patch.dict("os.environ", {}, clear=True):
            assert _resolve_auth_token() == ""

    def test_credentials_auth_success(self):
        """ODIN_ADMIN_USER + PASSWORD → authenticates via TaskItAuth."""
        env = {
            "ODIN_ADMIN_USER": "admin@test.com",
            "ODIN_ADMIN_PASSWORD": "secret",
            "TASKIT_URL": "http://taskit:9000",
        }
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "bearer-tok-123"
        with patch.dict("os.environ", env, clear=True), \
             patch("odin.backends.taskit.TaskItAuth", return_value=mock_auth) as cls:
            result = _resolve_auth_token()
            cls.assert_called_once_with("http://taskit:9000/auth/login/", "admin@test.com", "secret")
            assert result == "bearer-tok-123"

    def test_credentials_auth_failure_raises(self):
        """Credentials present but auth fails → error propagates (no silent degradation)."""
        from odin.backends.taskit import TaskItAuthError
        env = {
            "ODIN_ADMIN_USER": "admin@test.com",
            "ODIN_ADMIN_PASSWORD": "wrong",
        }
        mock_auth = MagicMock()
        mock_auth.get_token.side_effect = TaskItAuthError("Login failed")
        with patch.dict("os.environ", env, clear=True), \
             patch("odin.backends.taskit.TaskItAuth", return_value=mock_auth):
            with pytest.raises(TaskItAuthError, match="Login failed"):
                _resolve_auth_token()

    def test_credentials_use_taskit_url_default(self):
        """When TASKIT_URL not set, auth uses http://localhost:8000."""
        env = {
            "ODIN_ADMIN_USER": "admin@test.com",
            "ODIN_ADMIN_PASSWORD": "secret",
        }
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "tok"
        with patch.dict("os.environ", env, clear=True), \
             patch("odin.backends.taskit.TaskItAuth", return_value=mock_auth) as cls:
            _resolve_auth_token()
            cls.assert_called_once_with("http://localhost:8000/auth/login/", "admin@test.com", "secret")

    def test_explicit_token_takes_priority_over_credentials(self):
        """TASKIT_AUTH_TOKEN wins even when ODIN_ADMIN_* are also set."""
        env = {
            "TASKIT_AUTH_TOKEN": "explicit",
            "ODIN_ADMIN_USER": "admin@test.com",
            "ODIN_ADMIN_PASSWORD": "secret",
        }
        with patch.dict("os.environ", env, clear=True):
            assert _resolve_auth_token() == "explicit"


# ── _get_auth_token caching ─────────────────────────────────────


class TestGetAuthToken:
    def test_caches_resolved_token(self, reset_auth_cache):
        """Token is resolved once and cached for subsequent calls."""
        with patch.dict("os.environ", {"TASKIT_AUTH_TOKEN": "cached-tok"}, clear=True):
            first = _get_auth_token()
            assert first == "cached-tok"

        # Second call uses cache even though env is different
        with patch.dict("os.environ", {"TASKIT_AUTH_TOKEN": "new-tok"}, clear=True):
            second = _get_auth_token()
            assert second == "cached-tok"

    def test_cache_reset_allows_new_resolution(self, reset_auth_cache):
        """After resetting cache, a new token is resolved."""
        with patch.dict("os.environ", {"TASKIT_AUTH_TOKEN": "tok-1"}, clear=True):
            assert _get_auth_token() == "tok-1"

        server_mod._auth_token = None  # reset
        with patch.dict("os.environ", {"TASKIT_AUTH_TOKEN": "tok-2"}, clear=True):
            assert _get_auth_token() == "tok-2"


# ── task_id env var defaulting ─────────────────────────────────


class TestTaskIdEnvDefault:
    """task_id defaults to TASKIT_TASK_ID env var when omitted."""

    def test_add_comment_defaults_task_id_from_env(self, mock_client):
        """taskit_add_comment uses TASKIT_TASK_ID when task_id not provided."""
        with patch.dict("os.environ", {"TASKIT_TASK_ID": "env-task-77"}):
            result = taskit_add_comment(content="update from env")
        mock_client.post_comment.assert_called_once()
        assert result == {"comment_id": 1}

    def test_add_comment_explicit_task_id_overrides_env(self, mock_client):
        """Explicit task_id takes priority over TASKIT_TASK_ID env var."""
        with patch.dict("os.environ", {"TASKIT_TASK_ID": "env-task-77"}):
            result = taskit_add_comment(content="update", task_id="explicit-42")
        mock_client.post_comment.assert_called_once()
        assert result == {"comment_id": 1}

    def test_add_comment_no_task_id_no_env_returns_error(self, mock_client):
        """No task_id and no TASKIT_TASK_ID → error dict."""
        with patch.dict("os.environ", {}, clear=True):
            result = taskit_add_comment(content="lost")
        assert "error" in result
        mock_client.post_comment.assert_not_called()

    def test_add_attachment_defaults_task_id_from_env(self, mock_client):
        """taskit_add_attachment uses TASKIT_TASK_ID when task_id not provided."""
        with patch.dict("os.environ", {"TASKIT_TASK_ID": "env-task-88"}):
            result = taskit_add_attachment(content="proof from env")
        mock_client.post_comment.assert_called_once()
        assert result == {"comment_id": 1}

    def test_add_attachment_no_task_id_no_env_returns_error(self, mock_client):
        """No task_id and no TASKIT_TASK_ID → error dict."""
        with patch.dict("os.environ", {}, clear=True):
            result = taskit_add_attachment(content="lost")
        assert "error" in result
        mock_client.post_comment.assert_not_called()
