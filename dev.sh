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
    # Kill-and-requeue policy (user decision): task sandboxes must not
    # outlive their supervisor. Orphans risk double execution, zombie
    # writes, and unbudgeted memory; the executor requeues their tasks.
    # (Only reaches task VMs — the odin-agents/odinbuild images are files,
    # not processes, and are untouched.)
    pkill -f "microsandbox run" 2>/dev/null || true
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

# --- Provision (idempotent — shared with install.sh so the first-run
#     story is the same in both paths; see scripts/lib/provision.sh) ---
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/provision.sh"
provision_environment
mkdir -p "$LOG_DIR"

find_first_dir() {
    for path in "$@"; do
        expanded="${path/#\~/$HOME}"
        if [ -d "$expanded" ]; then
            printf '%s\n' "$expanded"
            return 0
        fi
    done
    return 1
}

ensure_forkd_tap_if_available() {
    if [ "${FORKD_SETUP_TAP:-auto}" = "0" ]; then
        return 0
    fi

    local scripts_dir="${FORKD_SCRIPTS_DIR:-}"
    if [ -z "$scripts_dir" ]; then
        scripts_dir="$(find_first_dir ~/forkd-poc/forkd/scripts /usr/local/share/forkd/scripts /opt/forkd/scripts 2>/dev/null || true)"
    fi
    if [ -z "$scripts_dir" ] || [ ! -f "$scripts_dir/host-tap.sh" ]; then
        return 0
    fi

    local tap="${FORKD_TAP:-forkd-tap0}"
    if ip link show "$tap" >/dev/null 2>&1; then
        return 0
    fi

    log "Creating forkd tap device ($tap)..."
    sudo bash "$scripts_dir/host-tap.sh"
}

ensure_forkd_tap_if_available

# Forkd browser snapshot provisioning — setup-time only, never per task.
# Set FORKD_PROVISION_BROWSER=0 to skip on machines that do not use forkd/chrome-devtools.
if [ "${FORKD_PROVISION_BROWSER:-auto}" != "0" ] && [ -x "$ROOT_DIR/scripts/provision_forkd_browser.sh" ]; then
    log "Checking forkd browser snapshot..."
    if ! "$ROOT_DIR/scripts/provision_forkd_browser.sh" --if-configured; then
        warn "forkd browser snapshot provisioning failed."
        warn "Stopping dev startup so chrome-devtools proof tasks do not fail later in the UI."
        warn "Set FORKD_PROVISION_BROWSER=0 only if you intentionally want to run without forkd browser proof."
        exit 1
    fi
fi

find_first_executable() {
    for path in "$@"; do
        expanded="${path/#\~/$HOME}"
        if [ -x "$expanded" ]; then
            printf '%s\n' "$expanded"
            return 0
        fi
    done
    return 1
}

start_forkd_controller_if_available() {
    if [ "${FORKD_START_CONTROLLER:-auto}" = "0" ]; then
        return 0
    fi

    local controller="${FORKD_CONTROLLER_BIN:-}"
    if [ -z "$controller" ]; then
        controller="$(command -v forkd-controller 2>/dev/null || true)"
    fi
    if [ -z "$controller" ]; then
        controller="$(find_first_executable ~/forkd-poc/bin/forkd-controller ~/bin/forkd-controller 2>/dev/null || true)"
    fi
    if [ -z "$controller" ]; then
        if [ "${FORKD_START_CONTROLLER:-auto}" = "1" ]; then
            echo "forkd-controller not found" >&2
            exit 1
        fi
        return 0
    fi

    local controller_url="${FORKD_CONTROLLER_URL:-http://127.0.0.1:8889}"
    local browser_tag="${FORKD_BROWSER_SNAPSHOT_TAG:-odin-node22-4g-cli-browser}"
    if curl -fsS "$controller_url/v1/snapshots" >/dev/null 2>&1; then
        if [ "${FORKD_PROVISION_BROWSER:-auto}" != "0" ] && [ -d "/var/lib/forkd/snapshots/$browser_tag" ] && ! curl -fsS "$controller_url/v1/snapshots/$browser_tag/info" >/dev/null 2>&1; then
            warn "forkd-controller is already running but does not know browser snapshot $browser_tag."
            warn "Stop the old forkd-controller and rerun ./dev.sh so the refreshed /var/lib/forkd/state.json is loaded."
            exit 1
        fi
        info "forkd-controller already running at $controller_url"
        return 0
    fi

    local bind_addr="${FORKD_CONTROLLER_BIND:-127.0.0.1:8889}"
    local snapshot_root="${FORKD_SNAPSHOT_ROOT:-/var/lib/forkd/snapshots}"
    local audit_log="${FORKD_AUDIT_LOG:-/tmp/odin-forkd-controller-audit.log}"
    log "Starting forkd-controller at $controller_url..."
    sudo -E "$controller" serve \
        --bind "$bind_addr" \
        --snapshot-root "$snapshot_root" \
        --audit-log "$audit_log" \
        > "$LOG_DIR/forkd-controller.log" 2>&1 &
    PIDS+=($!)

    for _ in $(seq 1 40); do
        if curl -fsS "$controller_url/v1/snapshots" >/dev/null 2>&1; then
            info "forkd-controller ready"
            return 0
        fi
        sleep 0.25
    done

    warn "forkd-controller did not become ready."
    warn "See $LOG_DIR/forkd-controller.log"
    tail -40 "$LOG_DIR/forkd-controller.log" 2>/dev/null || true
    exit 1
}

start_forkd_controller_if_available


# --- Start ---

# Auto-set CORS and API URL so frontend/backend connect on non-default ports
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-http://localhost:$FRONTEND_PORT}"
export VITE_HARNESS_TIME_API_URL="${VITE_HARNESS_TIME_API_URL:-http://localhost:$BACKEND_PORT}"
export VITE_INSTANCE="${INSTANCE}"
# Dedicated merge queue (must be set before backend/celery import settings):
# a merge is a 10s git job; on the shared pool it waits behind ~26-min
# sandbox executions (observed 12-min merge latency). settings.py routes
# merge_task_on_reflection to this queue; a threads worker serves it below.
export MERGE_QUEUE_NAME="${MERGE_QUEUE_NAME:-merges}"
# Executor concurrency: 4 slots fit since per-agent VM RAM dropped to
# 2048-3072 MiB (profiled; 3x3072+1x2048 + 6GB OS reserve < 18GB host).
export DAG_EXECUTOR_MAX_CONCURRENCY="${DAG_EXECUTOR_MAX_CONCURRENCY:-4}"
# macOS can't read /proc/meminfo, so the sandbox RAM budget is unbounded
# unless set explicitly. 12 GiB leaves ~6 for OS + backend + celery + UI on
# this 18 GiB host. VMs are 2-3 GiB each (see .odin/config.yaml), but the
# budget accounts the 3 GiB honest cap per spawn.
export SANDBOX_MEMORY_BUDGET_MIB="${SANDBOX_MEMORY_BUDGET_MIB:-12288}"
export SANDBOX_DEFAULT_VM_MEM_MIB="${SANDBOX_DEFAULT_VM_MEM_MIB:-3072}"
# Dedicated reflection queue: a reflection is part of a flow, not a
# competing task — reviews must never wait behind the 3-slot execution
# pool. settings.py routes execute_reflection to this queue.
export REFLECTION_QUEUE_NAME="${REFLECTION_QUEUE_NAME:-reflections}"
if [ "$INSTANCE" = "dev" ]; then
    export ODIN_CLI_PATH="${ODIN_CLI_PATH:-odin-dev}"
fi

# Backend HTTP server. SERVE_MODE selects between Django's dev runserver and a
# production ASGI server:
#   - dev  (default): manage.py runserver  — autoreload, dev-only, drops
#                     connections under sustained concurrent load.
#   - prod          : gunicorn with an uvicorn worker if gunicorn is installed
#                     (canonical Django Channels production setup, graceful
#                     reload on SIGHUP), falling back to plain uvicorn otherwise.
#                     Both are ASGI, so channels/websockets keep working exactly
#                     as they do under daphne's runserver.
# The always-on loop runs on SQLite + InMemory channel layer
# (config/settings.py CHANNEL_LAYERS), which is single-process only, so the
# worker default is 1. The single uvicorn worker still serves many concurrent
# HTTP requests via its async event loop + threadpool (fixes the connection
# drops). Raise BACKEND_WORKERS only after moving to Postgres + a Redis channel
# layer (USE_SQLITE=False).
start_backend() {
    local workers="${BACKEND_WORKERS:-1}"
    if [ "${SERVE_MODE:-dev}" = "prod" ]; then
        if command -v gunicorn >/dev/null 2>&1; then
            log "Starting backend (gunicorn + uvicorn worker, workers=$workers)..."
            (cd "$BACKEND_DIR" && gunicorn config.asgi:application \
                -k uvicorn.workers.UvicornWorker \
                -w "$workers" \
                --bind "0.0.0.0:${BACKEND_PORT}" \
                --access-logfile - --error-logfile - \
                --log-level info) > "$LOG_DIR/backend.log" 2>&1 &
        else
            log "Starting backend (uvicorn, workers=$workers; gunicorn not installed)..."
            (cd "$BACKEND_DIR" && uvicorn config.asgi:application \
                --workers "$workers" \
                --host 0.0.0.0 --port "$BACKEND_PORT" \
                --log-level info) > "$LOG_DIR/backend.log" 2>&1 &
        fi
    else
        log "Starting backend (runserver)..."
        python "$BACKEND_DIR/manage.py" runserver "0.0.0.0:${BACKEND_PORT}" > "$LOG_DIR/backend.log" 2>&1 &
    fi
    PIDS+=($!)
}

start_backend

(cd "$FRONTEND_DIR" && npm run dev -- --port $FRONTEND_PORT) > "$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)

# Kill stale celery workers from previous runs (crashed terminals, forgotten tabs, etc.)
pkill -f "celery.*worker" 2>/dev/null && sleep 1 || true

(cd "$BACKEND_DIR" && celery -A config worker --beat --loglevel=info --concurrency=3 --pool=prefork) > "$LOG_DIR/celery.log" 2>&1 &
PIDS+=($!)

# Worker for the dedicated merge queue (MERGE_QUEUE_NAME exported above).
(cd "$BACKEND_DIR" && celery -A config worker -Q "$MERGE_QUEUE_NAME" --loglevel=info --concurrency=2 --pool=threads -n merges@%h) > "$LOG_DIR/celery-merges.log" 2>&1 &
PIDS+=($!)

# Worker for the dedicated reflection queue (REFLECTION_QUEUE_NAME exported above).
(cd "$BACKEND_DIR" && celery -A config worker -Q "$REFLECTION_QUEUE_NAME" --loglevel=info --concurrency=2 --pool=threads -n reflections@%h) > "$LOG_DIR/celery-reflections.log" 2>&1 &
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
