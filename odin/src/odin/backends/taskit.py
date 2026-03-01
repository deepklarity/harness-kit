"""TaskIt REST API board backend."""

import time
from typing import Any, Dict, List, Optional

import httpx

from odin.backends.base import BoardBackend
from odin.backends.registry import register_backend
from odin.logging import setup_logger, TaskContextAdapter
from odin.specs import SpecArchive
from odin.taskit.models import Task, TaskStatus

logger = TaskContextAdapter(setup_logger("odin.taskit"))

# Re-login 5 minutes before token expiry
_TOKEN_REFRESH_MARGIN = 300


def _unwrap_list(data) -> list:
    """Unwrap a DRF paginated response to a plain list.

    DRF returns {"count": N, "results": [...]} for list endpoints.
    This helper handles both paginated dicts and plain lists.
    """
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def _raise_for_status(resp: httpx.Response) -> None:
    """Like resp.raise_for_status() but includes the response body in the error.

    Detects 401 responses and raises TaskItAuthError with actionable guidance
    instead of a raw HTTP error.
    """
    if resp.status_code == 401:
        raise TaskItAuthError(
            f"TaskIt returned 401 Unauthorized for {resp.request.url}\n"
            "The TaskIt backend requires authentication. To fix this:\n"
            "  1. Create an admin user:  python manage.py createadmin --email admin@test.com --password test123\n"
            "  2. Set env vars in your .env (in the directory where you run odin):\n"
            "       ODIN_ADMIN_USER=admin@test.com\n"
            "       ODIN_ADMIN_PASSWORD=test123\n"
            "  Or disable auth on the TaskIt backend: AUTH_ENABLED=False"
        )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = resp.text[:2000] if resp.text else "(empty body)"
        logger.error(
            "HTTP %s for %s — %s", resp.status_code, resp.request.url, body[:500],
            exc_info=True,
        )
        raise httpx.HTTPStatusError(
            f"{exc.response.status_code} {exc.response.reason_phrase} "
            f"for url '{exc.request.url}'\nResponse body: {body}",
            request=exc.request,
            response=exc.response,
        ) from None


class TaskItAuthError(Exception):
    """Raised when authentication with the TaskIt backend fails."""


class TaskItAuth(httpx.Auth):
    """httpx Auth handler that authenticates via TaskIt's /auth/login/ endpoint.

    Odin only needs email + password. The TaskIt backend authenticates
    internally and returns a Bearer token. Tokens are cached at the class level
    so multiple TaskItBackend instances sharing the same credentials (e.g.,
    CLI _resolve_id + Orchestrator) reuse a single login.
    """

    # Class-level token cache: (login_url, email) -> (token, expires_at)
    _token_cache: Dict[tuple, tuple] = {}

    def __init__(self, login_url: str, email: str, password: str):
        self._login_url = login_url
        self._email = email
        self._password = password
        self._cache_key = (login_url, email)

    def auth_flow(self, request: httpx.Request):
        """httpx Auth hook — inject Bearer token when auth is enabled."""
        token = self.get_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        else:
            request.headers.pop("Authorization", None)
            logger.debug(
                "TaskIt auth token is empty; sending request without Authorization header"
            )
        yield request

    def get_token(self) -> str:
        """Return a valid token, re-authenticating if expired or near-expiry."""
        cached = self._token_cache.get(self._cache_key)
        if cached:
            token, expires_at = cached
            now = time.time()
            if token and now < expires_at - _TOKEN_REFRESH_MARGIN:
                return token
            # Auth-disabled mode caches an empty token marker for a day.
            if not token and now < expires_at:
                return ""
        return self._login()

    def _login(self) -> str:
        """Authenticate with TaskIt's /auth/login/ endpoint."""
        logger.info("Authenticating with TaskIt at %s", self._login_url)
        try:
            resp = httpx.post(
                self._login_url,
                json={"email": self._email, "password": self._password},
                timeout=15,
            )
        except httpx.ConnectError:
            raise TaskItAuthError(
                f"Cannot connect to TaskIt at {self._login_url}. "
                "Is the TaskIt backend running?"
            )
        except httpx.RequestError as exc:
            raise TaskItAuthError(f"Login request failed: {exc}")

        if resp.status_code == 200:
            data = resp.json()
            # Auth disabled on backend — no token needed
            if "token" not in data:
                logger.info("TaskIt auth is disabled, proceeding without token")
                self._token_cache[self._cache_key] = ("", time.time() + 86400)
                return ""
            token = data["token"]
            expires_at = time.time() + data.get("expires_in", 3600)
            self._token_cache[self._cache_key] = (token, expires_at)
            logger.debug("Authenticated with TaskIt as %s", self._email)
            return token

        # Parse error detail
        try:
            detail = resp.json().get("detail", "Unknown error")
        except Exception:
            detail = resp.text[:300]

        if resp.status_code == 401:
            raise TaskItAuthError(
                f"Login failed for '{self._email}': {detail}\n"
                "Check ODIN_ADMIN_USER and ODIN_ADMIN_PASSWORD in your .env file."
            )
        raise TaskItAuthError(
            f"Login failed ({resp.status_code}): {detail}"
        )


@register_backend("taskit")
class TaskItBackend(BoardBackend):
    """Board backend that syncs tasks and specs to a TaskIt instance via REST API.

    Translation logic:
    - Status: .upper() sending to TaskIt, .lower() reading back
    - Agent -> User: agents are users with email "<agent>@odin.agent"
    - Task ID: Odin hex IDs -> POST to create, store returned PK. Numeric IDs -> PUT.
    - depends_on: int/str conversion between systems

    When admin_email and admin_password are provided, authenticates via
    TaskIt's /auth/login/ endpoint.
    """

    def __init__(
        self,
        base_url: str,
        board_id: int,
        created_by: str,
        admin_email: Optional[str] = None,
        admin_password: Optional[str] = None,
        **kwargs,
    ):
        self._base_url = base_url.rstrip("/")
        self._board_id = board_id
        self._created_by = created_by
        self._agent_user_cache: Dict[str, int] = {}
        # Maps odin spec IDs (e.g. "sp_abc123") to TaskIt integer PKs
        self._spec_pk_cache: Dict[str, int] = {}

        # Auth via TaskIt login endpoint (optional — when both are provided)
        auth: Optional[TaskItAuth] = None
        if admin_email and admin_password:
            login_url = f"{self._base_url}/auth/login/"
            auth = TaskItAuth(login_url, admin_email, admin_password)

        self._client = httpx.Client(base_url=self._base_url, timeout=30, auth=auth)
        self._trial_board_id: Optional[int] = None

    # -- Trial board --

    def _ensure_trial_board(self, name: str = "odin-trial") -> int:
        """Get or create the trial board. Returns the board ID."""
        if self._trial_board_id is not None:
            return self._trial_board_id

        resp = self._client.get("/boards/")
        _raise_for_status(resp)
        for board in _unwrap_list(resp.json()):
            if board.get("is_trial") and board["name"] == name:
                self._trial_board_id = board["id"]
                return self._trial_board_id

        # Create the trial board
        resp = self._client.post("/boards/", json={
            "name": name,
            "description": "Auto-created trial board for Odin test runs",
            "is_trial": True,
        })
        _raise_for_status(resp)
        self._trial_board_id = resp.json()["id"]
        return self._trial_board_id

    def use_trial_board(self, name: str = "odin-trial") -> int:
        """Switch this backend to use the trial board for all operations."""
        board_id = self._ensure_trial_board(name)
        self._board_id = board_id
        logger.info("Switched to trial board: id=%d, name=%s", board_id, name)
        return board_id

    # -- Health check --

    def ping(self) -> Dict[str, Any]:
        """Check connectivity to the TaskIt instance.

        Returns a dict with 'ok' (bool) and diagnostic info.
        """
        result: Dict[str, Any] = {"ok": False, "base_url": self._base_url, "board_id": self._board_id}
        try:
            resp = self._client.get("/boards/")
            _raise_for_status(resp)
            boards = _unwrap_list(resp.json())
            result["boards"] = len(boards)
            board_match = any(b["id"] == self._board_id for b in boards)
            result["board_exists"] = board_match

            resp = self._client.get("/tasks/", params={"board_id": self._board_id})
            _raise_for_status(resp)
            result["task_count"] = len(_unwrap_list(resp.json()))

            resp = self._client.get("/specs/", params={"board_id": self._board_id})
            _raise_for_status(resp)
            result["spec_count"] = len(_unwrap_list(resp.json()))

            result["ok"] = board_match
        except TaskItAuthError as e:
            result["error"] = str(e)
        except httpx.ConnectError:
            result["error"] = f"Cannot connect to {self._base_url}"
        except Exception as e:
            result["error"] = str(e)
        return result

    # -- Routing config --

    def fetch_routing_config(self) -> dict:
        """Fetch agent/model routing config from the TaskIt API.

        Returns the routing-config response: {"agents": [...]} where each
        agent has name, cost_tier, capabilities, default_model, premium_model,
        and a models list with per-model enabled/disabled state.
        """
        resp = self._client.get(f"/boards/{self._board_id}/routing-config/")
        _raise_for_status(resp)
        return resp.json()

    # -- Agent -> User resolution --

    def _resolve_agent_user(self, agent_name: str) -> int:
        """Resolve an agent name to a TaskIt user ID, creating if needed."""
        if agent_name in self._agent_user_cache:
            return self._agent_user_cache[agent_name]

        email = f"{agent_name}@odin.agent"
        resp = self._client.get("/users/", params={"search": email})
        _raise_for_status(resp)
        users = _unwrap_list(resp.json())

        # Search for exact email match
        for u in users:
            if u["email"] == email:
                self._agent_user_cache[agent_name] = u["id"]
                return u["id"]

        # Create the agent user
        resp = self._client.post("/users/", json={"name": agent_name, "email": email})
        _raise_for_status(resp)
        user_id = resp.json()["id"]
        self._agent_user_cache[agent_name] = user_id
        return user_id

    # -- Status translation --

    @staticmethod
    def _status_to_taskit(status: TaskStatus) -> str:
        return status.value.upper()

    @staticmethod
    def _status_from_taskit(status_str: str) -> TaskStatus:
        return TaskStatus(status_str.lower())

    # -- Task operations --

    def _get_all_items(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetch all items from a potentially paginated DRF list endpoint."""
        resp = self._client.get(path, params=params)
        _raise_for_status(resp)
        data = resp.json()
        if not (isinstance(data, dict) and "results" in data):
            return _unwrap_list(data)

        items: List[Dict[str, Any]] = list(data.get("results", []))
        next_url = data.get("next")
        while next_url:
            page_resp = self._client.get(next_url)
            _raise_for_status(page_resp)
            page_data = page_resp.json()
            if not (isinstance(page_data, dict) and "results" in page_data):
                items.extend(_unwrap_list(page_data))
                break
            items.extend(page_data.get("results", []))
            next_url = page_data.get("next")
        return items

    def _task_to_payload(self, task: Task) -> Dict[str, Any]:
        """Convert an Odin Task to a TaskIt API payload."""
        payload: Dict[str, Any] = {
            "board_id": self._board_id,
            "title": task.title,
            "description": task.description,
            "status": self._status_to_taskit(task.status),
            "depends_on": [str(d) for d in task.depends_on],
        }
        complexity = task.metadata.get("complexity")
        if complexity:
            payload["complexity"] = complexity
        priority = task.metadata.get("priority")
        if priority:
            payload["priority"] = priority
        dev_eta_seconds = task.metadata.get("dev_eta_seconds")
        if dev_eta_seconds is not None:
            payload["dev_eta_seconds"] = dev_eta_seconds
        payload["metadata"] = task.metadata
        # Link to spec if we have a taskit spec PK cached
        if task.spec_id and task.spec_id in self._spec_pk_cache:
            payload["spec_id"] = self._spec_pk_cache[task.spec_id]
        return payload

    def _task_from_response(self, data: Dict[str, Any]) -> Task:
        """Convert a TaskIt API response to an Odin Task."""
        # Extract agent name from assignee email
        agent = None
        assignee = data.get("assignee")
        if assignee and isinstance(assignee, dict):
            email = assignee.get("email", "")
            if email.endswith("@odin.agent"):
                agent = email.split("@")[0]

        depends_on = data.get("depends_on", [])
        if depends_on:
            depends_on = [str(d) for d in depends_on]

        metadata = data.get("metadata", {})
        metadata["taskit_id"] = data["id"]
        if data.get("complexity"):
            metadata["complexity"] = data["complexity"]
        # model_name (explicit DB field, set by UI) is authoritative over
        # metadata["selected_model"] (set at planning time and may be stale).
        if data.get("model_name"):
            metadata["selected_model"] = data["model_name"]

        return Task(
            id=str(data["id"]),
            title=data["title"],
            description=data.get("description", ""),
            status=self._status_from_taskit(data["status"]),
            assigned_agent=agent,
            spec_id=str(data["spec_id"]) if data.get("spec_id") else None,
            depends_on=depends_on,
            metadata=metadata,
        )

    def create_task(self, task: Task) -> Task:
        """Create a new task via POST. Returns the task with backend-assigned ID."""
        payload = self._task_to_payload(task)
        payload["created_by"] = self._created_by

        # Assign agent user if present
        if task.assigned_agent:
            user_id = self._resolve_agent_user(task.assigned_agent)
            payload["assignee_id"] = user_id

        resp = self._client.post("/tasks/", json=payload)
        _raise_for_status(resp)
        data = resp.json()

        # Update task with the returned PK
        task.id = str(data["id"])
        task.metadata["taskit_id"] = data["id"]
        logger.info(
            "Task created on TaskIt: taskit_id=%s, title=%s",
            data["id"], task.title,
        )
        return task

    def update_task(self, task: Task) -> Task:
        """Update an existing task via PUT."""
        payload: Dict[str, Any] = {
            "updated_by": self._created_by,
            "title": task.title,
            "description": task.description,
            "status": self._status_to_taskit(task.status),
            "depends_on": [str(d) for d in task.depends_on],
        }
        complexity = task.metadata.get("complexity")
        if complexity:
            payload["complexity"] = complexity
        priority = task.metadata.get("priority")
        if priority:
            payload["priority"] = priority
        dev_eta_seconds = task.metadata.get("dev_eta_seconds")
        if dev_eta_seconds is not None:
            payload["dev_eta_seconds"] = dev_eta_seconds
        payload["metadata"] = task.metadata
        if task.assigned_agent:
            user_id = self._resolve_agent_user(task.assigned_agent)
            payload["assignee_id"] = user_id

        resp = self._client.put(f"/tasks/{task.id}/", json=payload)
        _raise_for_status(resp)
        logger.debug(
            "Task updated on TaskIt: taskit_id=%s, status=%s",
            task.id, task.status.value,
        )
        return task

    def load_task(self, task_id: str) -> Optional[Task]:
        resp = self._client.get(f"/tasks/{task_id}/")
        if resp.status_code == 404:
            return None
        _raise_for_status(resp)
        return self._task_from_response(resp.json())

    def load_all_tasks(self) -> List[Task]:
        data = self._get_all_items("/tasks/", params={"board_id": self._board_id})
        return [self._task_from_response(d) for d in data]

    def delete_task(self, task_id: str) -> bool:
        resp = self._client.delete(f"/tasks/{task_id}/")
        return resp.status_code == 204

    def add_comment(
        self,
        task_id: str,
        author_email: str,
        content: str,
        author_label: str = "",
        attachments: list | None = None,
        comment_type: str | None = None,
    ) -> None:
        """POST a comment to /tasks/:id/comments/."""
        payload = {
            "author_email": author_email,
            "author_label": author_label,
            "content": content,
        }
        if attachments:
            payload["attachments"] = attachments
        if comment_type:
            payload["comment_type"] = comment_type
        resp = self._client.post(f"/tasks/{task_id}/comments/", json=payload)
        _raise_for_status(resp)
        logger.debug(
            "Comment posted: taskit_id=%s, author=%s, length=%d",
            task_id, author_email, len(content),
        )

    def get_comments(self, task_id: str) -> list:
        """GET /tasks/:id/comments/ — fetch all comments for a task."""
        resp = self._client.get(f"/tasks/{task_id}/comments/")
        _raise_for_status(resp)
        return _unwrap_list(resp.json())

    def record_execution_result(
        self,
        task_id: str,
        execution_result: Dict[str, Any],
        status: str,
        actor_email: str,
    ) -> None:
        """POST execution result to /tasks/:id/execution_result/."""
        payload = {
            "execution_result": execution_result,
            "status": status,
            "updated_by": actor_email,
        }
        resp = self._client.post(f"/tasks/{task_id}/execution_result/", json=payload)
        _raise_for_status(resp)
        logger.info(
            "Execution result recorded: taskit_id=%s, success=%s, status=%s",
            task_id, execution_result.get("success"), status,
        )

    def _resolve_spec_pk(self, odin_id: str) -> Optional[int]:
        """Resolve an odin spec ID to a TaskIt integer PK."""
        if odin_id in self._spec_pk_cache:
            return self._spec_pk_cache[odin_id]
        resp = self._client.get("/specs/", params={"odin_id": odin_id})
        _raise_for_status(resp)
        results = _unwrap_list(resp.json())
        if not results:
            return None
        pk = results[0]["id"]
        self._spec_pk_cache[odin_id] = pk
        return pk

    def record_planning_result(
        self,
        spec_id: str,
        raw_output: str,
        duration_ms: float,
        agent: str,
        model: str,
        effective_input: str,
        success: bool,
    ) -> None:
        """POST planning trace to /specs/:id/planning_result/."""
        taskit_pk = self._resolve_spec_pk(spec_id)
        if taskit_pk is None:
            logger.warning("Cannot post planning result: spec %s not found", spec_id)
            return
        payload = {
            "raw_output": raw_output,
            "duration_ms": duration_ms,
            "agent": agent,
            "model": model,
            "effective_input": effective_input,
            "success": success,
        }
        resp = self._client.post(f"/specs/{taskit_pk}/planning_result/", json=payload)
        _raise_for_status(resp)
        logger.info(
            "Planning result recorded: spec=%s, success=%s, duration_ms=%s",
            spec_id, success, duration_ms,
        )

    def update_spec_metadata(
        self,
        spec_id: str,
        metadata_patch: dict,
    ) -> None:
        """Merge additional keys into a spec's metadata via PATCH."""
        taskit_pk = self._resolve_spec_pk(spec_id)
        if taskit_pk is None:
            logger.warning("Cannot update spec metadata: spec %s not found", spec_id)
            return
        # Fetch current metadata
        resp = self._client.get(f"/specs/{taskit_pk}/")
        _raise_for_status(resp)
        current_meta = resp.json().get("metadata", {}) or {}
        current_meta.update(metadata_patch)
        resp = self._client.patch(
            f"/specs/{taskit_pk}/",
            json={"metadata": current_meta},
        )
        _raise_for_status(resp)

    # -- Spec operations --

    def save_spec(self, spec: SpecArchive) -> SpecArchive:
        """Persist a spec. Looks up by odin_id, then POST or PUT."""
        # Check if spec already exists in TaskIt
        resp = self._client.get("/specs/", params={"odin_id": spec.id})
        _raise_for_status(resp)
        existing = _unwrap_list(resp.json())

        if existing:
            # Update existing spec
            taskit_id = existing[0]["id"]
            self._spec_pk_cache[spec.id] = taskit_id
            payload = {
                "title": spec.title,
                "abandoned": spec.abandoned,
                "metadata": spec.metadata,
                "updated_by": self._created_by,
            }
            resp = self._client.put(f"/specs/{taskit_id}/", json=payload)
            _raise_for_status(resp)
        else:
            # Create new spec
            payload = {
                "odin_id": spec.id,
                "title": spec.title,
                "source": spec.source,
                "content": spec.content,
                "board_id": self._board_id,
                "metadata": spec.metadata,
            }
            resp = self._client.post("/specs/", json=payload)
            _raise_for_status(resp)
            data = resp.json()
            self._spec_pk_cache[spec.id] = data["id"]

        return spec

    def load_spec(self, spec_id: str) -> Optional[SpecArchive]:
        """Load a spec by odin_id."""
        resp = self._client.get("/specs/", params={"odin_id": spec_id})
        _raise_for_status(resp)
        results = _unwrap_list(resp.json())
        if not results:
            return None
        data = results[0]
        return SpecArchive(
            id=data["odin_id"],
            title=data["title"],
            source=data.get("source", "inline"),
            content=data.get("content", ""),
            abandoned=data.get("abandoned", False),
            metadata=data.get("metadata", {}),
        )

    def load_all_specs(self) -> List[SpecArchive]:
        data = self._get_all_items("/specs/", params={"board_id": self._board_id})
        specs = []
        for item in data:
            specs.append(SpecArchive(
                id=item["odin_id"],
                title=item["title"],
                source=item.get("source", "inline"),
                content=item.get("content", ""),
                abandoned=item.get("abandoned", False),
                metadata=item.get("metadata", {}),
            ))
        return specs

    def set_spec_abandoned(self, spec_id: str) -> bool:
        resp = self._client.get("/specs/", params={"odin_id": spec_id})
        _raise_for_status(resp)
        results = _unwrap_list(resp.json())
        if not results:
            return False
        taskit_id = results[0]["id"]
        resp = self._client.put(
            f"/specs/{taskit_id}/",
            json={"abandoned": True, "updated_by": self._created_by},
        )
        _raise_for_status(resp)
        return True

    def delete_spec(self, spec_id: str) -> bool:
        """Delete a spec by ID. Returns True if deleted."""
        resp = self._client.get("/specs/", params={"odin_id": spec_id})
        _raise_for_status(resp)
        results = _unwrap_list(resp.json())
        if not results:
            return False
        taskit_id = results[0]["id"]
        resp = self._client.delete(f"/specs/{taskit_id}/")
        return resp.status_code == 204

    # -- Label operations --

    def list_labels(self) -> List[dict]:
        resp = self._client.get("/labels/")
        _raise_for_status(resp)
        return _unwrap_list(resp.json())

    def create_label(self, name: str, color: str) -> dict:
        resp = self._client.post("/labels/", json={"name": name, "color": color})
        _raise_for_status(resp)
        return resp.json()  # POST returns a single object, not a list
