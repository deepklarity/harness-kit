#!/usr/bin/env bash
# Re-exec under bash if invoked via sh/dash. The documented entry point is
# `sh install.sh`, but the provisioning shared with dev.sh is bash; re-exec'ing
# keeps a single source of truth instead of maintaining a POSIX twin.
if [ -z "${BASH_VERSION:-}" ]; then
    if ! command -v bash >/dev/null 2>&1; then
        echo "install.sh: bash is required but was not found on PATH." >&2
        exit 1
    fi
    exec bash "$0" "$@"
fi
set -euo pipefail

# install.sh — the stranger's path: clone → sh install.sh → doctor → quickstart.
#
# Brings up the kit's dependencies on a fresh machine (macOS or Linux): checks
# prerequisites honestly (naming the exact install command for this OS when one
# is missing), creates an isolated .venv, installs backend + odin + frontend
# deps, migrates SQLite, and seeds the agent users. It then runs `odin doctor`
# so you see exactly which (if any) AI provider still needs authenticating, and
# points you at QUICKSTART.md for the next step.
#
# Idempotent: safe to run repeatedly; each step is a no-op once satisfied, so a
# second run is seconds, not minutes.
#
# This deliberately does NOT set up any AI provider. Doctor names what's
# missing per provider; one provider is enough to proceed.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/taskit/taskit-backend"
FRONTEND_DIR="$ROOT_DIR/taskit/taskit-frontend"
ODIN_DIR="$ROOT_DIR/odin"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[install]${NC} $*"; }
info() { echo -e "${BLUE}[install]${NC} $*"; }
warn() { echo -e "${YELLOW}[install]${NC} $*" >&2; }

# --- detect OS + package manager for honest "install this" hints ------------

detect_os() {
    case "$(uname -s)" in
        Darwin) printf 'mac';;
        Linux)  printf 'linux';;
        *)      printf 'other';;
    esac
}

OS="$(detect_os)"

# print_install_cmd <apt:pkgs> <dnf:pkgs> <brew:pkg> <fallback-blurb>
# Prints (to stderr) the concrete install command for the package manager
# actually present on this host, or a generic fallback when none is known.
print_install_cmd() {
    local apt_pkgs="$1" dnf_pkgs="$2" brew_pkg="$3" fallback="$4"
    if command -v apt-get >/dev/null 2>&1; then
        warn "    sudo apt-get install -y $apt_pkgs"
    elif command -v dnf >/dev/null 2>&1; then
        warn "    sudo dnf install -y $dnf_pkgs"
    elif command -v brew >/dev/null 2>&1; then
        warn "    brew install $brew_pkg"
    else
        warn "    $fallback"
    fi
}

# --- prerequisites (checked honestly; nothing is installed for you) --------

MISSING=0

# git
if ! command -v git >/dev/null 2>&1; then
    warn "git is required but was not found on PATH."
    if [ "$OS" = "mac" ]; then
        warn "    Install the Xcode Command Line Tools:  xcode-select --install"
    else
        print_install_cmd "git" "git" "git" "Install git from https://git-scm.com/"
    fi
    MISSING=1
fi

# python3 >= 3.10 (matches odin's requires-python and Django 5.1)
PY_OK=0
if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY_OK=1
    fi
fi
if [ "$PY_OK" -ne 1 ]; then
    if command -v python3 >/dev/null 2>&1; then
        warn "Python 3.10+ is required (found $(python3 --version 2>&1))."
    else
        warn "Python 3.10+ is required but python3 was not found on PATH."
    fi
    # python3-venv is needed to create the venv below; python3-dev lets pip
    # build any source wheels. brew's python ships venv builtin.
    print_install_cmd "python3 python3-venv python3-dev" "python3 python3-devel" \
        "python@3.12" "Install Python 3.10+ from https://www.python.org/downloads/"
    MISSING=1
fi

# node >= 18 (frontend build + dev server)
NODE_OK=0
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR="$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)"
    if [ -n "${NODE_MAJOR:-}" ] && [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null; then
        NODE_OK=1
    fi
fi
if [ "$NODE_OK" -ne 1 ]; then
    if command -v node >/dev/null 2>&1; then
        warn "Node.js 18+ is required (found $(node --version 2>&1))."
    else
        warn "Node.js 18+ is required but node was not found on PATH."
    fi
    # Debian/Red Hat ship old Node in their base repos, so point at NodeSource
    # rather than suggest a version that's too low.
    if command -v apt-get >/dev/null 2>&1; then
        warn "    Node 18+ needs NodeSource — https://github.com/nodesource/distributions"
    elif command -v dnf >/dev/null 2>&1; then
        warn "    sudo dnf module install nodejs:20   (or use NodeSource for 18+)"
    elif command -v brew >/dev/null 2>&1; then
        warn "    brew install node"
    else
        warn "    Install Node.js 18+ from https://nodejs.org/"
    fi
    MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    warn "One or more prerequisites are missing. Install them with the commands"
    warn "above, then re-run:  sh install.sh"
    exit 1
fi

log "Prerequisites OK ($(python3 --version 2>&1), node $(node --version 2>&1), git $(git --version 2>&1 | cut -d' ' -f3))."

# --- provision (shared with dev.sh so both paths stay identical) ------------
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/provision.sh"
provision_environment

# --- doctor: honest provider picture. No provider is set up here —----------
# doctor names what's missing per provider; one is enough to proceed. Doctor
# exits non-zero when no agent is authenticated yet, which is expected on a
# fresh install, so we don't let it fail the script.
log "Running odin doctor..."
echo ""
odin doctor --fast || true

echo ""
echo -e "${BOLD}Next step:${NC}"
echo -e "  Start the kit with ${BOLD}./dev.sh${NC}, then authenticate one AI provider."
echo -e "  Full walkthrough: ${BLUE}$ROOT_DIR/QUICKSTART.md${NC}"
echo ""
info "Done. Re-run any time — each step is a no-op when already satisfied."
