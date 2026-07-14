#!/bin/sh
# Provision an odin sandbox image with the opencode(glm) + kilo(minimax) CLIs.
# Provision-then-snapshot (msb has no Dockerfile build). Leaves the sandbox
# STOPPED and ready to snapshot.
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild

msb stop "$SBX" 2>/dev/null; msb remove "$SBX" 2>/dev/null

echo "=== pull base image (node:22-slim) ==="
msb pull node:22-slim || { echo "PULL_FAILED"; exit 1; }

echo "=== create sandbox (2G RAM for the install) ==="
msb create -n "$SBX" -m 2048 node:22-slim || { echo "CREATE_FAILED"; exit 1; }
sleep 3
msb status "$SBX"

echo "=== install git + agent CLIs (opencode-ai, @kilocode/cli) ==="
msb exec "$SBX" -- sh -lc 'apt-get update -qq && apt-get install -y -qq git ca-certificates && npm i -g opencode-ai @kilocode/cli 2>&1 | tail -6' || echo "INSTALL_STEP_NONZERO"

echo "=== verify CLIs inside guest ==="
msb exec "$SBX" -- sh -lc 'echo PATHS:; which node npm git opencode kilo 2>&1; echo VERSIONS:; node --version; git --version; opencode --version 2>&1 | head -1; kilo --version 2>&1 | head -1'

echo "=== stop sandbox (snapshot requires a stopped sandbox) ==="
msb stop "$SBX"
echo "=== BUILD DONE — $SBX is stopped, ready to snapshot ==="
