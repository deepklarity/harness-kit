#!/bin/sh
# Add the agy CLI (Google Antigravity — gemini's successor) to the existing
# odinbuild sandbox, then re-snapshot odin-agents (overwrite).
#
# METHOD (verified live 2026-07-06 — this exact flow built the current image):
# agy is NOT an npm package, and node:22-slim has NO curl — piping install.sh
# inside the guest fails. Instead: download the release tarball ON THE HOST,
# verify sha512 against the vendor manifest, `msb copy` it into the guest,
# extract, and rename — the tarball's binary is named `antigravity`, not agy.
#
#   manifest: https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/{os}_{arch}.json
#   (linux_arm64 for the arm64-Mac libkrun guest; glibc, not musl)
#
# AUTH is staged at run time by the harness (read-only mounts of ~/.antigravity
# and ~/.gemini — microsandbox.py::_CREDENTIAL_PATHS["agy"]). CAVEAT: agy's
# OAuth may be keychain-bound like claude's (F28) — the first confined smoke
# run is the auth test.
#
# Usage: sh docs/fable_roadmap/bootstrap/sandbox_image/extend_image_agy.sh
set -e
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild
ARCH="${AGY_ARCH:-linux_arm64}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MANIFEST_URL="https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/${ARCH}.json"
curl -fsSL --max-time 20 "$MANIFEST_URL" -o "$WORK/manifest.json"
URL=$(sed -n 's/.*"url": *"\([^"]*\)".*/\1/p' "$WORK/manifest.json")
SHA=$(sed -n 's/.*"sha512": *"\([^"]*\)".*/\1/p' "$WORK/manifest.json")
[ -n "$URL" ] && [ -n "$SHA" ] || { echo MANIFEST_PARSE_FAILED; exit 1; }

curl -fsSL --max-time 300 "$URL" -o "$WORK/agy.tar.gz"
echo "$SHA  $WORK/agy.tar.gz" | shasum -a 512 -c - || { echo SHA_MISMATCH; exit 1; }

msb start "$SBX"
sleep 4
msb copy "$WORK/agy.tar.gz" "$SBX:/tmp/agy.tar.gz"
msb exec "$SBX" -- sh -lc '
  tar -xzf /tmp/agy.tar.gz -C /usr/local/bin &&
  mv /usr/local/bin/antigravity /usr/local/bin/agy &&
  chmod +x /usr/local/bin/agy && rm /tmp/agy.tar.gz &&
  agy --version'

echo "=== verify all agent CLIs present ==="
msb exec "$SBX" -- sh -lc 'for c in opencode kilo codex claude agy; do printf "%s -> " "$c"; which "$c" || echo MISSING; done'

echo "=== stop + re-snapshot odin-agents (overwrite) ==="
msb stop "$SBX"
msb snapshot create -f --from "$SBX" odin-agents
echo "=== EXTEND AGY DONE ==="
