#!/usr/bin/env bash
set -euo pipefail

# Provision the reusable forkd browser rootfs/snapshot used by chrome-devtools MCP.
# This is intentionally a setup-time step, not a per-task sandbox step.

IF_CONFIGURED=0
if [ "${1:-}" = "--if-configured" ]; then
  IF_CONFIGURED=1
fi

find_first_file() {
  for path in "$@"; do
    expanded="${path/#\~/$HOME}"
    if [ -f "$expanded" ]; then
      printf '%s\n' "$expanded"
      return 0
    fi
  done
  return 1
}

find_first_dir() {
  for path in "$@"; do
    expanded="${path/#\~/$HOME}"
    if [ -d "$expanded" ]; then
      printf '%s\n' "$expanded"
      return 0
    fi
  done
  return 1
}

FORKD_BIN="${FORKD_BIN:-}"
if [ -z "$FORKD_BIN" ]; then
  FORKD_BIN="$(command -v forkd 2>/dev/null || true)"
fi
if [ -z "$FORKD_BIN" ] && [ -x "$HOME/forkd-poc/bin/forkd" ]; then
  FORKD_BIN="$HOME/forkd-poc/bin/forkd"
fi

KERNEL="${FORKD_KERNEL:-}"
if [ -z "$KERNEL" ]; then
  KERNEL="$(find_first_file /var/lib/forkd/kernels/vmlinux /var/lib/forkd/vmlinux ~/forkd-poc/vmlinux || true)"
fi

SCRIPTS_DIR="${FORKD_SCRIPTS_DIR:-}"
if [ -z "$SCRIPTS_DIR" ]; then
  SCRIPTS_DIR="$(find_first_dir /usr/local/share/forkd/scripts /opt/forkd/scripts ~/forkd-poc/forkd/scripts || true)"
fi

if [ -z "$FORKD_BIN" ] || [ -z "$KERNEL" ]; then
  if [ "$IF_CONFIGURED" -eq 1 ]; then
    echo "forkd browser provision: skipped (forkd binary/kernel not configured)"
    exit 0
  fi
  echo "forkd browser provision failed: forkd binary/kernel not found" >&2
  exit 1
fi

TAG="${FORKD_BROWSER_SNAPSHOT_TAG:-odin-node22-4g-cli-browser}"
IMAGE="${FORKD_IMAGE:-node:22-slim}"
SIZE_MIB="${FORKD_BROWSER_ROOTFS_SIZE_MIB:-8192}"
MEM_MIB="${FORKD_MEM_SIZE_MIB:-4096}"
TAP="${FORKD_TAP:-forkd-tap0}"
CACHE_DIR="${FORKD_CACHE_DIR:-$HOME/.cache/odin/forkd}"
ROOTFS="$CACHE_DIR/node-22-slim-python3-ca-certificates-git-chromium-s${SIZE_MIB}.ext4"
CONTROLLER_URL="${FORKD_CONTROLLER_URL:-http://127.0.0.1:8889}"

register_snapshot_state_if_needed() {
  local snapshot_dir="/var/lib/forkd/snapshots/$TAG"
  [ -d "$snapshot_dir" ] || return 1
  if curl -fsS "$CONTROLLER_URL/v1/snapshots/$TAG/info" >/dev/null 2>&1; then
    return 0
  fi
  sudo python3 - "$TAG" "$snapshot_dir" <<'PY'
import json, os, pathlib, sys, tempfile
tag, snapshot_dir = sys.argv[1], sys.argv[2]
state_path = pathlib.Path('/var/lib/forkd/state.json')
state_path.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(state_path.read_text())
except Exception:
    data = {"snapshots": {}, "sandboxes": {}, "workspaces": {}}
data.setdefault("snapshots", {})[tag] = {
    "tag": tag,
    "dir": snapshot_dir,
    "created_at_unix": int(os.stat(snapshot_dir).st_mtime),
    "status": "ready",
}
data.setdefault("sandboxes", {})
data.setdefault("workspaces", {})
fd, tmp = tempfile.mkstemp(prefix='state.', suffix='.json', dir=str(state_path.parent))
with os.fdopen(fd, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
os.replace(tmp, state_path)
PY
}

snapshot_ready() {
  if curl -fsS "$CONTROLLER_URL/v1/snapshots/$TAG/info" >/dev/null 2>&1; then
    return 0
  fi
  if [ -d "/var/lib/forkd/snapshots/$TAG" ]; then
    register_snapshot_state_if_needed
    return 0
  fi
  return 1
}

if snapshot_ready; then
  echo "forkd browser provision: snapshot ready ($TAG)"
  exit 0
fi

mkdir -p "$CACHE_DIR"
if [ -n "$SCRIPTS_DIR" ]; then
  export FORKD_SCRIPTS_DIR="$SCRIPTS_DIR"
fi

if [ ! -f "$ROOTFS" ]; then
  echo "forkd browser provision: building browser rootfs once at $ROOTFS"
  sudo -E "$FORKD_BIN" parent build "$IMAGE" \
    --output "$ROOTFS" \
    --size-mib "$SIZE_MIB" \
    --extra python3 \
    --extra ca-certificates \
    --extra git \
    --extra chromium
  sudo chown "$(id -u):$(id -g)" "$ROOTFS" 2>/dev/null || true
else
  echo "forkd browser provision: rootfs exists ($ROOTFS)"
fi

if snapshot_ready; then
  echo "forkd browser provision: snapshot ready ($TAG)"
  exit 0
fi

echo "forkd browser provision: creating controller snapshot $TAG"
ENV_ARGS=("XDG_DATA_HOME=/var/lib" "PATH=$HOME/.local/bin:$PATH")
if [ -n "$SCRIPTS_DIR" ]; then
  ENV_ARGS+=("FORKD_SCRIPTS_DIR=$SCRIPTS_DIR")
fi
sudo env "${ENV_ARGS[@]}" "$FORKD_BIN" snapshot \
  --tag "$TAG" \
  --kernel "$KERNEL" \
  --rootfs "$ROOTFS" \
  --rw \
  --tap "$TAP" \
  --boot-wait-secs 10 \
  --mem-size-mib "$MEM_MIB"

echo "forkd browser provision: ready ($TAG)"
