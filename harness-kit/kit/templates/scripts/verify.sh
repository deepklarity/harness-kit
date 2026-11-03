#!/bin/sh
# scripts/verify.sh: the one verify gate. Runs named suites and prints a summary table.
#
# Usage:
#   scripts/verify.sh              # every suite
#   scripts/verify.sh unit docs    # a subset
#
# Exit code is 0 only when every selected suite passes. No skips, no masks.
# Fill in the suites below: one `suite name dir command` line per suite, using the
# commands the repo already documents. Keep suites that bill a provider out of this list.
# Run logs go to .verify-logs/ (overwritten each run).

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG="$ROOT/.verify-logs"
mkdir -p "$LOG"
RESULTS="$LOG/_results.txt"
: > "$RESULTS"

# suite <name> <dir relative to root> <command>
suite() {
  name=$1; dir=$2; cmd=$3
  if [ -n "${SELECTED:-}" ]; then
    case " $SELECTED " in *" $name "*) ;; *) return 0 ;; esac
  fi
  start=$(date +%s)
  (cd "$ROOT/$dir" && sh -c "$cmd") > "$LOG/$name.log" 2>&1
  code=$?
  secs=$(( $(date +%s) - start ))
  if [ $code -eq 0 ]; then status=PASS; else status=FAIL; fi
  printf '%s|%s|%ss|%s\n' "$name" "$status" "$secs" "$LOG/$name.log" >> "$RESULTS"
}

SELECTED="$*"

# --- suites: replace these examples ---------------------------------------
suite docs . "node docs/harnesskit/templates/scripts/check_doc_links.mjs ."
# suite unit  backend  "pytest -q -m 'not live and not stack'"
# suite web   frontend "npm run test:run"
# --------------------------------------------------------------------------

printf '\n%-14s %-6s %-8s %s\n' SUITE RESULT TIME LOG
fail=0
while IFS='|' read -r name status secs log; do
  printf '%-14s %-6s %-8s %s\n' "$name" "$status" "$secs" "$log"
  [ "$status" = FAIL ] && fail=1
done < "$RESULTS"
if [ ! -s "$RESULTS" ]; then echo "no suite ran"; exit 2; fi
exit $fail
