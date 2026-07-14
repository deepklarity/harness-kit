#!/bin/sh
# Add python3 + taskit-mcp (from a host-built odin wheel) to the odinbuild
# sandbox, then re-snapshot odin-agents. The guest agent CLIs spawn taskit-mcp
# (stdio) to reach the host TaskIt backend over the LAN IP.
#
# Usage:
#   python3 -m pip wheel --no-deps -w /tmp/wheels ./odin
#   sh docs/fable_roadmap/bootstrap/sandbox_image/extend_image_taskit_mcp.sh /tmp/wheels/odin-*.whl
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild
WHEEL="$1"
[ -f "$WHEEL" ] || { echo "usage: $0 <odin-wheel-path>"; exit 1; }
WHEEL_BASE=$(basename "$WHEEL")

msb start "$SBX" || { echo START_FAILED; exit 1; }
sleep 3
msb copy "$WHEEL" "$SBX:/tmp/$WHEEL_BASE" || { echo COPY_FAILED; exit 1; }

echo "=== install python3 + venv ==="
msb exec "$SBX" -- sh -lc 'apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv 2>&1 | tail -3' || echo APT_NONZERO

echo "=== install odin wheel into /opt/odin-mcp venv ==="
msb exec "$SBX" -- sh -lc "python3 -m venv /opt/odin-mcp && /opt/odin-mcp/bin/pip install -q /tmp/$WHEEL_BASE && ln -sf /opt/odin-mcp/bin/taskit-mcp /usr/local/bin/taskit-mcp" || { echo PIP_FAILED; exit 1; }

echo "=== verify ==="
msb exec "$SBX" -- sh -lc 'which taskit-mcp && /opt/odin-mcp/bin/python -c "import odin.mcps.taskit_mcp.server" && echo TASKIT_MCP_IMPORT_OK'

echo "=== stop + re-snapshot odin-agents (overwrite) ==="
msb stop "$SBX"
msb snapshot create -f --from "$SBX" odin-agents
echo "=== EXTEND TASKIT-MCP DONE ==="
