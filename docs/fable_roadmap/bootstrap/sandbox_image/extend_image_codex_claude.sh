#!/bin/sh
# Add codex + claude to the existing odinbuild sandbox (already has opencode+kilo),
# then re-snapshot odin-agents (overwrite).
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild
msb start "$SBX" || { echo START_FAILED; exit 1; }
sleep 3
msb status "$SBX"
echo "=== install codex + claude ==="
msb exec "$SBX" -- sh -lc 'npm i -g @openai/codex @anthropic-ai/claude-code 2>&1 | tail -8' || echo INSTALL_NONZERO
echo "=== verify all 4 agent CLIs ==="
msb exec "$SBX" -- sh -lc 'for c in opencode kilo codex claude; do printf "%s -> " "$c"; which "$c" 2>/dev/null || echo MISSING; done; echo VERS:; codex --version 2>&1 | head -1; claude --version 2>&1 | head -1'
echo "=== stop + re-snapshot odin-agents (overwrite) ==="
msb stop "$SBX"
msb snapshot create -f --from "$SBX" odin-agents
echo "=== EXTEND DONE ==="
