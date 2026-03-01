# Taskit Backend

Django REST API for task orchestration with full mutation history tracking.

---


## Prerequisites and Dependencies

Use Python 3.10+.

Key dependencies used by this backend:

- Django 5.1.4
- Django REST Framework 3.15.2
- psycopg2-binary 2.9.10
- django-cors-headers 4.6.0
- python-dotenv 1.0.1
- celery[redis] 5.4.0 (optional, for DAG executor)
- redis 5.2.1 (optional, for DAG executor)

> Redis is only required when using Celery DAG execution (`ODIN_EXECUTION_STRATEGY=celery_dag`). Skip this section otherwise.

### Install and run Redis (by OS)

**Linux (Ubuntu / Debian)**

```bash
sudo apt update
sudo apt install -y redis-server
sudo systemctl start redis
redis-cli ping
```

**Linux (Fedora / RHEL)**

```bash
sudo dnf install -y redis
sudo systemctl start redis
redis-cli ping
```

**macOS**

```bash
brew install redis
brew services start redis
redis-cli ping
```

**Windows**

Use WSL (Ubuntu), then inside WSL:

```bash
# One-time: install WSL and Ubuntu (from PowerShell)
wsl --install

# Inside WSL Ubuntu:
sudo apt update
sudo apt install -y redis-server
sudo service redis-server start
redis-cli ping
```

---

## Step-by-Step Setup (Unified Flow)

All commands below assume you are in `taskit/taskit-backend`.

### 1. Create or activate virtual environment

```bash
cd taskit/taskit-backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

If `.venv` already exists, only run the activation command.

### 2. Install backend dependencies

```bash
pip install -r requirements.txt
```

### 3. Choose database system (before migrations)

Choose one mode:

- **SQLite (recommended for quick local setup)**: easiest, no PostgreSQL required
- **Local PostgreSQL**: use your own PostgreSQL installation
- **Dockerized PostgreSQL**: use PostgreSQL container if not installed locally

### 4. Configure `.env` based on the selected database

Start from:

```bash
cp .env.example .env
```

#### Option A: SQLite (recommended)

Keep `USE_SQLITE=True` (default).

#### Option B: Local PostgreSQL

Set in `.env`:

- `USE_SQLITE=False`
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` matching your PostgreSQL

Bootstrap DB:

```bash
python scripts/create_db.py
# Or with custom credentials (must match .env):
python scripts/create_db.py --user postgres --password yourpassword
```

#### Option C: Dockerized PostgreSQL

Set in `.env`:

- `USE_SQLITE=False`
- `DB_PORT=5432` (or `5433` if 5432 is already occupied)

If you use `5433`, set Docker mapping in `docker-compose.postgres.yml` to `5433:5432`.

Bootstrap DB:

```bash
docker compose -f docker-compose.postgres.yml up -d
python scripts/create_db.py --user postgres --password admin123
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Seed agent models (recommended)

Populate/update agent users and model catalogs from `data/agent_models.json`:

```bash
python manage.py seedmodels
```

Optional dry run:

```bash
python manage.py seedmodels --dry-run
```

### 7. Create admin user (before starting server)

If `AUTH_ENABLED=True`, create an admin before starting the API server:

```bash
python manage.py createadmin --name "Admin" --email admin@test.com --password test123
```

If auth is disabled, you can skip this step.

---

## Run the Services

### Terminal 1: run backend API

```bash
python manage.py runserver 0.0.0.0:8000
```

### Terminal 2: run worker (only for Celery DAG mode)

Recommended for orchestrated execution: set `.env` to:

```dotenv
ODIN_EXECUTION_STRATEGY=celery_dag
```

If you use `ODIN_EXECUTION_STRATEGY=local`, skip this terminal.

Run worker (combined worker + beat in one process):

```bash
celery -A config worker --beat --loglevel=info --concurrency=4 --pool=threads
```

`--pool=threads` is required because `execute_single_task` blocks on subprocess I/O —
the default `solo` pool serializes everything and prevents parallel task execution.
`--concurrency=4` should be at least `DAG_EXECUTOR_MAX_CONCURRENCY + 1` (default 3+1)
to allow concurrent task executions plus the poll/reflection scheduler.

Execution state flow:

```text
BACKLOG → TODO → IN_PROGRESS → EXECUTING → REVIEW → DONE
                      ↑              ↓
                      └── FAILED ←───┘
```

---

## User Management

```bash
# Without auth enabled
python manage.py createuser --name "Alice" --email alice@test.com

# With auth enabled (--password required when AUTH_ENABLED=True)
python manage.py createuser --name "Alice" --email alice@test.com --password secret123
python manage.py createadmin --name "Admin" --email admin@test.com --password test123

python manage.py listusers
```

---

## Authentication (Optional)

Taskit auth is off by default. To enable:

1. Set in `.env`:

```dotenv
AUTH_ENABLED=True
JWT_ACCESS_SECONDS=900
JWT_REFRESH_SECONDS=604800
AUTH_COOKIE_SECURE=False
AUTH_COOKIE_SAMESITE=Lax
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

2. Create your first admin user:

```bash
python manage.py createadmin --name "Admin" --email admin@test.com --password test123
```

Admin users can create/update/delete other users via the API. Non-admin users have read-only access to the user list.

---

## Environment Variables (Simple Reference)

Copy `.env.example` to `.env`, then set values relevant to your mode.

### Database

| Variable | What to set | Why |
|---|---|---|
| `USE_SQLITE` | `True` or `False` | `True` uses SQLite, `False` uses PostgreSQL |
| `DB_HOST` | `localhost` or DB host | PostgreSQL host |
| `DB_PORT` | `5432` (or `5433`) | PostgreSQL port |
| `DB_NAME` | `taskit` | PostgreSQL database name |
| `DB_USER` | your DB user | PostgreSQL user |
| `DB_PASSWORD` | your DB password | PostgreSQL password |

### Django

| Variable | What to set | Why |
|---|---|---|
| `PORT` | `8000` or custom | App port for deployments/scripts |
| `SECRET_KEY` | random secret | Django security key |
| `DEBUG` | `True` or `False` | Debug mode |

> **Note:** Local development in this README uses `python manage.py runserver 0.0.0.0:8000`.

### Authentication (optional)

| Variable | What to set | Why |
|---|---|---|
| `AUTH_ENABLED` | `True` or `False` | Enable auth for API requests |
| `AUTH_LEGACY_FIREBASE_FLAG_COMPAT` | `True` or `False` | Backward compatibility with older `FIREBASE_AUTH_ENABLED` env |
| `JWT_ACCESS_SECONDS` | `900` (or custom) | Access token lifetime |
| `JWT_REFRESH_SECONDS` | `604800` (or custom) | Refresh token lifetime |
| `AUTH_COOKIE_SECURE` | `True` or `False` | Secure flag on refresh cookie |
| `AUTH_COOKIE_SAMESITE` | `Lax`/`Strict`/`None` | SameSite policy for refresh cookie |
| `CORS_ALLOWED_ORIGINS` | comma-separated origins | Required for browser auth with credentials |

### Odin execution

Use when backend should trigger Odin execution.

| Variable | What to set | Why |
|---|---|---|
| `ODIN_EXECUTION_STRATEGY` | `local` or `celery_dag` | Execution mode |
| `ODIN_CLI_PATH` | `odin` or full binary path | Backend command to invoke Odin |
| `ODIN_WORKING_DIR` | path containing `.odin/` | Fallback working directory for Odin |

Example:

```dotenv
ODIN_EXECUTION_STRATEGY=local
ODIN_CLI_PATH=/home/you/venv/bin/odin
ODIN_WORKING_DIR=/home/you/Harness-Kit/main/taskit
```

### Celery (optional, for `celery_dag`)

| Variable | What to set | Why |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Message broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Task result backend |
| `DAG_EXECUTOR_MAX_CONCURRENCY` | `3` (or custom) | Max parallel task execution |
| `DAG_EXECUTOR_POLL_INTERVAL` | `5` (seconds) | Polling interval for runnable tasks |

---

## Tests

Uses in-memory SQLite (no PostgreSQL needed).

```bash
# Run all tests
USE_SQLITE=True AUTH_ENABLED=False python manage.py test tests -v2

# Run only CRUD tests
USE_SQLITE=True AUTH_ENABLED=False python manage.py test tests.test_crud -v2

# Run only complex flow tests
USE_SQLITE=True AUTH_ENABLED=False python manage.py test tests.test_flows -v2

# Run DAG executor tests
USE_SQLITE=True AUTH_ENABLED=False python manage.py test tests.test_dag_executor -v2

# Run a single test
USE_SQLITE=True AUTH_ENABLED=False python manage.py test tests.test_flows.TestOdinIntegrationFlow -v2
```

**`test_crud`**: CRUD coverage for User/Member, Board, Label, Task, Spec, API aliases, and Health endpoints.

**`test_flows`**: Complex flows including task lifecycle progression, history audit trails, execution strategy triggers, auto-board-membership, label management, spec cloning, board clear, and a full end-to-end Odin integration simulation.

**`test_dag_executor`**: DAG executor behavior including dependency satisfaction, failed dependency detection, poll-and-execute transitions, concurrency limits, and success/failure/timeout handling.

---

## Architecture

Single Django project (`config/`) with one app (`tasks/`). JWT auth is optional (toggle with `AUTH_ENABLED`), and browser sessions use HttpOnly refresh cookies.

### Models (`tasks/models.py`)

- **User**: name, email, role (`HUMAN`/`AGENT`/`ADMIN`)
- **Board**: name, description
- **Label**: name, color
- **Task**: title, description, priority, status, assignee (FK to User), labels (M2M), board (FK), dev_eta_seconds, start_date, due_date
- **TaskHistory**: field_name, old_value, new_value, changed_by (email), changed_at

### Serializers (`tasks/serializers.py`)

Separate read and write serializers:

- **Read**: `TaskSerializer` returns nested assignee and labels objects
- **Write**: `CreateTaskSerializer`, `UpdateTaskSerializer`, `AssignTaskSerializer` accept flat IDs

### Views (`tasks/views.py`)

DRF ViewSets with custom `@action` endpoints for assign, unassign, label management, and history retrieval. History tracking is explicit in view methods (not Django signals): every mutation creates `TaskHistory` records with old/new values.

### Key Domain Rules

- `created_by` and `updated_by` are email strings, not foreign keys
- Task creation records a history entry with `field_name: "created"`
- Priority: `LOW`, `MEDIUM` (default), `HIGH`, `CRITICAL`
- Status: `BACKLOG`, `TODO` (default), `IN_PROGRESS`, `EXECUTING`, `REVIEW`, `TESTING`, `DONE`, `FAILED`

---

## API Reference

Root endpoints (`/users/`, `/boards/`, etc.) remain available.
`/api/*` aliases are also available (`/api/tasks/`, `/api/members/`, `/api/specs/`, `/api/boards/`, `/api/timeline/`, `/api/kanban/`).
`/dashboard/` has been removed.

See [`docs/API.md`](docs/API.md) for the full reference.

| Resource | Endpoints |
|---|---|
| Health | `GET /health/` |
| Users | CRUD at `/users/` |
| Boards | CRUD at `/boards/` (detail includes nested tasks) |
| Labels | CRUD at `/labels/` |
| Tasks | CRUD at `/tasks/` + `assign/`, `unassign/`, `labels/`, `history/` actions |

---

## Docker PostgreSQL Troubleshooting

- **Port 5432 already in use**: change `ports` in `docker-compose.postgres.yml` to `5433:5432`, then set `DB_PORT=5433` in `.env`.
- **Container stays unhealthy**: check logs with `docker compose -f docker-compose.postgres.yml logs postgres`.
- **Auth errors from Django**: ensure `.env` credentials match both `docker-compose.postgres.yml` and `scripts/create_db.py` command.
- **Migrations fail after schema drift**: reset local data with `docker compose -f docker-compose.postgres.yml down -v`, then run bootstrap and migrate again.
