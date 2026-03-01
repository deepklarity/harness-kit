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

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dev]${NC} $*"; }
info() { echo -e "${BLUE}[dev]${NC} $*"; }

PIDS=()
cleanup() {
    echo ""
    log "Shutting down..."
    for pid in "${PIDS[@]}"; do
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

if [ ! -d "$ROOT_DIR/.venv" ]; then
    log "Creating virtual environment..."
    python3 -m venv "$ROOT_DIR/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"

if ! command -v odin &>/dev/null; then
    log "Installing odin..."
    pip install -e "$ODIN_DIR" --quiet
fi

if ! python -c "import rest_framework" 2>/dev/null; then
    log "Installing backend deps..."
    pip install -r "$BACKEND_DIR/requirements.txt" --quiet
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "Installing frontend deps..."
    (cd "$FRONTEND_DIR" && npm install --silent)
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

python "$BACKEND_DIR/manage.py" runserver 0.0.0.0:8000 > "$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

(cd "$FRONTEND_DIR" && npm run dev) > "$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)

(cd "$BACKEND_DIR" && celery -A config worker --beat --loglevel=info --concurrency=3 --pool=prefork) > "$LOG_DIR/celery.log" 2>&1 &
PIDS+=($!)

BOLD='\033[1m'
echo ""
echo -e "${BOLD}${GREEN}  → Open http://localhost:5173${NC}"
echo ""
info "API running on localhost:8000"
info "Ctrl-C to stop  |  Trouble? tail -f .dev-logs/backend.log"
wait
