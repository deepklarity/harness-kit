#!/usr/bin/env bash
set -euo pipefail

# dev.sh — clone → ./dev.sh → working app
#
# First run (~60s): venv, deps, migrate, seed agents
# After that (~3s): just starts services
#
# Ctrl-C stops everything.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/taskit/taskit-backend"
FRONTEND_DIR="$ROOT_DIR/taskit/taskit-frontend"
ODIN_DIR="$ROOT_DIR/odin"
LOG_DIR="$ROOT_DIR/.dev-logs"

INSTANCE="${INSTANCE:-}"

# Dev instance auto-offsets ports by 1 to avoid collisions with stable
if [ "$INSTANCE" = "dev" ]; then
    BACKEND_PORT="${BACKEND_PORT:-9101}"
    FRONTEND_PORT="${FRONTEND_PORT:-9201}"
else
    BACKEND_PORT="${BACKEND_PORT:-9100}"
    FRONTEND_PORT="${FRONTEND_PORT:-9200}"
fi

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dev]${NC} $*"; }
info() { echo -e "${BLUE}[dev]${NC} $*"; }

PIDS=()
cleanup() {
    echo ""
    log "Shutting down..."
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    log "Done."
}
trap cleanup EXIT INT TERM

# --- Check for .env files that override zero-config defaults ---
YELLOW='\033[1;33m'
warn() { echo -e "${YELLOW}[dev]${NC} $*"; }

env_conflicts=0
for envfile in "$BACKEND_DIR/.env" "$FRONTEND_DIR/.env"; do
    if [ -f "$envfile" ]; then
        warn "Found $(basename "$(dirname "$envfile")")/.env — its settings override dev.sh defaults."
        env_conflicts=1
    fi
done
if [ "$env_conflicts" -eq 1 ]; then
    warn "Remove .env files for zero-config dev, or keep them for custom config."
    echo ""
fi

# --- Provision (idempotent, each step skips if already done) ---

if [ ! -f "$ROOT_DIR/.venv/bin/activate" ]; then
    log "Creating virtual environment..."
    python3 -m venv "$ROOT_DIR/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"

REQ_HASH=$(md5 -q "$BACKEND_DIR/requirements.txt" 2>/dev/null || md5sum "$BACKEND_DIR/requirements.txt" | cut -d' ' -f1)
REQ_STAMP="$ROOT_DIR/.venv/.requirements-hash"
if [ ! -f "$REQ_STAMP" ] || [ "$(cat "$REQ_STAMP")" != "$REQ_HASH" ]; then
    log "Installing backend deps..."
    pip install -r "$BACKEND_DIR/requirements.txt" --quiet
    echo "$REQ_HASH" > "$REQ_STAMP"
fi

# odin must install AFTER backend deps — installing requirements.txt first
# ensures shared dependencies (httpx, pydantic, etc.) are resolved before
# odin's editable install layers on top without conflicts.
if ! python -c "from odin.worktree import WorktreeManager" 2>/dev/null; then
    log "Installing odin..."
    pip install -e "$ODIN_DIR" --quiet
    # Verify — fail fast if install didn't work
    python -c "from odin.worktree import WorktreeManager" || {
        echo "ERROR: odin install failed. Run: pip install -e $ODIN_DIR"
        exit 1
    }
fi

PKG_HASH=$(md5 -q "$FRONTEND_DIR/package.json" 2>/dev/null || md5sum "$FRONTEND_DIR/package.json" | cut -d' ' -f1)
PKG_STAMP="$FRONTEND_DIR/node_modules/.package-hash"
if [ ! -f "$PKG_STAMP" ] || [ "$(cat "$PKG_STAMP")" != "$PKG_HASH" ]; then
    log "Installing frontend deps..."
    (cd "$FRONTEND_DIR" && npm install --silent)
    echo "$PKG_HASH" > "$PKG_STAMP"
fi

# Migrations — always run (fast no-op when nothing changed)
log "Checking migrations..."
python "$BACKEND_DIR/manage.py" migrate --run-syncdb --verbosity 0

# Seed agent users (idempotent — merges, never duplicates)
python "$BACKEND_DIR/manage.py" seedmodels --verbosity 0 > /dev/null 2>&1 || true

# Broker dirs
mkdir -p "$BACKEND_DIR/.celery/out" "$BACKEND_DIR/.celery/processed" "$BACKEND_DIR/.celery/results"
mkdir -p "$LOG_DIR"

# --- Start ---

# Auto-set CORS and API URL so frontend/backend connect on non-default ports
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-http://localhost:$FRONTEND_PORT}"
export VITE_HARNESS_TIME_API_URL="${VITE_HARNESS_TIME_API_URL:-http://localhost:$BACKEND_PORT}"
export VITE_INSTANCE="${INSTANCE}"
if [ "$INSTANCE" = "dev" ]; then
    export ODIN_CLI_PATH="${ODIN_CLI_PATH:-odin-dev}"
fi

python "$BACKEND_DIR/manage.py" runserver 0.0.0.0:$BACKEND_PORT > "$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

(cd "$FRONTEND_DIR" && npm run dev -- --port $FRONTEND_PORT) > "$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)

# Kill stale celery workers from previous runs (crashed terminals, forgotten tabs, etc.)
pkill -f "celery.*worker" 2>/dev/null && sleep 1 || true

(cd "$BACKEND_DIR" && celery -A config worker --beat --loglevel=info --concurrency=3 --pool=prefork) > "$LOG_DIR/celery.log" 2>&1 &
PIDS+=($!)

BOLD='\033[1m'
AMBER='\033[0;33m'
echo ""
if [ -n "$INSTANCE" ]; then
    echo -e "${BOLD}${AMBER}  → [$INSTANCE] http://localhost:$FRONTEND_PORT${NC}"
else
    echo -e "${BOLD}${GREEN}  → Open http://localhost:$FRONTEND_PORT${NC}"
fi
echo ""
info "API running on localhost:$BACKEND_PORT"
[ -n "$INSTANCE" ] && info "Instance: $INSTANCE (amber theme, badge, wrench favicon)"
info "Ctrl-C to stop  |  Trouble? tail -f .dev-logs/backend.log"
wait
