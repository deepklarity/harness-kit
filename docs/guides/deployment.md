# Deployment Guide

This guide covers deploying the harness-kit stack: TaskIt backend, TaskIt frontend, and Odin orchestrator.

## Environments

| Component | Local Dev | Staging / Production |
|-----------|-----------|---------------------|
| taskit-backend | SQLite, `runserver` | PostgreSQL, gunicorn + nginx |
| taskit-frontend | Vite dev server | Static build (`npm run build`) + CDN/nginx |
| odin | Direct CLI | Same — runs on the machine where agents are available |
| Redis | Optional | Required if using Celery DAG executor |

## Local Development

See [README.md](../README.md#quick-start) for the full quick-start guide. The defaults are tuned for local dev:

- `USE_SQLITE=True` — no PostgreSQL needed
- `FIREBASE_AUTH_ENABLED=False` — no Firebase project needed
- `DEBUG=True` — detailed error pages

If you need PostgreSQL locally but do not have it installed, TaskIt backend includes a Dockerized fallback:

Edit your existing `.env` (single env file):

- Set `USE_SQLITE=False`
- Keep `DB_PORT=5432` when Docker can use host port 5432
- Set `DB_PORT=5433` when local PostgreSQL already uses 5432

If using 5433, set Docker port mapping in `docker-compose.postgres.yml` to `5433:5432`.

```bash
cd taskit/taskit-backend
docker compose -f docker-compose.postgres.yml up -d
python scripts/create_db.py --user postgres --password admin123
python manage.py migrate
```

Stop it when done:

```bash
docker compose -f docker-compose.postgres.yml down
```

Reset all local Postgres data:

```bash
docker compose -f docker-compose.postgres.yml down -v
```

## Production Deployment

### TaskIt Backend (Django)

#### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis 5+ (if using Celery)

#### Environment variables

Create `.env` from `.env.example` and configure:

```bash
# Database — use a real PostgreSQL instance
DB_HOST=your-db-host
DB_PORT=5432
DB_NAME=taskit
DB_USER=taskit_app
DB_PASSWORD=<strong-password>
USE_SQLITE=False

# Django
SECRET_KEY=<generate-a-random-secret>
DEBUG=False
PORT=8000

# Firebase (enable in production)
FIREBASE_AUTH_ENABLED=True
FIREBASE_CREDENTIALS_PATH=/path/to/firebase-sa.json
FIREBASE_API_KEY=<your-firebase-api-key>

# Odin execution (optional — enables auto-execution when tasks move to IN_PROGRESS)
# ODIN_EXECUTION_STRATEGY=local
# ODIN_CLI_PATH=/usr/local/bin/odin
# ODIN_WORKING_DIR=/path/to/project
```

#### Steps

```bash
cd taskit/taskit-backend

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Start with gunicorn
gunicorn taskit.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --timeout 120
```

#### With Celery (for DAG execution)

```bash
# Worker
celery -A taskit worker --loglevel=info

# Beat scheduler (optional — for periodic tasks)
celery -A taskit beat --loglevel=info
```

### TaskIt Frontend (React)

#### Build

```bash
cd taskit/taskit-frontend

# Set production API URL
echo "VITE_HARNESS_TIME_API_URL=https://api.your-domain.com" > .env

# If using Firebase auth
echo "VITE_FIREBASE_AUTH_ENABLED=true" >> .env
echo "VITE_FIREBASE_API_KEY=<key>" >> .env
echo "VITE_FIREBASE_AUTH_DOMAIN=<domain>" >> .env
echo "VITE_FIREBASE_PROJECT_ID=<project-id>" >> .env
# ... (see .env.example for all Firebase vars)

npm ci
npm run build
```

The build output is in `dist/`. Serve it with any static file server.

#### Nginx example

```nginx
server {
    listen 80;
    server_name your-domain.com;
    root /path/to/taskit-frontend/dist;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Odin CLI

Odin runs on the machine where AI agents (Claude Code, Codex, etc.) are available. It is a CLI tool, not a web service.

```bash
cd odin
pip install -e ".[mcp]"

# Configure agent credentials
cp .env.example .env
# Edit .env with TaskIt auth credentials

cp config/config.sample.yaml config/config.yaml
# Edit config.yaml with agent definitions
```

#### MCP Server (optional)

Odin includes a TaskIt MCP server for blocking question/reply workflows:

```bash
taskit-mcp  # starts the FastMCP server
```

### Harness Usage Status

Standalone CLI — no deployment needed. Install and run:

```bash
cd harness_usage_status
pip install -e .
cp config/.env.example config/.env
# Add API keys for providers you want to check

harness-usage-status           # show all provider quotas
harness-usage-status --provider claude  # single provider
```

## Database Setup

### PostgreSQL (production)

```bash
# Create database and user
psql -U postgres <<SQL
CREATE DATABASE taskit;
CREATE USER taskit_app WITH PASSWORD '<strong-password>';
GRANT ALL PRIVILEGES ON DATABASE taskit TO taskit_app;
ALTER USER taskit_app CREATEDB;  -- needed for test database creation
SQL

# Run migrations
cd taskit/taskit-backend
python manage.py migrate
```

### SQLite (development)

No setup needed. Set `USE_SQLITE=True` in `.env` and Django creates `db.sqlite3` automatically on first migrate.

## Firebase Setup (optional)

Firebase provides authentication. When disabled (`FIREBASE_AUTH_ENABLED=False`), the API is open — suitable for local development only.

1. Create a Firebase project at [console.firebase.google.com](https://console.firebase.google.com)
2. Enable Email/Password authentication
3. Download the service account key → save as `taskit-backend/firebase-sa.json`
4. Copy the Web API key and config values to both backend and frontend `.env` files
5. Set `FIREBASE_AUTH_ENABLED=True` (backend) and `VITE_FIREBASE_AUTH_ENABLED=true` (frontend)

## Health Checks

```bash
# Backend API
curl http://localhost:8000/api/boards/

# Frontend (dev server)
curl http://localhost:5173/

# Odin — verify installation
odin --help

# Usage status — verify installation
harness-usage-status --help
```

## Troubleshooting

### Backend won't start

- **ModuleNotFoundError**: Run `pip install -r requirements.txt` from `taskit/taskit-backend/`
- **Database connection refused**: Check `DB_HOST`, `DB_PORT` in `.env`. For dev, set `USE_SQLITE=True`
- **Docker Postgres port conflict**: If `5432` is busy, change `docker-compose.postgres.yml` port mapping to `5433:5432` and set `DB_PORT=5433` in `.env`
- **Docker Postgres unhealthy**: Inspect logs with `docker compose -f docker-compose.postgres.yml logs postgres`
- **Migration errors**: Run `python manage.py migrate` — the database schema may be out of date

### Frontend won't connect to API

- Check `VITE_HARNESS_TIME_API_URL` in `.env` — it must match where the backend is running
- CORS: The backend includes `django-cors-headers`; ensure your frontend origin is allowed

### Odin can't reach TaskIt

- Verify `ODIN_ADMIN_USER` and `ODIN_ADMIN_PASSWORD` in odin's `.env`
- If Firebase auth is enabled on TaskIt, ensure `ODIN_FIREBASE_API_KEY` is set
- Test with: `curl -X POST http://localhost:8000/api/boards/`

### Tests fail

```bash
# Backend — force SQLite and disable auth for tests
USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests -v2

# Frontend
npm run test:run

# Odin (integration tests require real agents, excluded by default)
cd odin && pytest
```
