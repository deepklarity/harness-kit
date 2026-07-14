#!/bin/sh
# triage_task.sh — one-shot fast triage of an odin/taskit task. Target: <10s.
#
# THE first thing to run when a task failed, looks stuck, or just finished.
# No polling, no waiting — one compact report from all the cheap signals, so
# an operator (or agent session) can decide the next move immediately instead
# of watching a 3-minute loop. Companion to watch_task.sh (which is for
# *ongoing* runs, backgrounded); this is for *point-in-time* diagnosis.
#
# Usage (from anywhere):
#   sh docs/fable_roadmap/bootstrap/triage_task.sh <task_id>
#
# Sections: board state + failure metadata -> trace -> worktree -> VM -> logs.
# Last line is machine-scannable:
#   TRIAGE: status=<S> vm=<N> trace=<bytes>B failure=<type|none>

ID="$1"
[ -n "$ID" ] || { echo "usage: $0 <task_id>"; exit 2; }
cd "$(dirname "$0")/../../.." || exit 2

echo "=== TRIAGE task #$ID $(date +%H:%M:%S) ==="

# --- 1. Board truth: status, assignee, failure metadata, merge state ---
INSPECT=$(cd taskit/taskit-backend && python3 testing_tools/task_inspect.py "$ID" --json --sections basic,metadata 2>/dev/null)
STATUS=$(printf '%s' "$INSPECT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('basic', d) or {}).get('status', '?'))
except Exception:
    print('?')
")
echo "--- board: status=$STATUS ---"
printf '%s' "$INSPECT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit
b = d.get('basic', d) or {}
meta = d.get('metadata') or {}
print('  agent=%s model=%s spec=%s' % (b.get('agent'), b.get('model'), b.get('spec_id', b.get('spec'))))
for k in ('last_failure_type', 'last_failure_origin', 'last_failure_reason',
          'merge_status', 'branch', 'reflection_verdict'):
    v = meta.get(k)
    if v:
        print('  %s: %s' % (k, str(v)[:300]))
"

# --- 2. Live trace: size, mtime, last extracted text ---
# Celery runs odin with cwd = the task worktree, so the live trace may sit under
# the worktree's .odin/logs; prefer whichever is freshest.
TRACE=".odin/logs/task_${ID}.trace.jsonl"
WT_EARLY=$(ls -d .odin/worktrees/*/"$ID" 2>/dev/null | head -1)
if [ -n "$WT_EARLY" ] && [ -f "$WT_EARLY/.odin/logs/task_${ID}.trace.jsonl" ]; then
  if [ ! -f "$TRACE" ] || [ "$WT_EARLY/.odin/logs/task_${ID}.trace.jsonl" -nt "$TRACE" ]; then
    TRACE="$WT_EARLY/.odin/logs/task_${ID}.trace.jsonl"
  fi
fi
if [ -f "$TRACE" ]; then
  TSZ=$(wc -c < "$TRACE" | tr -d ' ')
  echo "--- trace: ${TSZ}B  mtime=$(stat -f %Sm -t %H:%M:%S "$TRACE" 2>/dev/null || stat -c %y "$TRACE" 2>/dev/null | cut -c12-19) ---"
  # Last meaningful lines: prefer text/error events over step bookkeeping
  tail -40 "$TRACE" | python3 -c "
import sys, json
keep = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        j = json.loads(line)
    except Exception:
        keep.append(line[:200]); continue
    t = j.get('type', '')
    if t in ('text', 'error', 'tool_use', 'result') or 'error' in str(j).lower()[:400]:
        keep.append(line[:240])
for line in keep[-6:]:
    print(' ', line)
" 2>/dev/null
  tail -3 ".odin/logs/task_${ID}.out" 2>/dev/null | sed 's/^/  out| /' | cut -c1-240
else
  TSZ=0
  echo "--- trace: MISSING ($TRACE) ---"
fi

# --- 3. Worktree: branch, HEAD vs spec, dirt ---
WT=$(ls -d .odin/worktrees/*/"$ID" 2>/dev/null | head -1)
if [ -n "$WT" ]; then
  BR=$(git -C "$WT" rev-parse --abbrev-ref HEAD 2>/dev/null)
  HEAD=$(git -C "$WT" rev-parse --short HEAD 2>/dev/null)
  DIRTY=$(git -C "$WT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "--- worktree: $WT  branch=$BR head=$HEAD dirty=${DIRTY} files ---"
  git -C "$WT" log --oneline -3 2>/dev/null | sed 's/^/  /'
else
  echo "--- worktree: none ---"
fi

# --- 4. Sandbox VM liveness ---
VMS=$(pgrep -f "microsandbox sandbox" 2>/dev/null | wc -l | tr -d ' ')
if [ "$VMS" -gt 0 ]; then
  CPU=$(ps -o cputime= -p "$(pgrep -f 'microsandbox sandbox' | head -1)" 2>/dev/null | tr -d ' ')
  echo "--- vm: $VMS running, cpu=$CPU ---"
else
  echo "--- vm: none running ---"
fi

# --- 5. Backend-side logs: spec/task log tail + celery errors mentioning the task ---
SPECLOG=$(ls taskit/taskit-backend/logs/spec_*_task_"${ID}".log 2>/dev/null | head -1)
if [ -n "$SPECLOG" ]; then
  echo "--- spec log tail ($SPECLOG) ---"
  tail -8 "$SPECLOG" | cut -c1-240 | sed 's/^/  /'
fi
if [ -f .dev-logs/celery.log ]; then
  CEL=$(grep -a "task[ _#]*${ID}\b" .dev-logs/celery.log 2>/dev/null | grep -ai "error\|exception\|fail" | tail -3)
  [ -n "$CEL" ] && { echo "--- celery errors ---"; echo "$CEL" | cut -c1-240 | sed 's/^/  /'; }
fi

# --- machine-scannable summary ---
FAILTYPE=$(printf '%s' "$INSPECT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('metadata') or {}).get('last_failure_type') or 'none')
except Exception:
    print('none')
")
echo "TRIAGE: status=$STATUS vm=$VMS trace=${TSZ}B failure=$FAILTYPE"
