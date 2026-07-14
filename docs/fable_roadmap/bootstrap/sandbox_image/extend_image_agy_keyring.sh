#!/bin/sh
# Bake the headless Secret Service stack into the odin-agents image so agy
# (Google Antigravity) can authenticate inside the microVM.
#
# WHY (proven live 2026-07-06): agy stores its OAuth token in the OS credential
# store, NOT a file. On the Linux microVM guest that store is the freedesktop
# Secret Service (go-keyring's linux backend). The harness reads the host's live
# agy credential at runtime and seeds it into a per-run, empty-password
# gnome-keyring inside the guest (see microsandbox.py::_agy_* and the
# dbus-run-session wrapper). This image layer only provides the GENERIC daemons —
# no token is baked, so the snapshot stays user-agnostic and portable.
#
#   gnome-keyring    — the Secret Service daemon (go-keyring reads from it)
#   libsecret-tools  — `secret-tool` CLI the harness uses to seed the token
#   dbus-x11         — provides `dbus-run-session` (private session bus per run)
#
# Usage: sh docs/fable_roadmap/bootstrap/sandbox_image/extend_image_agy_keyring.sh
set -e
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild

# idempotent: start if stopped, tolerate an already-running sandbox
msb start "$SBX" 2>/dev/null || echo "(odinbuild already running — reusing)"
sleep 4

echo "=== install gnome-keyring + libsecret-tools + dbus-x11 ==="
msb exec "$SBX" -- sh -lc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq &&
  apt-get install -y -qq gnome-keyring libsecret-tools dbus-x11 2>&1 | tail -4
' || { echo APT_FAILED; exit 1; }

echo "=== verify the keyring stack is present ==="
msb exec "$SBX" -- sh -lc '
  for c in gnome-keyring-daemon secret-tool dbus-run-session agy; do
    printf "%s -> " "$c"; which "$c" || { echo MISSING; exit 1; }
  done
'

echo "=== stop + re-snapshot odin-agents (overwrite) ==="
msb stop "$SBX"
msb snapshot create -f --from "$SBX" odin-agents
echo "=== EXTEND AGY-KEYRING DONE ==="
