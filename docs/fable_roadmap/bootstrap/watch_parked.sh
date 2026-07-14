#!/bin/sh
# watch_parked.sh v3 — emits a line whenever a task is PARKED (merge
# needs_human / merge error). v1 bug: the tasks API is paginated;
# iterating the raw dict crashed every poll, silently.
POLL=60
RENOTIFY=1800
STATE_DIR=$(mktemp -d)
while true; do
  python3 - <<'EOF'
import json, urllib.request
base = "http://localhost:9100"
for board in (5, 6):
    url = f"{base}/api/tasks/?board_id={board}"
    while url:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.load(r)
        except Exception as e:
            print(f"PARKED-WATCH API_DOWN board={board} {e}")
            break
        for t in d.get("results", []):
            md = t.get("metadata") or {}
            ms = md.get("merge_status")
            st = t.get("status")
            # Parked = anything that needs a human/operator and produces
            # no further status transitions on its own:
            #  - merge waiting on an answer or dead on an error
            #  - FAILED (v2 missed this: three same-window agent failures
            #    sat invisible for 90 minutes)
            if st in ("REVIEW", "EXECUTING", "IN_PROGRESS") and ms in ("needs_human", "error"):
                print(f"PARKED board={board} task={t['id']} status={st} merge={ms} title={t.get('title','')[:50]!r}")
            elif st == "FAILED":
                reason = (md.get("last_failure_type") or "?")
                print(f"PARKED board={board} task={t['id']} status=FAILED type={reason} title={t.get('title','')[:50]!r}")
        url = d.get("next")
EOF
  sleep $POLL
done | while read line; do
  key=$(echo "$line" | tr -c 'A-Za-z0-9' '_' | cut -c1-100)
  stamp="$STATE_DIR/$key"
  now=$(date +%s)
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  if [ $((now - last)) -ge $RENOTIFY ]; then
    echo "$line"
    echo "$now" > "$stamp"
  fi
done
