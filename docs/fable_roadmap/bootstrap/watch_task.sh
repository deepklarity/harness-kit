#!/bin/sh
# watch_task.sh — exit-on-signal watcher for a running odin task.
#
# Usage (run BACKGROUNDED from an agent session, from anywhere):
#   sh docs/fable_roadmap/bootstrap/watch_task.sh <task_id> [max_wait_secs]
#
# The agentic-loop problem this solves: a sandboxed task can run 30 minutes,
# and "wait then look" discovers failures late. This watcher samples cheap
# health signals with phase-aware exponential backoff, prints one compact
# heartbeat line per sample, and EXITS the moment anything meaningful
# changes — a backgrounded agent session is then woken by its task-completion
# notification instead of polling blindly.
#
# Exit reasons (last stdout line, machine-scannable):
#   WATCH_EXIT: terminal-status <STATUS>   task left EXECUTING/IN_PROGRESS
#   WATCH_EXIT: new-commit <sha>           agent committed in its worktree (progress!)
#   WATCH_EXIT: never-picked-up            IN_PROGRESS but executor never started it
#                                          (check: assignee set? spec set? deps? slots?
#                                          dag_executor SILENTLY skips unassigned tasks — F43)
#   WATCH_EXIT: vm-gone                    this task's microVM vanished without a status
#                                          change (checked twice, 5s grace for status lag)
#   WATCH_EXIT: stalled                    VM alive but CPU+trace frozen >= 90s continuous
#   WATCH_EXIT: max-wait <secs>            give-up budget reached
#
# Health signals sampled:
#   - task status        (testing_tools/task_inspect.py --json, board = source of truth)
#   - live trace size    (.odin/logs/task_<id>.trace.jsonl — streamed from inside the
#                         sandbox via bind-mount tee; THE progress signal. Inspect it
#                         any time: tail -f <trace> or odin logs -f <id>)
#   - worktree HEAD+dirt (agent progress: commits land, files change)
#   - microVM liveness   ATTRIBUTED TO THIS TASK: the `microsandbox run` process
#                         bind-mounts task_<id>.trace.jsonl, so it is findable by
#                         cmdline; its child is the actual VM. A global pgrep would
#                         misattribute concurrent tasks' VMs (bit us on task #114).
#   - microVM CPU time   (ps cputime on the attributed VM — "working" vs "hung")
#
# Backoff policy (phase-aware, not blind doubling):
#   - reset to 5s on ANY status transition (transitions cluster follow-on signals)
#   - cap 15s while IN_PROGRESS (pickup should happen in ~5s; detect never-pickup fast)
#   - cap 120s while EXECUTING healthy (steady state; trace growth is the heartbeat)

ID="$1"
MAX_WAIT="${2:-2400}"
PICKUP_DEADLINE=90
[ -n "$ID" ] || { echo "usage: $0 <task_id> [max_wait_secs]"; exit 2; }

cd "$(dirname "$0")/../../.." || exit 2
find_wt() { ls -d .odin/worktrees/*/"$ID" 2>/dev/null | head -1; }
WT=$(find_wt)
# Celery runs odin with cwd = the task worktree, so the live trace may sit under
# the worktree's .odin/logs; operator foreground runs use the repo-root one.
resolve_trace() {
  WT=${WT:-$(find_wt)}
  if [ -n "$WT" ] && [ -f "$WT/.odin/logs/task_${ID}.trace.jsonl" ]; then
    TRACE="$WT/.odin/logs/task_${ID}.trace.jsonl"
  else
    TRACE=".odin/logs/task_${ID}.trace.jsonl"
  fi
}
resolve_trace

task_status() {
  (cd taskit/taskit-backend && python3 testing_tools/task_inspect.py "$ID" --json --sections basic 2>/dev/null) \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null
}

# Per-task VM attribution: the `microsandbox run` process for THIS task mounts
# task_<id>.trace.jsonl; its child process is the VM itself.
run_pid() { pgrep -f "microsandbox run.*task_${ID}\.trace\.jsonl" 2>/dev/null | head -1; }
vm_pid()  { RP=$(run_pid); [ -n "$RP" ] && pgrep -P "$RP" 2>/dev/null | head -1; }
vm_cpu()  { VP=$(vm_pid); [ -n "$VP" ] && ps -o cputime= -p "$VP" 2>/dev/null | tr -d ' '; }
trace_sz() { resolve_trace; [ -f "$TRACE" ] && wc -c < "$TRACE" | tr -d ' ' || echo 0; }
wt_head()  { WT=${WT:-$(find_wt)}; [ -n "$WT" ] && git -C "$WT" rev-parse --short HEAD 2>/dev/null; }
wt_dirty() { [ -n "$WT" ] && git -C "$WT" status --porcelain 2>/dev/null | wc -l | tr -d ' '; }

BASE_HEAD=$(wt_head)
LAST_CPU=""
LAST_TSZ=""
LAST_STATUS=""
STALL=0
PICKUP_WAIT=0
ELAPSED=0
INTERVAL=5
SEEN_VM=0

while :; do
  STATUS=$(task_status)
  RP=$(run_pid)
  CPU=$(vm_cpu)
  HEAD=$(wt_head)
  DIRTY=$(wt_dirty)
  TSZ=$(trace_sz)
  VMSTATE=$([ -n "$RP" ] && echo 1 || echo 0)
  echo "[$(date +%H:%M:%S)] status=${STATUS:-?} vm=$VMSTATE cpu=${CPU:--} trace=${TSZ}B head=${HEAD:--} dirty=${DIRTY:--} elapsed=${ELAPSED}s next=${INTERVAL}s"

  case "$STATUS" in
    EXECUTING|IN_PROGRESS|"") ;;  # still going (empty = transient query failure)
    *) echo "WATCH_EXIT: terminal-status $STATUS"; exit 0 ;;
  esac

  # Status transitions cluster follow-on signals — sample fast again.
  if [ -n "$STATUS" ] && [ "$STATUS" != "$LAST_STATUS" ] && [ -n "$LAST_STATUS" ]; then
    INTERVAL=5
  fi
  [ -n "$STATUS" ] && LAST_STATUS="$STATUS"

  if [ -n "$BASE_HEAD" ] && [ -n "$HEAD" ] && [ "$HEAD" != "$BASE_HEAD" ]; then
    echo "WATCH_EXIT: new-commit $HEAD"; exit 0
  fi

  # Never-picked-up: IN_PROGRESS means "waiting for the executor"; if no VM and
  # no trace appear within PICKUP_DEADLINE, the poller is skipping this task.
  # Known silent skips: missing assignee, missing spec, unmet deps, no free slots.
  if [ "$STATUS" = "IN_PROGRESS" ] && [ "$VMSTATE" = "0" ] && [ "$TSZ" = "0" ]; then
    PICKUP_WAIT=$((PICKUP_WAIT + INTERVAL))
    if [ "$PICKUP_WAIT" -ge "$PICKUP_DEADLINE" ]; then
      echo "WATCH_EXIT: never-picked-up (${PICKUP_WAIT}s in IN_PROGRESS with no VM/trace — check assignee, spec, deps, slots)"
      exit 0
    fi
  else
    PICKUP_WAIT=0
  fi

  # vm-gone: only for THIS task's VM, and only after a 5s grace + status recheck —
  # normal completion kills the VM moments before the status flips.
  if [ "$VMSTATE" = "1" ]; then
    SEEN_VM=1
  elif [ "$SEEN_VM" = "1" ]; then
    sleep 5
    RSTATUS=$(task_status)
    case "$RSTATUS" in
      EXECUTING|IN_PROGRESS|"") echo "WATCH_EXIT: vm-gone (task ${ID}'s VM exited, status still ${RSTATUS:-?})"; exit 0 ;;
      *) echo "WATCH_EXIT: terminal-status $RSTATUS"; exit 0 ;;
    esac
  fi

  # Stalled = CPU frozen AND trace not growing for >= 90 continuous seconds.
  # (Trace can legitimately pause while a model thinks; CPU can idle-tick while
  # hung — only both frozen together means the run is dead. Time-based, not
  # sample-based, so the fast early sampling can't false-positive.)
  if [ -n "$CPU" ] && [ "$CPU" = "$LAST_CPU" ] && [ "$TSZ" = "$LAST_TSZ" ]; then
    STALL=$((STALL + INTERVAL))
    [ "$STALL" -ge 90 ] && { echo "WATCH_EXIT: stalled"; exit 0; }
  else
    STALL=0
  fi
  LAST_CPU="$CPU"
  LAST_TSZ="$TSZ"

  if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
    echo "WATCH_EXIT: max-wait ${MAX_WAIT}s"; exit 0
  fi

  sleep "$INTERVAL"
  ELAPSED=$((ELAPSED + INTERVAL))
  INTERVAL=$((INTERVAL * 2))
  if [ "$STATUS" = "IN_PROGRESS" ]; then
    [ "$INTERVAL" -gt 15 ] && INTERVAL=15
  else
    [ "$INTERVAL" -gt 120 ] && INTERVAL=120
  fi
done
