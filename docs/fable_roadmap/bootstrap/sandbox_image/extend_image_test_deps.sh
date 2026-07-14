#!/bin/sh
# Add a python test toolchain (pytest + pytest-asyncio + hypothesis) and the
# taskit-backend requirements to the existing odinbuild sandbox, then
# re-snapshot odin-agents (overwrite). Mirrors the provision-then-snapshot
# pattern of extend_image_codex_claude.sh / extend_image_taskit_mcp.sh /
# extend_image_agy.sh.
#
# WHY: confined agents were pip-installing pytest/Django test deps inside the
# guest on every run — minutes and tokens of per-run toil on a disposable
# image. Baking the full test toolchain into the snapshot lets a sandboxed
# agent run EITHER suite (odin/tests or taskit-backend/tests) with zero
# per-run installs.
#
# WHAT GETS INSTALLED:
#   - python3 + pip + venv toolchain (apt; idempotent if taskit-mcp baked it)
#   - pytest, pytest-asyncio        (the requested test runners; pytest-asyncio
#                                     is also declared in odin/pyproject.toml
#                                     with asyncio_mode=auto)
#   - hypothesis                    (odin/pyproject.toml declares it and it is
#                                     imported by odin/tests/unit/test_dag.py,
#                                     so the odin suite cannot even collect
#                                     without it)
#   - odin runtime deps             (fire, rich, pydantic, httpx, requests,
#                                     pyyaml, python-dotenv, filelock, fastmcp
#                                     — the FULL [project].dependencies list
#                                     from odin/pyproject.toml, imported at the
#                                     top level of odin/src/odin/*. Without
#                                     these `python3 -m pytest` in odin/ fails
#                                     at collection with ImportError, which is
#                                     exactly what drives agents to rebuild a
#                                     venv + `pip install -e .` every run.
#                                     Installed explicitly (not relied on
#                                     transitively from taskit-backend
#                                     requirements) so the odin suite is
#                                     self-sufficient. pyyaml + python-dotenv
#                                     also arrive via requirements.txt below as
#                                     a harmless duplicate; keep this list in
#                                     sync with odin/pyproject.toml
#                                     [project].dependencies.)
#   - taskit-backend/requirements.txt (FULL file — see note below)
#
# WHY THE FULL requirements.txt (nothing skipped): Django imports EVERY entry
# in INSTALLED_APPS at startup, including during `manage.py test`.
# config/settings.py lists daphne, rest_framework, rest_framework_simplejwt,
# corsheaders, channels — so a partial install (e.g. dropping the
# live-server-only daphne/channels-redis to save space) makes test collection
# fail with ImportError on the missing app. The whole point of this image is a
# complete, working test environment, so the file is installed verbatim via
# `pip install -r`. The live requirements.txt is copied into the guest (msb
# copy) so the recipe auto-tracks future requirement additions without edits.
#
# WHY SYSTEM-WIDE (not a venv like /opt/odin-mcp): the taskit-mcp recipe uses
# an isolated venv because taskit-mcp is a single entry point symlinked into
# /usr/local/bin. A test toolchain is different — confined agents invoke many
# commands (`python3 -m pytest`, `python3 manage.py test`) and must not need
# to know a venv path. We therefore install into the system python3 with pip's
# --break-system-packages flag (the PEP 668 escape hatch; appropriate for a
# disposable sandbox image). The /opt/odin-mcp venv is a separate interpreter
# and is left untouched.
#
# Usage (run from the repo root):
#   sh docs/fable_roadmap/bootstrap/sandbox_image/extend_image_test_deps.sh
#
# OPERATOR VERIFY (install-free suite run) — after the re-snapshot above, boot a
# FRESH guest from the new odin-agents image and confirm BOTH python suites run
# with zero venv creation and zero pip installs:
#
#   msb start odinbuild && sleep 3
#   # 1) guest interpreter + package inventory (no .venv, deps on system python3)
#   msb exec odinbuild -- sh -lc 'which python3; ls -d /opt/odin-mcp; python3 -c "import sys; print(sys.executable)"; python3 -c "import pytest,pydantic,fastmcp,django,celery,yaml,dotenv; print(\"IMPORTS_OK\")"'
#   # 2) odin suite — clone/copy the repo into the guest, then:
#   msb exec odinbuild -- sh -lc 'cd /work/odin && PYTHONPATH=src python3 -m pytest tests/unit/ -q'
#   # 3) taskit-backend suite:
#   msb exec odinbuild -- sh -lc 'cd /work/taskit/taskit-backend && USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python3 manage.py test tests -v0'
#   msb stop odinbuild
#
# Acceptance = no `venv`/`pip install` commands appear in the trace of a confined
# run once an agent is also told (via the harness task prompt) that the env is
# pre-baked — see the "Environment:" line injected by Orchestrator._wrap_prompt.
set -x
export PATH="$HOME/.local/bin:$PATH"
SBX=odinbuild
REQ=taskit/taskit-backend/requirements.txt

[ -f "$REQ" ] || { echo "REQ_NOT_FOUND: run from repo root ($REQ missing)"; exit 1; }

msb start "$SBX" || { echo START_FAILED; exit 1; }
sleep 3
msb status "$SBX"

echo "=== copy requirements.txt into guest ==="
msb copy "$REQ" "$SBX:/tmp/requirements.txt" || { echo COPY_FAILED; msb stop "$SBX"; exit 1; }

echo "=== install python3 + pip + venv toolchain (idempotent) ==="
msb exec "$SBX" -- sh -lc 'apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv 2>&1 | tail -3' || { echo APT_FAILED; msb stop "$SBX"; exit 1; }

echo "=== install test runners + odin runtime deps + taskit-backend requirements (system python3) ==="
msb exec "$SBX" -- sh -lc 'python3 -m pip install --break-system-packages -q pytest pytest-asyncio hypothesis fire rich pydantic httpx requests pyyaml python-dotenv filelock fastmcp && python3 -m pip install --break-system-packages -q -r /tmp/requirements.txt' || { echo INSTALL_FAILED; msb stop "$SBX"; exit 1; }

echo "=== verify: test runners + odin deps + full INSTALLED_APPS stack import cleanly ==="
msb exec "$SBX" -- sh -lc 'python3 -c "import pytest, pytest_asyncio, hypothesis, fire, rich, pydantic, httpx, requests, yaml, dotenv, filelock, fastmcp, django, rest_framework, rest_framework_simplejwt.token_blacklist, corsheaders, channels, daphne, celery; print(\"IMPORTS_OK\", \"pytest\", pytest.__version__, \"django\", django.__version__)"; python3 -m pytest --version'

echo "=== stop + re-snapshot odin-agents (overwrite) ==="
msb stop "$SBX"
msb snapshot create -f --from "$SBX" odin-agents
echo "=== EXTEND TEST DEPS DONE ==="
