#!/usr/bin/env bash
# Shared provisioning for the kit: virtualenv, backend deps, odin editable
# install, frontend deps, SQLite migrations, and the agent seed.
#
# Sourced by install.sh (stranger's path) and dev.sh (developer path) so the
# first-run story is identical and there is exactly one place that knows how
# to bring the kit's dependencies up. Every step is idempotent: a repeat run
# is a fast no-op once the world matches its stamps.
#
# Caller MUST define before calling provision_environment:
#   ROOT_DIR      repo root
#   BACKEND_DIR   $ROOT_DIR/taskit/taskit-backend
#   FRONTEND_DIR  $ROOT_DIR/taskit/taskit-frontend
#   ODIN_DIR      $ROOT_DIR/odin
#   log()         one-arg logging function (already defined by the caller)
#
# Side effect: activates $ROOT_DIR/.venv in the caller's shell so subsequent
# `python`/`odin`/`pip` calls resolve to the venv.
# Returns non-zero only on a hard odin-install failure.

provision_environment() {
    # --- virtualenv (created once; reused on every subsequent run) ---
    if [ ! -f "$ROOT_DIR/.venv/bin/activate" ]; then
        log "Creating virtual environment..."
        python3 -m venv "$ROOT_DIR/.venv"
    fi
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.venv/bin/activate"

    # --- backend deps (reinstalled only when requirements.txt changes) ---
    REQ_HASH=$(md5 -q "$BACKEND_DIR/requirements.txt" 2>/dev/null \
        || md5sum "$BACKEND_DIR/requirements.txt" | cut -d' ' -f1)
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
            return 1
        }
    fi

    # --- frontend deps (reinstalled only when package.json changes) ---
    PKG_HASH=$(md5 -q "$FRONTEND_DIR/package.json" 2>/dev/null \
        || md5sum "$FRONTEND_DIR/package.json" | cut -d' ' -f1)
    PKG_STAMP="$FRONTEND_DIR/node_modules/.package-hash"
    if [ ! -f "$PKG_STAMP" ] || [ "$(cat "$PKG_STAMP")" != "$PKG_HASH" ]; then
        log "Installing frontend deps..."
        (cd "$FRONTEND_DIR" && npm install --silent)
        echo "$PKG_HASH" > "$PKG_STAMP"
    fi

    # --- migrations — always run (fast no-op when nothing changed) ---
    log "Checking migrations..."
    python "$BACKEND_DIR/manage.py" migrate --run-syncdb --verbosity 0

    # --- seed agent users (idempotent — merges, never duplicates) ---
    python "$BACKEND_DIR/manage.py" seedmodels --verbosity 0 > /dev/null 2>&1 || true

    # --- broker scratch dirs the celery filebroker transport expects ---
    mkdir -p "$BACKEND_DIR/.celery/out" "$BACKEND_DIR/.celery/processed" "$BACKEND_DIR/.celery/results"
}
