#!/bin/sh
# scripts/verify.sh — single entrypoint to run all four test suites.
#
# Usage:
#   scripts/verify.sh                  # run every suite
#   scripts/verify.sh odin snapshots   # run a subset
#
# Suites: odin, backend, frontend, snapshots
#
# Exit code is 0 only when every selected suite passes. A non-zero exit means
# at least one suite is red — no skips, no masks, no xfails. Test counts are
# parsed from each tool's own output and printed verbatim in the summary table.
#
# Run logs are kept under .verify-logs/ (overwritten each run).

set -u

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ODIN_DIR="$ROOT_DIR/odin"
BACKEND_DIR="$ROOT_DIR/taskit/taskit-backend"
FRONTEND_DIR="$ROOT_DIR/taskit/taskit-frontend"
LOG_DIR="$ROOT_DIR/.verify-logs"
RESULTS="$LOG_DIR/_results.txt"

# Pin the odin package to THIS tree. odin is pipx/venv-installed EDITABLE
# against the main checkout; without this, backend tests in a branch
# worktree silently import the main checkout's odin and surface phantom
# TypeErrors (e.g. MergeResult kwargs) when the worktree's odin/src
# diverges. This is the single chokepoint: the W5.12 spec-branch runner
# invokes exactly this script, so pinning here covers both the operator
# path and the post-merge verify path. Prepend (not replace) so a foreign
# odin/src already on PYTHONPATH from the parent process loses, not wins.
ODIN_SRC_DIR="$ROOT_DIR/odin/src"
if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$ODIN_SRC_DIR:$PYTHONPATH"
else
    PYTHONPATH="$ODIN_SRC_DIR"
fi
export PYTHONPATH

mkdir -p "$LOG_DIR"
: > "$RESULTS"

# Pick a Python. Prefer the repo venv if it exists, else python3 on PATH.
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PY="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY=$(command -v python3)
elif command -v python >/dev/null 2>&1; then
    PY=$(command -v python)
else
    printf 'verify.sh: no python interpreter found (need .venv or python3 on PATH)\n' >&2
    exit 2
fi

TOTAL_START=$("$PY" -c 'import time; print(time.time())')

# Run a suite. Args: name, cwd, env_string, cmd_string.
#   env_string is space-separated KEY=VAL pairs (empty ok).
#   cmd_string is a single shell-quoted string passed to sh -c.
run_suite() {
    name=$1
    cwd=$2
    env_string=$3
    cmd_string=$4

    log="$LOG_DIR/$name.log"
    printf 'running %-9s ... ' "$name"

    start=$("$PY" -c 'import time; print(time.time())')
    # shellcheck disable=SC2086
    ( cd "$cwd" && env $env_string sh -c "$cmd_string" ) >"$log" 2>&1
    rc=$?
    end=$("$PY" -c 'import time; print(time.time())')
    # Sub-second precision formatted as e.g. "1.23s".
    dur=$("$PY" -c "print(f'{${end}-${start}:.2f}')")

    # Strip ANSI escape codes so output parsing isn't tripped by color sequences.
    clean_log="$LOG_DIR/$name.clean"
    sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$log" >"$clean_log"

    # Pull the headline result line(s) out of the log. We keep:
    #   - pytest:    "5 failed, 38 passed"  (strip trailing "in Ts")
    #   - django:    "Ran 57 tests"          (strip trailing "in TTs")
    #   - vitest:    "18 files / 141 tests passed"
    summary=$(awk '
        /^=+ (FAILURES|short test summary info)/ { next }
        /^=+ .* (passed|failed|skipped|error)/ {
            line = $0; sub(/ in [0-9.]+s$/, "", line); print line; next
        }
        /[0-9]+ (passed|failed|error|skipped).*in [0-9]/ {
            line = $0; sub(/ in [0-9.]+s$/, "", line); print line; next
        }
        /^Ran [0-9]+ test/ {
            line = $0; sub(/ in [0-9.]+s$/, "", line); ran_line = line; next
        }
        /^FAILED \(/ {
            print ran_line " " $0
            ran_line = ""
            next
        }
        /^OK$/ {
            print ran_line
            ran_line = ""
            next
        }
        /^Destroying test database/ { ran_line = ""; next }
        /^[[:space:]]*Test Files / {
            tf_line = $0
            sub(/^[[:space:]]*Test Files[[:space:]]*/, "", tf_line)
            next
        }
        /^[[:space:]]*Tests / {
            t_line = $0
            sub(/^[[:space:]]*Tests[[:space:]]*/, "", t_line)
            if (tf_line != "") {
                print tf_line ", " t_line
                tf_line = ""
            } else {
                print t_line
            }
            next
        }
        /^[[:space:]]*Duration / { next }
        /^[[:space:]]*Start at / { next }
        { tf_line = "" }
    ' "$clean_log" | sed 's/^[[:space:]]*//' | tr -s ' ' | head -c 200)

    if [ "$rc" -eq 0 ]; then
        verdict="PASS"
    else
        # Detect environment-missing failures: the suite's toolchain
        # wasn't installed (e.g. no node_modules => ``sh: 1: vitest: not
        # found`` in a fresh worktree).  Two signals together identify
        # this case reliably:
        #   1. rc != 0, AND
        #   2. no test counts were parsed (a real failure always emits
        #      a passed/failed/error line), AND
        #   3. the log contains a "not found"-style message from the
        #      shell trying to exec the missing binary.
        # Without this branch the gate used to record a fresh worktree
        # as ``frontend|FAIL`` and promote-check reported a fake
        # critical-suite HOLD — blocking every clean task until a human
        # checked by hand.  ``ENV_MISSING`` is its own category so the
        # report says plainly "environment missing" (provision and
        # retry), never a fake FAILED.
        if [ -z "$summary" ] && grep -Eiq "(command not found|: not found|no such file or directory)" "$clean_log"; then
            verdict="ENV_MISSING"
        else
            verdict="FAIL"
        fi
    fi

    printf '%s %s (%ss)\n' "$name" "$verdict" "$dur"
    printf '%s|%s|%s|%s\n' "$name" "$verdict" "$summary" "$dur" >> "$RESULTS"
}

# Parse args. No args => run everything.
SELECTED=$*
if [ -z "$SELECTED" ]; then
    SELECTED="odin backend frontend snapshots"
fi

# Suites. Each runs in its own working directory with the right env.
if echo " $SELECTED " | grep -q " odin "; then
    run_suite odin "$ODIN_DIR" "" \
        "$PY -m pytest tests/ --tb=line -q"
fi

if echo " $SELECTED " | grep -q " backend "; then
    run_suite backend "$BACKEND_DIR" \
        "USE_SQLITE=True FIREBASE_AUTH_ENABLED=False" \
        "$PY manage.py test tests -v 1"
fi

if echo " $SELECTED " | grep -q " frontend "; then
    # Lazy-provision frontend deps before running the suite.  Task worktrees
    # are born without node_modules (gitignored); without this the suite dies
    # env-only ("vitest: not found") and the gate reports ENV_MISSING — a fake
    # critical-suite hold.  provision_frontend_deps.sh shares one install
    # under the main repo's .odin/.cache and symlinks it in (instant after the
    # first gate run).  Best-effort: on failure the suite still runs and
    # honestly reports ENV_MISSING (the provisioning error is in the log).
    # Output is appended to the frontend log so diagnosis stays in one place.
    FRONTEND_DIR="$FRONTEND_DIR" sh "$ROOT_DIR/scripts/provision_frontend_deps.sh" \
        >>"$LOG_DIR/frontend.log" 2>&1 || true
    run_suite frontend "$FRONTEND_DIR" "" "npm run test:run"
fi

if echo " $SELECTED " | grep -q " snapshots "; then
    run_suite snapshots "$ROOT_DIR" "" \
        "$PY -m pytest tests/e2e_snapshots/ --tb=line -q"
fi

TOTAL_END=$("$PY" -c 'import time; print(time.time())')
TOTAL=$("$PY" -c "print(f'{${TOTAL_END}-${TOTAL_START}:.2f}')")

# Summary table.
printf '\n===== verify summary =====\n'
printf '%-10s %-6s %-50s %-8s\n' "SUITE" "RESULT" "TESTS" "SECONDS"
printf '%-10s %-6s %-50s %-8s\n' "----------" "------" "--------------------------------------------------" "--------"
while IFS='|' read -r name verdict summary dur; do
    [ -z "$name" ] && continue
    # Trim summary to 48 chars so the table stays a fixed width.
    short=$(printf '%s' "$summary" | cut -c1-48)
    printf '%-10s %-6s %-50s %-8s\n' "$name" "$verdict" "$short" "$dur"
done < "$RESULTS"
printf '%-10s %-6s %-50s %-8s\n' "----------" "------" "--------------------------------------------------" "--------"
printf '%-10s %-6s %-50s %-8s\n' "TOTAL" "-" "-" "$TOTAL"

# Decide the gate.  Both FAIL and ENV_MISSING make the gate RED — a
# suite that couldn't run is not a pass.  The distinction between them
# is in the per-suite table above (and in _results.txt, which is what
# promote-check parses); the exit code just says "not all green".
overall=0
env_missing_count=0
fail_count=0
while IFS='|' read -r name verdict summary dur; do
    [ -z "$name" ] && continue
    if [ "$verdict" = "FAIL" ]; then
        overall=1
        fail_count=$((fail_count + 1))
    elif [ "$verdict" = "ENV_MISSING" ]; then
        overall=1
        env_missing_count=$((env_missing_count + 1))
    fi
done < "$RESULTS"

if [ "$overall" -ne 0 ]; then
    # Name the category so a human scanning the tail knows whether to
    # investigate test failures or to provision the environment — the
    # two have completely different fixes, and conflating them is what
    # made the fresh-worktree case look like a code regression.
    parts=""
    [ "$fail_count" -gt 0 ]         && parts="$parts $fail_count FAIL"
    [ "$env_missing_count" -gt 0 ]  && parts="$parts $env_missing_count ENV_MISSING (provision and retry)"
    printf '\nverify.sh: RED —%s (see %s for full logs)\n' "$parts" "$LOG_DIR"
else
    printf '\nverify.sh: GREEN — all selected suites passed\n'
fi

exit "$overall"