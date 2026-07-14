#!/bin/sh
# watch_board.sh — the ONE board watcher for operator sessions.
#
# Polls every task on the board every POLL seconds and writes one line to
# stdout in exactly two cases:
#   1. IMMEDIATELY when any task's status changes:  "CHANGE <id>:<old>-><new> ..."
#   2. A heartbeat with the full non-terminal state, at exponentially
#      increasing intervals since the last change (30s,60s,...,cap 600s):
#      "HEARTBEAT <age>s <id:status ...>"
# API unreachability is itself an event ("API_DOWN"), so silence is bounded:
# no output for > heartbeat cap means the watcher itself is dead, not idle.
#
# Designed to run under a notification harness (Claude Monitor, or
# `... | while read line; do notify; done`) — stdout lines ARE the events.
#
# Usage: sh docs/fable_roadmap/bootstrap/watch_board.sh [board_id] [poll_secs]
BOARD="${1:-5}"
POLL="${2:-10}"
BASE="${TASKIT_URL:-http://localhost:9100}"

fetch() {
  curl -s --max-time 8 "$BASE/api/tasks/?board_id=$BOARD" | python3 -c '
import sys, json
try:
    items = json.load(sys.stdin)
    if isinstance(items, dict): items = items.get("results", [])
    parts = []
    for t in sorted(items, key=lambda t: t["id"]):
        tag = t["status"]
        # A parked merge waits on a human reply — that must be visible in
        # every state line, or the operator flies blind for hours (task 344).
        if (t.get("metadata") or {}).get("merge_status") == "needs_human":
            tag += "+NEEDS_HUMAN"
        parts.append("%s:%s" % (t["id"], tag))
    print(" ".join(parts))
except Exception:
    print("PARSE_ERROR")
' 2>/dev/null
}

prev=""
last_change=$(date +%s)
hb_interval=30
next_hb=$(( last_change + hb_interval ))
api_down=0
# Per-status stall thresholds (minutes). Normal durations differ wildly by
# state: executions run 10-35m routinely, queued tasks wait for slots, but a
# REVIEW sitting half an hour means a reviewer died or a merge waits on a
# human. Tunable via env.
STALL_EXECUTING=$(( ${STALL_EXECUTING_MINUTES:-45} * 60 ))
STALL_REVIEW=$(( ${STALL_REVIEW_MINUTES:-25} * 60 ))
STALL_QUEUED=$(( ${STALL_QUEUED_MINUTES:-90} * 60 ))
STALL_OTHER=$(( ${STALL_MINUTES:-30} * 60 ))
task_times=""   # "id:epoch" pairs, updated on every per-task change
stall_alerted="" # "id:status" pairs already alarmed — one STALLED per stall

last_tick=$(date +%s)
while true; do
  cur=$(fetch)
  now=$(date +%s)
  # Host slept: wall clock jumped far beyond the poll interval. Stall
  # clocks measured across a sleep are lies — reset them all.
  if [ $(( now - last_tick )) -gt $(( POLL * 6 + 60 )) ]; then
    echo "CLOCK_JUMP $(( (now - last_tick) / 60 ))m gap (host slept?) — stall clocks reset"
    task_times=""; stall_alerted=""
  fi
  last_tick=$now

  if [ -z "$cur" ] || [ "$cur" = "PARSE_ERROR" ]; then
    if [ "$api_down" -eq 0 ]; then echo "API_DOWN $BASE (state=$cur)"; api_down=1; fi
    sleep "$POLL"; continue
  fi
  if [ "$api_down" -eq 1 ]; then echo "API_RECOVERED"; api_down=0; fi

  if [ -n "$prev" ] && [ "$cur" != "$prev" ]; then
    # diff pair-by-pair: report old->new per changed task
    changes=""
    for pair in $cur; do
      id="${pair%%:*}"
      old=$(printf '%s\n' $prev | tr ' ' '\n' | grep "^$id:" | head -1)
      [ -n "$old" ] && [ "$old" != "$pair" ] && changes="$changes $id:${old#*:}->${pair#*:}"
      [ -z "$old" ] && changes="$changes $id:NEW->${pair#*:}"
    done
    if [ -n "$changes" ]; then
      echo "CHANGE$changes"
      last_change=$now
      hb_interval=30
      next_hb=$(( now + hb_interval ))
    fi
  fi
  prev="$cur"

  # per-task stall clock: reset on change, seed on first sight
  new_times=""
  for pair in $cur; do
    id="${pair%%:*}"
    old=$(printf '%s\n' $prev | tr ' ' '\n' | grep "^$id:" | head -1)
    seen=$(printf '%s\n' $task_times | tr ' ' '\n' | grep "^$id:" | head -1)
    if [ -z "$seen" ] || { [ -n "$old" ] && [ "$old" != "$pair" ]; }; then
      new_times="$new_times $id:$now"
    else
      new_times="$new_times $seen"
    fi
  done
  task_times="$new_times"

  if [ "$now" -ge "$next_hb" ]; then
    active=$(printf '%s\n' $cur | tr ' ' '\n' | grep -vE ":(DONE|FAILED|BACKLOG|TESTING)$" | tr '\n' ' ')
    # Stall alarm: any non-terminal, non-shelf task unchanged for STALL_SECS.
    # A stalled task is an event for the operator: inspect it (haiku triage),
    # don't wait for a human to notice.
    for pair in $active; do
      id="${pair%%:*}"; st="${pair#*:}"
      t0=$(printf '%s\n' $task_times | tr ' ' '\n' | grep "^$id:" | head -1)
      t0="${t0#*:}"
      [ -z "$t0" ] && continue
      case "$st" in
        EXECUTING) lim=$STALL_EXECUTING ;;
        REVIEW) lim=$STALL_REVIEW ;;
        IN_PROGRESS|TODO) lim=$STALL_QUEUED ;;
        *) lim=$STALL_OTHER ;;
      esac
      if [ $(( now - t0 )) -ge "$lim" ]; then
        if ! printf '%s\n' $stall_alerted | tr ' ' '\n' | grep -q "^$pair$"; then
          echo "STALLED $pair unchanged $(( (now - t0) / 60 ))m (limit $(( lim / 60 ))m) — triage now (task_inspect --brief, last comments, logs)"
          stall_alerted="$stall_alerted $pair"
        fi
      fi
    done
    # forget alerts for pairs no longer present (task changed state)
    kept=""
    for ap in $stall_alerted; do
      printf '%s\n' $cur | tr ' ' '\n' | grep -q "^$ap$" && kept="$kept $ap"
    done
    stall_alerted="$kept"
    # Hard refill trigger (user directive): a thin queue is an explicit event,
    # not something the operator computes from heartbeats. cap+2 threshold.
    live=$(printf '%s\n' $active | tr ' ' '\n' | grep -cE ":(EXECUTING|IN_PROGRESS)$")
    [ "$live" -lt "${QUEUE_LOW_THRESHOLD:-5}" ] && echo "QUEUE_LOW live=$live threshold=${QUEUE_LOW_THRESHOLD:-5} — refill the board NOW (RESUME refill rule)"
    echo "HEARTBEAT $((now - last_change))s active: ${active:-none}"
    hb_interval=$(( hb_interval * 2 )); [ "$hb_interval" -gt 600 ] && hb_interval=600
    next_hb=$(( now + hb_interval ))
  fi

  sleep "$POLL"
done
