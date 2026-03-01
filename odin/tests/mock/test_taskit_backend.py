"""Tests for TaskItBackend — HTTP interactions with mocked responses.

Tags: [mock] — no real HTTP calls, no LLM.

Architecture under test:
  Odin ──POST /auth/login/──▶ TaskIt backend
       ◀── {token, expires_in} ──┘
  Odin ──Bearer token──▶ TaskIt API endpoints

Odin never talks to third-party auth directly. Auth is fully delegated to TaskIt.
"""

import time
from unittest.mock import patch

import httpx
import pytest

from odin.backends.taskit import (
    TaskItAuth,
    TaskItAuthError,
    TaskItBackend,
)
from odin.taskit.models import Task, TaskStatus


# ── Constants ─────────────────────────────────────────────────────────

FAKE_ADMIN_EMAIL = "admin@test.com"
FAKE_ADMIN_PASSWORD = "test123"
FAKE_TOKEN = "eyJhbGciOiJSUzI1NiJ9.FAKE_TOKEN"
LOGIN_URL = "http://localhost:8000/auth/login/"


# ── Helpers ───────────────────────────────────────────────────────────


def _login_success_response(token=FAKE_TOKEN, expires_in=3600):
    return httpx.Response(200, json={"token": token, "expires_in": expires_in})


def _login_auth_disabled_response():
    """TaskIt returns 200 with detail when auth is disabled."""
    return httpx.Response(200, json={"detail": "Auth is disabled."})


def _login_bad_credentials_response():
    return httpx.Response(401, json={"detail": "INVALID_PASSWORD"})


def _login_server_error_response():
    return httpx.Response(500, json={"detail": "Server auth configuration incomplete. Set FIREBASE_API_KEY."})


# ── TaskItAuth — login flow ──────────────────────────────────────────


class TestTaskItAuthLogin:
    def setup_method(self):
        TaskItAuth._token_cache.clear()

    def test_login_returns_token(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response()):
            token = auth.get_token()
        assert token == FAKE_TOKEN

    def test_login_caches_token(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response()) as mock_post:
            auth.get_token()
            auth.get_token()
        # Only one HTTP call — token was cached
        assert mock_post.call_count == 1

    def test_login_sends_correct_payload(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response()) as mock_post:
            auth.get_token()
        mock_post.assert_called_once_with(
            LOGIN_URL,
            json={"email": FAKE_ADMIN_EMAIL, "password": FAKE_ADMIN_PASSWORD},
            timeout=15,
        )

    def test_login_bad_credentials_raises_with_guidance(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_bad_credentials_response()):
            with pytest.raises(TaskItAuthError, match="ODIN_ADMIN_USER"):
                auth.get_token()

    def test_login_server_error_raises(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_server_error_response()):
            with pytest.raises(TaskItAuthError, match="500"):
                auth.get_token()

    def test_login_connection_error_raises_with_guidance(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(TaskItAuthError, match="TaskIt backend running"):
                auth.get_token()

    def test_login_when_auth_disabled_returns_empty_token(self):
        """When TaskIt has auth disabled, /auth/login/ returns 200 without a token."""
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_auth_disabled_response()):
            token = auth.get_token()
        assert token == ""

    def test_auth_disabled_token_is_cached_without_relogin(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_auth_disabled_response()) as mock_post:
            assert auth.get_token() == ""
            assert auth.get_token() == ""
        assert mock_post.call_count == 1


# ── TaskItAuth — token expiry & re-login ─────────────────────────────


class TestTaskItAuthExpiry:
    def setup_method(self):
        TaskItAuth._token_cache.clear()

    def test_re_login_when_token_expired(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        # Pre-populate cache with an expired token
        TaskItAuth._token_cache[auth._cache_key] = ("OLD_TOKEN", time.time() - 10)

        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response("NEW_TOKEN")):
            token = auth.get_token()
        assert token == "NEW_TOKEN"

    def test_re_login_when_near_expiry(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        # Token within 5-minute refresh margin
        TaskItAuth._token_cache[auth._cache_key] = ("OLD_TOKEN", time.time() + 60)

        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response("FRESH")):
            token = auth.get_token()
        assert token == "FRESH"

    def test_no_re_login_when_token_valid(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        TaskItAuth._token_cache[auth._cache_key] = ("VALID_TOKEN", time.time() + 3600)

        # Should NOT call httpx.post
        token = auth.get_token()
        assert token == "VALID_TOKEN"

    def test_expiry_set_from_response(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        with patch("odin.backends.taskit.httpx.post", return_value=_login_success_response(expires_in=7200)):
            auth.get_token()
        # Should be ~7200 seconds from now
        _, expires_at = TaskItAuth._token_cache[auth._cache_key]
        assert expires_at > time.time() + 7000


# ── TaskItAuth as httpx.Auth ──────────────────────────────────────────


class TestTaskItAuthFlow:
    def setup_method(self):
        TaskItAuth._token_cache.clear()

    def test_auth_flow_injects_bearer_header(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        TaskItAuth._token_cache[auth._cache_key] = ("MY_TOKEN", time.time() + 3600)

        request = httpx.Request("GET", "http://localhost:8000/tasks/")
        gen = auth.auth_flow(request)
        modified_request = next(gen)
        assert modified_request.headers["Authorization"] == "Bearer MY_TOKEN"

    def test_auth_flow_skips_empty_bearer_header(self):
        auth = TaskItAuth(LOGIN_URL, FAKE_ADMIN_EMAIL, FAKE_ADMIN_PASSWORD)
        TaskItAuth._token_cache[auth._cache_key] = ("", time.time() + 3600)

        request = httpx.Request(
            "GET",
            "http://localhost:8000/tasks/",
            headers={"Authorization": "Bearer stale"},
        )
        gen = auth.auth_flow(request)
        modified_request = next(gen)
        assert "Authorization" not in modified_request.headers


# ── TaskItBackend — auth configuration ────────────────────────────────


class TestTaskItBackendAuth:
    def setup_method(self):
        TaskItAuth._token_cache.clear()

    def test_backend_without_auth_has_no_auth_handler(self):
        backend = TaskItBackend(base_url="http://localhost:8000", board_id=1, created_by="odin")
        assert backend._client.auth is None

    def test_backend_with_auth_has_taskit_auth(self):
        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="odin",
            admin_email=FAKE_ADMIN_EMAIL,
            admin_password=FAKE_ADMIN_PASSWORD,
        )
        assert isinstance(backend._client.auth, TaskItAuth)

    def test_backend_partial_auth_config_no_auth(self):
        """If only email is provided (no password), auth is not configured."""
        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="odin",
            admin_email=FAKE_ADMIN_EMAIL,
        )
        assert backend._client.auth is None

    def test_auth_login_url_constructed_from_base_url(self):
        backend = TaskItBackend(
            base_url="http://myserver:9000",
            board_id=1,
            created_by="odin",
            admin_email=FAKE_ADMIN_EMAIL,
            admin_password=FAKE_ADMIN_PASSWORD,
        )
        assert backend._client.auth._login_url == "http://myserver:9000/auth/login/"

    def test_authenticated_request_includes_bearer_token(self):
        """Verify actual HTTP requests include the Bearer token."""
        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="odin",
            admin_email=FAKE_ADMIN_EMAIL,
            admin_password=FAKE_ADMIN_PASSWORD,
        )
        # Pre-populate token cache to avoid actual login call
        auth = backend._client.auth
        TaskItAuth._token_cache[auth._cache_key] = (FAKE_TOKEN, time.time() + 3600)

        captured_headers = {}

        def handler(request):
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            auth=backend._client.auth,
            transport=transport,
        )

        backend.load_all_tasks()
        assert "authorization" in captured_headers
        assert captured_headers["authorization"] == f"Bearer {FAKE_TOKEN}"

    def test_auth_disabled_request_sends_no_authorization_header(self):
        backend = TaskItBackend(
            base_url="http://localhost:8000",
            board_id=1,
            created_by="odin",
            admin_email=FAKE_ADMIN_EMAIL,
            admin_password=FAKE_ADMIN_PASSWORD,
        )
        auth = backend._client.auth
        TaskItAuth._token_cache[auth._cache_key] = ("", time.time() + 3600)

        captured_headers = {}

        def handler(request):
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            auth=backend._client.auth,
            transport=transport,
        )

        backend.load_all_tasks()
        assert "authorization" not in captured_headers


# ── TaskItBackend — CRUD with mocked HTTP ─────────────────────────────


class TestTaskItBackendCRUD:
    """Task CRUD operations with mocked HTTP responses (no auth)."""

    def _make_backend_with_transport(self, handler):
        backend = TaskItBackend(base_url="http://localhost:8000", board_id=1, created_by="odin")
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            transport=httpx.MockTransport(handler),
        )
        return backend

    def test_save_new_task(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(201, json={
                    "id": 42, "title": "Test", "description": "Desc",
                    "status": "TODO", "assignee": None, "labels": [],
                    "depends_on": [], "metadata": {},
                })
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        task = Task(id="abc123", title="Test", description="Desc", status=TaskStatus.TODO)
        result = backend.save_task(task)
        assert result.id == "42"
        assert result.metadata["taskit_id"] == 42

    def test_save_existing_task(self):
        def handler(request):
            if request.method == "PUT":
                return httpx.Response(200, json={
                    "id": 42, "title": "Updated", "status": "IN_PROGRESS",
                })
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        task = Task(id="42", title="Updated", description="D", status=TaskStatus.IN_PROGRESS)
        result = backend.save_task(task)
        assert result.id == "42"

    def test_load_task_found(self):
        def handler(request):
            return httpx.Response(200, json={
                "id": 42, "title": "Found", "description": "D",
                "status": "TODO", "assignee": None, "depends_on": [],
                "metadata": {},
            })

        backend = self._make_backend_with_transport(handler)
        task = backend.load_task("42")
        assert task is not None
        assert task.title == "Found"

    def test_load_task_not_found(self):
        backend = self._make_backend_with_transport(lambda r: httpx.Response(404))
        assert backend.load_task("999") is None

    def test_delete_task_success(self):
        backend = self._make_backend_with_transport(lambda r: httpx.Response(204))
        assert backend.delete_task("42") is True

    def test_delete_task_not_found(self):
        backend = self._make_backend_with_transport(lambda r: httpx.Response(404))
        assert backend.delete_task("999") is False

    def test_load_all_tasks(self):
        def handler(request):
            return httpx.Response(200, json=[
                {"id": 1, "title": "A", "description": "", "status": "TODO",
                 "assignee": None, "depends_on": [], "metadata": {}},
                {"id": 2, "title": "B", "description": "", "status": "IN_PROGRESS",
                 "assignee": {"email": "gemini@odin.agent", "id": 10}, "depends_on": [],
                 "metadata": {}},
            ])

        backend = self._make_backend_with_transport(handler)
        tasks = backend.load_all_tasks()
        assert len(tasks) == 2
        assert tasks[1].assigned_agent == "gemini"


# ── TaskItBackend — agent → assignee resolution ───────────────────────


class TestAgentAssigneeResolution:
    """Verify that assigned_agent on a task resolves to an assignee_id
    via _resolve_agent_user, confirming the unified agent=assignee model."""

    def _make_backend_with_transport(self, handler):
        backend = TaskItBackend(base_url="http://localhost:8000", board_id=1, created_by="odin")
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            transport=httpx.MockTransport(handler),
        )
        return backend

    def test_save_task_with_agent_sends_assignee_id(self):
        """When a task has assigned_agent, save_task should resolve it to
        a user and include assignee_id in the POST payload."""
        captured_payloads = []

        def handler(request):
            url = str(request.url)
            if "/users/" in url and request.method == "GET":
                # Agent user already exists
                return httpx.Response(200, json=[
                    {"id": 77, "email": "minimax@odin.agent", "name": "minimax"},
                ])
            if "/tasks/" in url and request.method == "POST":
                import json as _json
                captured_payloads.append(_json.loads(request.content))
                return httpx.Response(201, json={
                    "id": 10, "title": "Test", "description": "",
                    "status": "TODO", "assignee": {"id": 77, "email": "minimax@odin.agent"},
                    "labels": [], "depends_on": [], "metadata": {},
                })
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        task = Task(
            id="new1", title="Test", description="",
            status=TaskStatus.TODO, assigned_agent="minimax",
        )
        backend.save_task(task)

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["assignee_id"] == 77

    def test_resolve_agent_creates_user_when_not_found(self):
        """When the agent user doesn't exist, _resolve_agent_user creates it."""
        created_users = []

        def handler(request):
            url = str(request.url)
            if "/users/" in url and request.method == "GET":
                # No users found
                return httpx.Response(200, json=[])
            if "/users/" in url and request.method == "POST":
                import json as _json
                body = _json.loads(request.content)
                created_users.append(body)
                return httpx.Response(201, json={"id": 99, **body})
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        user_id = backend._resolve_agent_user("gemini")

        assert user_id == 99
        assert len(created_users) == 1
        assert created_users[0]["email"] == "gemini@odin.agent"
        assert created_users[0]["name"] == "gemini"

    def test_resolve_agent_caches_user_id(self):
        """Second call to _resolve_agent_user should use cache, not HTTP."""
        call_count = {"get": 0}

        def handler(request):
            url = str(request.url)
            if "/users/" in url and request.method == "GET":
                call_count["get"] += 1
                return httpx.Response(200, json=[
                    {"id": 42, "email": "claude@odin.agent", "name": "claude"},
                ])
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        id1 = backend._resolve_agent_user("claude")
        id2 = backend._resolve_agent_user("claude")

        assert id1 == id2 == 42
        assert call_count["get"] == 1  # Only one HTTP call

    def test_load_task_extracts_agent_from_assignee_email(self):
        """When loading a task, the assignee email @odin.agent is
        parsed back to assigned_agent, confirming round-trip consistency."""
        def handler(request):
            return httpx.Response(200, json={
                "id": 5, "title": "Loaded", "description": "D",
                "status": "IN_PROGRESS",
                "assignee": {"id": 77, "email": "minimax@odin.agent"},
                "depends_on": [], "metadata": {},
            })

        backend = self._make_backend_with_transport(handler)
        task = backend.load_task("5")
        assert task is not None
        assert task.assigned_agent == "minimax"


# ── TaskItBackend — get_comments ──────────────────────────────────────


class TestGetComments:
    """Verify get_comments() fetches via GET /tasks/:id/comments/."""

    def _make_backend_with_transport(self, handler):
        backend = TaskItBackend(base_url="http://localhost:8000", board_id=1, created_by="odin")
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            transport=httpx.MockTransport(handler),
        )
        return backend

    def test_get_comments_returns_list(self):
        def handler(request):
            return httpx.Response(200, json=[
                {"id": 1, "content": "First comment", "author_email": "odin@harness.kit"},
                {"id": 2, "content": "Second comment", "author_email": "mock@odin.agent"},
            ])

        backend = self._make_backend_with_transport(handler)
        comments = backend.get_comments("42")
        assert len(comments) == 2
        assert comments[0]["content"] == "First comment"
        assert comments[1]["content"] == "Second comment"

    def test_get_comments_handles_paginated_response(self):
        def handler(request):
            return httpx.Response(200, json={
                "count": 1, "next": None, "previous": None,
                "results": [
                    {"id": 1, "content": "Paginated comment", "author_email": "odin@harness.kit"},
                ],
            })

        backend = self._make_backend_with_transport(handler)
        comments = backend.get_comments("42")
        assert len(comments) == 1
        assert comments[0]["content"] == "Paginated comment"

    def test_get_comments_empty(self):
        backend = self._make_backend_with_transport(lambda r: httpx.Response(200, json=[]))
        comments = backend.get_comments("42")
        assert comments == []


# ── Config env var loading ────────────────────────────────────────────


class TestTaskItConfigFromEnv:
    def test_env_vars_populate_taskit_config(self, monkeypatch):
        monkeypatch.setenv("ODIN_ADMIN_USER", "admin@test.com")
        monkeypatch.setenv("ODIN_ADMIN_PASSWORD", "pass123")

        from odin.config import _apply_taskit_auth_env
        from odin.models import TaskItConfig

        cfg = _apply_taskit_auth_env(TaskItConfig())
        assert cfg.admin_email == "admin@test.com"
        assert cfg.admin_password == "pass123"

    def test_env_vars_not_set_leaves_defaults(self, monkeypatch):
        monkeypatch.delenv("ODIN_ADMIN_USER", raising=False)
        monkeypatch.delenv("ODIN_ADMIN_PASSWORD", raising=False)

        from odin.config import _apply_taskit_auth_env
        from odin.models import TaskItConfig

        cfg = _apply_taskit_auth_env(TaskItConfig())
        assert cfg.admin_email is None
        assert cfg.admin_password is None

    def test_env_vars_override_yaml_config(self, monkeypatch):
        monkeypatch.setenv("ODIN_ADMIN_USER", "env@test.com")
        monkeypatch.setenv("ODIN_ADMIN_PASSWORD", "env_pass")

        from odin.config import _apply_taskit_auth_env
        from odin.models import TaskItConfig

        yaml_cfg = TaskItConfig(admin_email="yaml@test.com", admin_password="yaml_pass")
        cfg = _apply_taskit_auth_env(yaml_cfg)
        assert cfg.admin_email == "env@test.com"
        assert cfg.admin_password == "env_pass"


# ── Paginated response handling ──────────────────────────────────────


class TestPaginatedResponseHandling:
    """DRF returns paginated dicts, not plain lists.

    Regression test for KeyError: 0 when save_spec received
    {"count": 1, "results": [{...}]} instead of [{...}].
    """

    def _make_backend_with_transport(self, handler):
        backend = TaskItBackend(base_url="http://localhost:8000", board_id=1, created_by="odin")
        backend._client = httpx.Client(
            base_url="http://localhost:8000",
            timeout=30,
            transport=httpx.MockTransport(handler),
        )
        return backend

    def test_save_spec_with_paginated_response(self):
        """save_spec should handle DRF paginated responses."""
        from odin.specs import SpecArchive

        def handler(request):
            url = str(request.url)
            if request.method == "GET" and "/specs/" in url:
                # DRF paginated response
                return httpx.Response(200, json={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [{"id": 10, "odin_id": "spec_abc", "title": "Old"}],
                })
            if request.method == "PUT":
                return httpx.Response(200, json={"id": 10, "title": "Updated"})
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        spec = SpecArchive(id="spec_abc", title="Updated", source="inline", content="x")
        result = backend.save_spec(spec)
        assert result.id == "spec_abc"

    def test_load_spec_with_paginated_response(self):
        """load_spec should handle DRF paginated responses."""
        from odin.specs import SpecArchive

        def handler(request):
            return httpx.Response(200, json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{
                    "id": 5, "odin_id": "sp_123", "title": "My Spec",
                    "source": "inline", "content": "content here",
                    "abandoned": False, "metadata": {},
                }],
            })

        backend = self._make_backend_with_transport(handler)
        spec = backend.load_spec("sp_123")
        assert spec is not None
        assert spec.title == "My Spec"

    def test_load_spec_paginated_empty(self):
        """load_spec returns None when paginated response has empty results."""
        def handler(request):
            return httpx.Response(200, json={
                "count": 0, "next": None, "previous": None, "results": [],
            })

        backend = self._make_backend_with_transport(handler)
        assert backend.load_spec("nonexistent") is None

    def test_load_all_tasks_with_paginated_response(self):
        """load_all_tasks should handle DRF paginated responses."""
        def handler(request):
            return httpx.Response(200, json={
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {"id": 1, "title": "A", "description": "", "status": "TODO",
                     "assignee": None, "depends_on": [], "metadata": {}},
                    {"id": 2, "title": "B", "description": "", "status": "DONE",
                     "assignee": None, "depends_on": [], "metadata": {}},
                ],
            })

        backend = self._make_backend_with_transport(handler)
        tasks = backend.load_all_tasks()
        assert len(tasks) == 2

    def test_load_all_tasks_fetches_all_pages(self):
        """load_all_tasks should follow next links until all pages are loaded."""
        def handler(request):
            url = str(request.url)
            if "page=2" in url:
                return httpx.Response(200, json={
                    "count": 2,
                    "next": None,
                    "previous": "http://localhost:8000/tasks/?board_id=1&page=1",
                    "results": [
                        {"id": 20, "title": "Second Page", "description": "", "status": "TODO",
                         "assignee": None, "depends_on": [], "metadata": {}},
                    ],
                })
            return httpx.Response(200, json={
                "count": 2,
                "next": "http://localhost:8000/tasks/?board_id=1&page=2",
                "previous": None,
                "results": [
                    {"id": 45, "title": "First Page", "description": "", "status": "TODO",
                     "assignee": None, "depends_on": [], "metadata": {}},
                ],
            })

        backend = self._make_backend_with_transport(handler)
        tasks = backend.load_all_tasks()
        assert [t.id for t in tasks] == ["45", "20"]

    def test_load_all_specs_with_paginated_response(self):
        """load_all_specs should handle DRF paginated responses."""
        def handler(request):
            return httpx.Response(200, json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{
                    "id": 3, "odin_id": "sp_x", "title": "Spec X",
                    "source": "file", "content": "stuff",
                    "abandoned": False, "metadata": {},
                }],
            })

        backend = self._make_backend_with_transport(handler)
        specs = backend.load_all_specs()
        assert len(specs) == 1
        assert specs[0].id == "sp_x"

    def test_set_spec_abandoned_with_paginated_response(self):
        """set_spec_abandoned should handle DRF paginated responses."""
        def handler(request):
            url = str(request.url)
            if request.method == "GET" and "/specs/" in url:
                return httpx.Response(200, json={
                    "count": 1, "next": None, "previous": None,
                    "results": [{"id": 7, "odin_id": "sp_old", "title": "Old"}],
                })
            if request.method == "PUT":
                return httpx.Response(200, json={"id": 7})
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        assert backend.set_spec_abandoned("sp_old") is True

    def test_delete_spec_with_paginated_response(self):
        """delete_spec should handle DRF paginated responses."""
        def handler(request):
            url = str(request.url)
            if request.method == "GET" and "/specs/" in url:
                return httpx.Response(200, json={
                    "count": 1, "next": None, "previous": None,
                    "results": [{"id": 9, "odin_id": "sp_del", "title": "Del"}],
                })
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(404)

        backend = self._make_backend_with_transport(handler)
        assert backend.delete_spec("sp_del") is True
