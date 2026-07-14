#!/bin/sh
# Start harness-kit services env-hermetically for autonomous execution, in their
# own session so they survive the launching shell being killed.
#
# Scrubs Claude Code / Anthropic session env vars so spawned agent CLIs
# (claude -p, reflections) don't trip the nested-session guard or inherit
# a redirected API base (finding F9 in ../archive/OPERATIONS.md).
#
# ─── Why this script exists ──────────────────────────────────────────────────
# `./dev.sh` is an *interactive* supervisor: it starts backend/celery/frontend
# as its own background children and traps EXIT/INT/TERM to reap them. That is
# correct for a foreground terminal — and lethal for the always-on loop. When the
# shell that launched it died (closed terminal, killed ssh session, OOM-killed
# watcher), dev.sh's process group received the signal, its EXIT trap fired, and
# the whole stack (API + celery + UI) went down with it. Reaped-with-the-launcher
# outages happened repeatedly before this fix.
#
# This script launches the SAME dev.sh (same provisioning, same server binaries,
# same auth posture) but moved into its own Unix session via `setsid` (Linux) or
# `nohup` (macOS fallback). The supervisor and every service it spawns land in a
# fresh session/process group that the launcher's terminal has no link to, so a
# hangup or kill of the launcher cannot reach them. The supervisor pid is recorded
# in `.dev-logs/services.pid`; `status` and `stop` are driven off it.
#
# ─── Usage ───────────────────────────────────────────────────────────────────
#   sh docs/fable_roadmap/bootstrap/start_services.sh              # start, prod (default)
#   sh docs/fable_roadmap/bootstrap/start_services.sh start [prod|dev]
#   sh docs/fable_roadmap/bootstrap/start_services.sh status        # idempotent health check
#   sh docs/fable_roadmap/bootstrap/start_services.sh stop          # stop the whole stack
#   sh docs/fable_roadmap/bootstrap/start_services.sh restart [prod|dev]
#   sh docs/fable_roadmap/bootstrap/start_services.sh prod          # back-compat: mode as arg = start
#   START_MODE=dev sh docs/fable_roadmap/bootstrap/start_services.sh
#
# `start` is idempotent: if the supervisor in .dev-logs/services.pid is alive and
# the backend port is listening, it reports "already running" and exits 0.
#
# ─── Modes ───────────────────────────────────────────────────────────────────
# prod  (DEFAULT)  Backend on a production ASGI server: gunicorn with an uvicorn
#                  worker when gunicorn is installed (canonical Django Channels
#                  production setup; graceful reload on SIGHUP), otherwise plain
#                  uvicorn. Both are ASGI, so channels/websockets keep working
#                  exactly as under daphne's runserver. The async event loop +
#                  threadpool serve many concurrent requests from one process,
#                  fixing the connection-drops that made odin retry and stall the
#                  always-on loop under the dev runserver. (Merged in W3.18.)
#
# dev              Backend on Django's dev runserver (autoreload). Dev-only; fine
#                  for interactive poking, but runserver drops HTTP connections
#                  under sustained concurrent load. Kept as an opt-in.
#
# ─── Dev-mode auth semantics (preserved in BOTH modes) ──────────────────────
# The always-on loop relies on the board API being open in dev mode. These are
# the process env defaults already emitted below / inherited from settings.py:
#   AUTH_ENABLED=False        -> DRF uses no authentication classes
#                                (config/settings.py:29, tasks/middleware.py)
#   DEBUG=True                -> dev error pages, ALLOWED_HOSTS=["*"]
#   USE_SQLITE=True           -> SQLite + InMemory channel layer
# This script does NOT enable AUTH_ENABLED, does NOT set DEBUG=False, and does
# NOT switch the DB. The production server is a transport change only — the auth
# posture is identical to `./dev.sh`. (Harden these for a real multi-user
# deployment; that is out of scope for the autonomous loop.)
#
# ─── Tunables (env, all optional) ───────────────────────────────────────────
#   BACKEND_WORKERS   uvicorn/gunicorn worker count (default 1; see CHANNEL_LAYERS
#                     single-process constraint). Raise only on Postgres + Redis.
#   BACKEND_PORT / FRONTEND_PORT / INSTANCE — passed through to dev.sh.
#
# ─── Rollback ───────────────────────────────────────────────────────────────
# 1. Stop the detached stack:
#        sh docs/fable_roadmap/bootstrap/start_services.sh stop
# 2. Then run either path — both are unchanged by this feature:
#      - detached again:  sh docs/fable_roadmap/bootstrap/start_services.sh
#      - foreground:      ./dev.sh        # interactive supervisor, Ctrl-C to stop
# No state to clean up: detached and foreground share the same DB, ports, and env
# vars. The only difference this script adds is the Unix session in which the
# supervisor runs. Removing `.dev-logs/services.pid` (if stale) is always safe; it
# is recreated on the next `start`.
#
# To drop the production server path entirely: `pip uninstall gunicorn` and revert
# the gunicorn line in taskit/taskit-backend/requirements.txt — plain uvicorn
# (already present) remains a working production ASGI server without it, and
# `START_MODE=dev` falls all the way back to the Django runserver.

set -u

SCRIPT_DIR=$(dirname "$0")
ROOT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
LOG_DIR="$ROOT_DIR/.dev-logs"
PID_FILE="$LOG_DIR/services.pid"
BOOT_LOG="$LOG_DIR/services-boot.log"
BACKEND_PORT="${BACKEND_PORT:-9100}"
FRONTEND_PORT="${FRONTEND_PORT:-9200}"

mkdir -p "$LOG_DIR"

# ─── arg parsing: subcommand first, mode anywhere ───────────────────────────
ACTION=start
MODE="${START_MODE:-prod}"
for arg in "$@"; do
    case "$arg" in
        start|status|stop|restart) ACTION="$arg" ;;
        prod|production) MODE=prod ;;
        dev|development) MODE=dev ;;
        -h|--help) ACTION=help ;;
        *) echo "start_services.sh: unknown argument '$arg' (use start|status|stop|restart [prod|dev])" >&2; exit 2 ;;
    esac
done

[ "$BACKEND_PORT" = "$FRONTEND_PORT" ] && FRONTEND_PORT=9200

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { printf '%b[svc]%b %s\n' "$GREEN" "$NC" "$*"; }
info() { printf '%b[svc]%b %s\n' "$BLUE"  "$NC" "$*"; }
warn() { printf '%b[svc]%b %s\n' "$YELLOW" "$NC" "$*" >&2; }
err()  { printf '%b[svc]%b %s\n' "$RED"   "$NC" "$*" >&2; }

# Pick the strongest detachment primitive available. setsid (Linux) puts the
# supervisor in its own session+process group — the launcher's terminal cannot
# signal it at all. macOS has no setsid by default; nohup ignores SIGHUP (the
# usual terminal-close signal) and is the portable fallback.
if command -v setsid >/dev/null 2>&1; then
    LAUNCHER=setsid
else
    LAUNCHER=nohup
fi

# supervisor_alive -> echoes pid (and returns 0) iff the pidfile points at a live
# process; cleans a stale pidfile and returns 1 otherwise.
supervisor_pid() {
    [ -f "$PID_FILE" ] || return 1
    pid=$(cat "$PID_FILE" 2>/dev/null)
    [ -n "$pid" ] || { rm -f "$PID_FILE"; return 1; }
    if kill -0 "$pid" 2>/dev/null; then
        printf '%s' "$pid"
        return 0
    fi
    rm -f "$PID_FILE"
    return 1
}

# port_up <port> -> 0 if something is accepting TCP connections on localhost.
# Tries IPv4 then IPv6: the backend (gunicorn/uvicorn) binds 0.0.0.0 (IPv4), but
# vite binds "localhost" which on many hosts resolves to ::1 (IPv6 only) — checking
# only one stack makes status falsely report a healthy UI as DOWN.
port_up() {
    python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
for fam, addr in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
    s = socket.socket(fam)
    s.settimeout(0.5)
    try:
        s.connect(addr)
        sys.exit(0)
    except Exception:
        pass
    finally:
        s.close()
sys.exit(1)
PY
}

celery_up() { pgrep -f "celery.*-A config worker" >/dev/null 2>&1; }

print_status() {
    pid=$(supervisor_pid || true)
    if [ -n "$pid" ]; then
        info "supervisor alive (pid $pid) — session $pid, mode managed by dev.sh"
    else
        warn "supervisor NOT running (no live pid in $PID_FILE)"
    fi
    if port_up "$BACKEND_PORT"; then
        info "backend     UP  http://localhost:$BACKEND_PORT"
    else
        warn "backend     DOWN (port $BACKEND_PORT not listening)"
    fi
    if port_up "$FRONTEND_PORT"; then
        info "frontend    UP  http://localhost:$FRONTEND_PORT"
    else
        warn "frontend    DOWN (port $FRONTEND_PORT not listening)"
    fi
    if celery_up; then
        info "celery      UP  $(pgrep -f 'celery.*-A config worker' | tr '\n' ' ')"
    else
        warn "celery      DOWN (no 'celery -A config worker' process)"
    fi
}

do_status() {
    print_status
    pid=$(supervisor_pid || true)
    [ -z "$pid" ] && return 1
    port_up "$BACKEND_PORT" || return 1
    return 0
}

do_start() {
    if pid=$(supervisor_pid); then
        log "already running (supervisor pid $pid); use 'status' for detail, 'stop' to halt."
        return 0
    fi
    if port_up "$BACKEND_PORT"; then
        warn "port $BACKEND_PORT already in use but no supervisor pidfile — another instance (e.g. ./dev.sh) may be running. Stop it first."
        return 1
    fi

    case "$MODE" in
        prod|dev) ;;
        *) err "unknown mode '$MODE' (use 'prod' or 'dev')"; exit 2 ;;
    esac

    # Scrub Claude/Anthropic session env so spawned CLIs don't trip the
    # nested-session guard (same logic as the pre-detach version of this script).
    UNSETS=$(env | awk -F= '/^(CLAUDE|ANTHROPIC|claude|anthropic)/ {printf "-u %s ", $1}')
    ODIN_CLI_PATH="${ODIN_CLI_PATH:-$(command -v odin 2>/dev/null || true)}"

    log "starting in $MODE mode (detached via $LAUNCHER); first run may take ~60s to provision..."
    log "boot log: $BOOT_LOG"

    # The inner sh writes ITS OWN pid (== dev.sh's pid after exec) to the pidfile,
    # then replaces itself with dev.sh under the scrubbed env. Because $LAUNCHER is
    # setsid/nohup, this whole tree lands in a new session and inherits no
    # controlling tty — the launcher shell's death cannot propagate here.
    launch_cmd="echo \$\$ > '$PID_FILE'; exec env $UNSETS SERVE_MODE='$MODE' ODIN_EXECUTION_STRATEGY=celery_dag ODIN_CLI_PATH='$ODIN_CLI_PATH' '$ROOT_DIR/dev.sh'"
    $LAUNCHER sh -c "$launch_cmd" </dev/null >>"$BOOT_LOG" 2>&1 &
    disowned_pid=$!

    # Wait for the pidfile (the inner sh writes it immediately on a clean start).
    i=0
    while [ ! -s "$PID_FILE" ] && [ $i -lt 50 ]; do
        # If the setsid/nohup wrapper itself already exited and no pidfile, fail fast.
        if [ "$LAUNCHER" = nohup ] && ! kill -0 "$disowned_pid" 2>/dev/null && [ ! -s "$PID_FILE" ]; then
            err "supervisor failed to start; see $BOOT_LOG"; tail -20 "$BOOT_LOG" 2>/dev/null >&2; return 1
        fi
        sleep 0.2; i=$((i+1))
    done
    if [ ! -s "$PID_FILE" ]; then
        err "pidfile never appeared; see $BOOT_LOG"; tail -20 "$BOOT_LOG" 2>/dev/null >&2; return 1
    fi
    pid=$(cat "$PID_FILE")

    # Wait for the backend to actually accept connections (covers first-run
    # provisioning: venv, deps, migrate, seed). The detached supervisor keeps
    # running regardless of whether this script is killed mid-wait.
    log "waiting for backend on :$BACKEND_PORT (supervisor pid $pid)..."
    i=0
    while [ $i -lt 240 ]; do
        kill -0 "$pid" 2>/dev/null || { err "supervisor pid $pid exited during startup; see $BOOT_LOG"; tail -30 "$BOOT_LOG" 2>/dev/null >&2; return 1; }
        port_up "$BACKEND_PORT" && break
        sleep 0.5; i=$((i+1))
        [ $((i % 20)) -eq 0 ] && printf '.'
    done
    echo

    if port_up "$BACKEND_PORT"; then
        log "ready — backend :$BACKEND_PORT, frontend :$FRONTEND_PORT, supervisor pid $pid"
        log "kill the launching shell anytime; services keep running. Stop with: $0 stop"
        return 0
    fi
    err "backend did not come up within 120s; see $BOOT_LOG"
    tail -30 "$BOOT_LOG" 2>/dev/null >&2
    return 1
}

do_stop() {
    pid=$(supervisor_pid || true)
    if [ -z "$pid" ]; then
        log "no live supervisor; reaping any orphaned services..."
    else
        # The supervisor is a session+group leader (setsid) or at least the parent
        # (nohup); signal the whole process group so dev.sh AND every service it
        # spawned (backend workers, celery prefork pool, vite) receive TERM.
        log "stopping supervisor pid $pid (whole process group)..."
        kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true

        i=0
        while kill -0 "$pid" 2>/dev/null && [ $i -lt 40 ]; do
            sleep 0.25; i=$((i+1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            warn "still alive after 10s, sending KILL..."
            kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
            sleep 0.5
        fi
        rm -f "$PID_FILE"
    fi

    # Always reap any worker that escaped the group — celery prefork children and
    # the esbuild service vite spawns both ignore/miss TERM and survive a
    # supervisor kill, reparenting to init. This also unblocks dev.sh's `wait` on
    # a normal stop, so the supervisor exits promptly. Scoped to OUR service
    # invocations so unrelated processes are never touched. Runs whether or not a
    # live supervisor was found, so orphaned children get cleaned up too.
    pkill -TERM -f "celery.*-A config worker" 2>/dev/null || true
    pkill -TERM -f "config.asgi:application" 2>/dev/null || true
    pkill -TERM -f "manage.py runserver" 2>/dev/null || true
    pkill -TERM -f "vite --port $FRONTEND_PORT" 2>/dev/null || true
    sleep 1
    # Anything still alive after TERM gets KILL (esbuild will need this).
    pkill -KILL -f "celery.*-A config worker" 2>/dev/null || true
    pkill -KILL -f "config.asgi:application" 2>/dev/null || true

    sleep 0.5
    if port_up "$BACKEND_PORT" || celery_up; then
        warn "a service is still responding after stop; check .dev-logs/*.log"
        return 1
    fi
    log "stopped."
    return 0
}

case "$ACTION" in
    start)   do_start ;;
    status)  do_status ;;
    stop)    do_stop ;;
    restart) do_stop && do_start ;;
    help)
        sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
        ;;
esac
