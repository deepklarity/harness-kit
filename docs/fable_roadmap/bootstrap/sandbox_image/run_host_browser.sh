#!/bin/sh
# Start the shared host-side browser that microsandbox agents drive over CDP for
# visual proof (screenshots of the local UI).
#
# WHY (proven live 2026-07-07, task 182): the odin-agents microsandbox guest is an
# aarch64 libkrun microVM. Chromium CANNOT launch there — every headless invocation
# SIGTRAPs (exit 133 / `brk`) during early browser bring-up, regardless of
# --no-sandbox / --single-process / --disable-gpu / --jitless. `chromium --version`
# and node's V8 both work, so it is not JIT/PAC; the browser process simply cannot
# initialize inside the microVM. (A forkd guest is a container sharing the host
# kernel, where Chromium DOES run — that path launches Chromium in-guest and needs
# nothing here.)
#
# The fix keeps the browser where it works — the host — and points the in-guest
# chrome-devtools-mcp at it via --browserUrl. The orchestrator injects
# `--browserUrl http://127.0.0.1:9222` for microsandbox agents; the MCP-config
# staging step rewrites 127.0.0.1 -> the host LAN IP, so the guest connects to the
# host's :9222. Egress to the host is already open when MCP is configured, so no
# net rule is needed. Override the endpoint with `chrome_devtools.browser_url` in
# .odin/config.yaml.
#
# Chrome's DevTools endpoint allows IP-literal Host headers (the DNS-rebinding guard
# only blocks hostnames), so guests connecting by the host IP are accepted. The only
# requirement is that :9222 is reachable from the guest. Chrome binds the debug port
# to 127.0.0.1, so we forward the guest-facing interface to it with socat.
#
# Usage: sh docs/fable_roadmap/bootstrap/sandbox_image/run_host_browser.sh [PORT]
#   Runs in the foreground (chrome + forwarder). Ctrl-C stops both.
#   Leave it running for the lifetime of the agent fleet.
set -e

PORT="${1:-9222}"
CHROME="$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null || command -v google-chrome 2>/dev/null || command -v google-chrome-stable 2>/dev/null || true)"
[ -n "$CHROME" ] || { echo "No chromium/chrome on host. Install one (apt-get install -y chromium)."; exit 1; }
command -v socat >/dev/null 2>&1 || { echo "socat is required (apt-get install -y socat)."; exit 1; }

# Guest-facing host IP on the microsandbox bridge. msb exposes the host to guests as
# host.microsandbox.internal; guests connect by that IP. Binding the forwarder to
# 0.0.0.0 is simplest and safe on an isolated build host.
BIND_ADDR="${BROWSER_BIND_ADDR:-0.0.0.0}"

PROFILE="$(mktemp -d /tmp/odin-host-browser.XXXXXX)"
echo "=== launching headless $CHROME on 127.0.0.1:$PORT (profile: $PROFILE) ==="
"$CHROME" \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins='*' \
  --user-data-dir="$PROFILE" \
  about:blank &
CHROME_PID=$!

cleanup() { kill "$CHROME_PID" "$SOCAT_PID" 2>/dev/null || true; rm -rf "$PROFILE"; }
trap cleanup INT TERM EXIT

# Wait for the debug endpoint to come up.
i=0
while [ "$i" -lt 30 ]; do
  if command -v curl >/dev/null 2>&1 && curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then break; fi
  i=$((i + 1)); sleep 0.5
done

echo "=== forwarding $BIND_ADDR:$PORT -> 127.0.0.1:$PORT (socat) ==="
socat "TCP-LISTEN:$PORT,fork,reuseaddr,bind=$BIND_ADDR" "TCP:127.0.0.1:$PORT" &
SOCAT_PID=$!

echo "=== host browser ready. Guests reach it via --browserUrl http://<host-ip>:$PORT ==="
echo "    Verify from a guest:  node -e \"require('http').get('http://host.microsandbox.internal:$PORT/json/version',r=>r.pipe(process.stdout))\""
echo "    Ctrl-C to stop."
wait "$CHROME_PID"
