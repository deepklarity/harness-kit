#!/bin/sh
# scripts/provision_frontend_deps.sh
#
# Make the frontend test suite runnable inside a task worktree by sharing
# ONE node_modules install across every worktree via a symlink.
#
# Task worktrees are born without node_modules (the path is gitignored), so
# the frontend gate used to die env-only ("vitest: not found") and
# promote-check reported ENV_MISSING — a fake critical-suite hold on every
# clean task.  This script removes that condition: the first frontend gate
# installs node_modules once into a shared cache under the main repo's
# .odin/.cache; every worktree (and every later gate run) symlinks to it.
#
# Why shared+symlink over per-worktree `npm ci`:
#   - per-worktree npm ci costs ~22s + ~1GB PER worktree (disk-infeasible
#     with parallel worktrees on a small disk; see task-200 proof);
#   - the shared install is paid once, then each worktree is an instant
#     symlink — and vitest tolerates a symlinked node_modules (verified:
#     163/163 PASS through the link).
#
# Lazy by design: invoked only from the frontend gate (scripts/verify.sh),
# so worktree CREATION time for non-frontend tasks is unaffected.
#
# Env:
#   FRONTEND_DIR        absolute path to the frontend dir to provision
#                       (default: taskit/taskit-frontend relative to cwd).
#   FRONTEND_INSTALL_CMD  override the install command (default:
#                       `npm ci --no-audit --no-fund`); used by tests to
#                       fake the install without real npm/network.
#
# Exit 0 once $FRONTEND_DIR/node_modules is usable (real dir or working
# symlink); non-zero if a fresh install was required and it failed (no
# partial symlink is left behind for the suite to trip on).

set -u

FE="${FRONTEND_DIR:-taskit/taskit-frontend}"
FE_NM="$FE/node_modules"
INSTALL_CMD="${FRONTEND_INSTALL_CMD:-npm ci --no-audit --no-fund}"

# Already usable (real dir, real file, or working symlink)?  Nothing to do —
# covers the common steady state (symlink already present) and the rare
# hand-installed dir.  `-e` follows symlinks, so a broken link falls through
# to re-provisioning below.
if [ -e "$FE_NM" ]; then
    exit 0
fi

# Locate the MAIN checkout (the shared cache lives there, not in the
# worktree, so it survives worktree cleanup and is reused by siblings).
# In a worktree, git's common dir is <main>/.git; its parent is the main
# root.  In the main repo itself this resolves to cwd.  --path-format=absolute
# needs git >= 2.31.
common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=""
if [ -n "$common" ] && [ -d "$common" ]; then
    MAIN=$(dirname "$common")
else
    MAIN=$(pwd)
fi

CACHE_DIR="$MAIN/.odin/.cache/frontend-node-modules"
SHARED_NM="$CACHE_DIR/node_modules"
LOCK_DIR="$MAIN/.odin/locks"
LOCK="$LOCK_DIR/frontend-deps.lock"

# Ensure the shared install exists.  A mkdir lock serializes parallel
# worktrees that hit provisioning at the same time, so `npm ci` runs once,
# not N times.
if [ ! -d "$SHARED_NM" ]; then
    mkdir -p "$LOCK_DIR" 2>/dev/null
    got_lock=0
    tries=0
    while :; do
        if mkdir "$LOCK" 2>/dev/null; then
            got_lock=1
            break
        fi
        # Another process finished the install while we waited — no lock
        # needed, and we must NOT touch the lock (the holder owns it).
        [ -d "$SHARED_NM" ] && break
        tries=$((tries + 1))
        if [ "$tries" -gt 600 ]; then
            echo "provision_frontend_deps: timed out waiting for concurrent install" >&2
            exit 1
        fi
        sleep 1
    done

    if [ "$got_lock" = "1" ]; then
        # Re-check under lock: a predecessor may have finished between our
        # outer check and acquiring the lock.
        if [ ! -d "$SHARED_NM" ]; then
            mkdir -p "$CACHE_DIR"
            # The install is driven by the frontend's own lockfile, so copy
            # the package manifests into the cache dir and run there.
            cp "$FE/package.json" "$FE/package-lock.json" "$CACHE_DIR"/ 2>/dev/null
            ( cd "$CACHE_DIR" && $INSTALL_CMD ) </dev/null >&2
            irc=$?
            if [ "$irc" -ne 0 ] || [ ! -d "$SHARED_NM" ]; then
                # Failed install must not leave a half-built cache: the next
                # gate run would see the dir and skip the install, then
                # symlink to an empty tree and report a misleading FAIL.
                rm -rf "$SHARED_NM"
                rmdir "$LOCK" 2>/dev/null
                echo "provision_frontend_deps: install failed (rc=$irc)" >&2
                exit "$irc"
            fi
        fi
        rmdir "$LOCK" 2>/dev/null
    fi
fi

# Link the shared install into this worktree's frontend dir.  By here FE_NM
# is either missing or a broken symlink (a real dir returned at the top), so
# `rm -rf` only ever clears a stale link — never a hand-installed tree.
rm -rf "$FE_NM" 2>/dev/null
mkdir -p "$FE"
ln -s "$SHARED_NM" "$FE_NM"
