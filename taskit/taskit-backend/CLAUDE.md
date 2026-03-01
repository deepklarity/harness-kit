# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Harness Time is a Trello-like task management REST API built with Django + Django REST Framework, backed by PostgreSQL.

## Architecture

Single Django project (`config/`) with one app (`tasks/`). Firebase authentication (optional, toggle via `FIREBASE_AUTH_ENABLED` env var). PostgreSQL required.

- **Models** (`tasks/models.py`): User (`firebase_uid`, `is_admin`, `color`, `role`, `must_change_password`, `available_models`), Board (`is_trial`), BoardMembership (M2M join), Label (`color`), Task (`spec` FK, `depends_on`, `complexity`, `metadata`, `model_name`), TaskComment (append-only, with `comment_type` and `CommentAttachment`), TaskHistory, Spec (`odin_id`, `title`, `source`, `content`, `abandoned`, `metadata`), SpecComment, ReflectionReport (`verdict`, `analysis_sections`, `token_usage`, `reviewer_agent/model`). Integer auto-increment PKs. Tasks have M2M to Labels, FK to Board, optional FK to User (assignee) and Spec.
- **Serializers** (`tasks/serializers.py`): Separate read/write serializers. `TaskSerializer` for reads (nested assignee + labels), `CreateTaskSerializer`/`UpdateTaskSerializer` for writes.
- **Views** (`tasks/views.py`): DRF ViewSets. TaskViewSet uses `@action` for assign/unassign/labels/history. History tracking is explicit in view methods.
- **Auth** (`tasks/auth_views.py`, `tasks/middleware.py`, `tasks/authentication.py`, `tasks/permissions.py`): Django JWT auth (access token in header, refresh token via httponly cookie). `TaskitAuthMiddleware` validates Bearer tokens. `TaskitJWTAuthentication` DRF auth class. `IsAdminUser` permission. Controlled by `AUTH_ENABLED` env var (default: `False` for dev). Legacy `FIREBASE_AUTH_ENABLED` env var still accepted via compat flag.
- **Execution** (`tasks/execution/`): Pluggable execution strategy for running Odin tasks. `ExecutionStrategy` ABC in `base.py` with `trigger()`, `stop()`, `trigger_summarize()`. Implementations: `local.py` (subprocess), `celery_dag.py` (Celery DAG with parallel execution). Selected by `ODIN_EXECUTION_STRATEGY` env var (`"local"` or `"celery_dag"`; empty = disabled).
- **URLs** (`config/urls.py`): DRF DefaultRouter at root (`/users/`, `/boards/`, etc.) + auth endpoints at `/auth/`.

## Key Domain Rules

- Every task mutation creates TaskHistory records with old/new values and `changed_by` (email).
- Task creation records a history entry (`field_name: "created"`).
- `created_by` and `updated_by` are email strings, not foreign keys to User.
- TaskPriority: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` (default: `MEDIUM`).
- TaskStatus: `BACKLOG`, `TODO` (default), `IN_PROGRESS`, `EXECUTING`, `REVIEW`, `TESTING`, `DONE`, `FAILED`.
- `dev_eta_seconds` is nullable — estimated development time in seconds.

## API Endpoints

See `docs/API.md` for full reference.

CRUD for: `/users/`, `/boards/`, `/tasks/`, `/labels/`

Task-specific actions:
- `POST /tasks/:id/assign/` — assign to user
- `POST /tasks/:id/unassign/` — remove assignee
- `POST /tasks/:id/labels/` — add labels
- `DELETE /tasks/:id/labels/` — remove labels
- `GET /tasks/:id/history/` — get mutation history
- `GET /tasks/:id/comments/` — list comments
- `POST /tasks/:id/comments/` — add comment (`author_email`, `content`, optional `author_label`, `attachments`)
- `GET /tasks/?board_id=<id>` — filter tasks by board

Auth endpoints (when `AUTH_ENABLED=True`):
- `POST /auth/login/` — login with email + password, returns JWT access token + refresh cookie
- `POST /auth/register/` — register new user
- `POST /auth/refresh/` — rotate access token using refresh cookie
- `GET /auth/me/` — current user info (session restore)
- `POST /auth/logout/` — blacklist refresh token
- `POST /auth/change-password/` — change password (requires old password)

## Logging Rules

- **Always log full tracebacks**, never just `str(e)`. Use `logger.exception()` in except blocks or `logger.error(..., exc_info=True)`. Tracebacks are essential for debugging — `str(e)` loses the stack trace and makes issues impossible to diagnose.
- Use `logger.warning(..., exc_info=True)` when the error is handled gracefully but the traceback is still useful for debugging.

## Environment Variables

```
DB_HOST=localhost    DB_PORT=5432    DB_USER=postgres
DB_PASSWORD=postgres DB_NAME=harness_time DB_SSLMODE=disable
PORT=8080
AUTH_ENABLED=False                       # Master auth switch (default: disabled for dev)
JWT_ACCESS_SECONDS=900                   # Access token lifetime (15 min)
JWT_REFRESH_SECONDS=604800               # Refresh token lifetime (7 days)
CORS_ALLOWED_ORIGINS=http://localhost:5173

# Odin execution
ODIN_EXECUTION_STRATEGY=                 # "local" or "celery_dag" (empty = disabled)
ODIN_CLI_PATH=odin                       # Path to odin CLI binary
ODIN_WORKING_DIR=                        # Default working dir for odin exec

# Celery + DAG executor (required when ODIN_EXECUTION_STRATEGY=celery_dag)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
DAG_EXECUTOR_MAX_CONCURRENCY=3           # Max simultaneous task executions
DAG_EXECUTOR_POLL_INTERVAL=5             # Seconds between dependency checks
```

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env

python manage.py migrate
python manage.py runserver 0.0.0.0:8000

# Tests (uses in-memory SQLite, no PostgreSQL needed)
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests -v2
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests.test_crud -v2
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests.test_flows -v2
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests.test_comments -v2
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests.test_dag_executor -v2

# User management
python manage.py createuser --name "Alice" --email alice@test.com
python manage.py createadmin --email admin@example.com --password secret
python manage.py listusers
```

## Debugging Techniques

### Log tailing

```bash
tail -f logs/taskit.log         # Abbreviated, no tracebacks
tail -f logs/taskit_detail.log  # Full tracebacks
```

### Diagnostic scripts

**Always use these before reading source code or logs when debugging.** They use Django ORM directly (no auth), surface problems automatically, and show data in context. See root `CLAUDE.md` → "Data Inspection & Debugging" for the full decision table.

```bash
cd taskit/taskit-backend

# Default (all sections, truncated content)
python testing_tools/task_inspect.py <task_id>
python testing_tools/spec_trace.py <spec_id>
python testing_tools/board_overview.py [board_id]
python testing_tools/reflection_inspect.py <report_id>
python testing_tools/snapshot_extractor.py <spec_id> <output_dir>

# Output modes: --brief (1-3 lines), --full (everything), --json (structured)
# Section filtering: --sections basic,tokens,diagnosis
# Slim snapshots: --slim (exclude large text fields)
```
