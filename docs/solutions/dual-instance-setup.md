# Dual-Instance Setup (Dogfooding)

Use harness-kit to develop harness-kit. Two separate git clones give fully isolated databases, venvs, celery brokers, and logs.

## Directory Layout

```
harness-kit-stable/    # production instance (main branch)
harness-kit-dev/       # development instance (feature branches)
```

## Initial Setup

```bash
git clone <repo> harness-kit-stable
git clone <repo> harness-kit-dev
```

## Running

```bash
# Terminal 1 — Stable (backend 9100, frontend 9200)
cd harness-kit-stable && ./dev.sh

# Terminal 2 — Dev (auto-offsets to backend 9101, frontend 9201)
cd harness-kit-dev && INSTANCE=dev ./dev.sh
```

`INSTANCE=dev` automatically:
- Offsets ports by 1 (9101/9201) to avoid collisions
- Enables amber/orange theme, `[DEV]` badge, `[DEV]` title prefix, wrench favicon
- Shows instance info in terminal startup message

No `.env` files needed — everything is wired through `dev.sh`.

## Port Map

| Service  | Stable          | Dev             |
|----------|-----------------|-----------------|
| Backend  | localhost:9100  | localhost:9101  |
| Frontend | localhost:9200  | localhost:9201  |

Ports are still overridable: `INSTANCE=dev FRONTEND_PORT=9300 ./dev.sh`

## Visual Markers

- **Stable**: default blue/gray theme, no badge
- **Dev**: amber/orange theme, `[DEV]` badge in header, `[DEV]` title prefix, wrench favicon

## Git Workflow

- **Stable**: stays on `main`, `git pull` only
- **Dev**: feature branches, push PRs from here
- **Sync**: `cd harness-kit-dev && git fetch origin && git rebase origin/main`

## Odin CLI: Stable vs Dev

The TaskIt dual setup above handles the backend/frontend, but Odin (the CLI) needs its own dual install since it's a global binary.

### Problem

`pipx install odin` installs a frozen copy to `~/.local/pipx/venvs/odin/`. Edits to `harness-kit-dev/odin/src/` don't affect the installed binary — you keep running stale code. Only one `odin` binary can exist, so installing dev overwrites stable.

### Solution: pipx `--suffix`

```bash
# Stable (frozen copy, installed from main branch)
cd harness-kit-stable/odin && pipx install .

# Dev (editable, tracks your working tree)
pipx install -e /path/to/harness-kit-dev/odin --suffix='-dev' --force
```

This gives you two separate binaries:

| Binary        | Source                    | Editable? | Use for                    |
|---------------|---------------------------|-----------|----------------------------|
| `odin`        | harness-kit-stable/odin   | No        | Running real specs         |
| `odin-dev`    | harness-kit-dev/odin/src  | Yes       | Testing code changes       |

### Usage

```bash
# Stable — run specs against production TaskIt
cd ~/work/project && odin plan spec.md

# Dev — test your odin code changes
cd ~/work/project && odin-dev plan spec.md
```

Since `odin-dev` is an editable install (`-e`), any code change in `harness-kit-dev/odin/src/` takes effect immediately — no reinstall needed.

### Dogfooding rule: dev instance always uses `odin-dev`

When harness-kit-dev works on itself (dogfooding), **always use `odin-dev`** — never bare `odin`. This applies everywhere:

- **Planning**: `odin-dev plan spec.md` (not `odin plan`)
- **Execution**: `odin-dev exec <task_id>` (not `odin exec`)
- **From git worktrees**: `cd /path/to/worktree && odin-dev exec <task_id>`
- **In CLAUDE.md instructions**: reference `odin-dev` in any dev-instance docs
- **In agent sessions**: if an odin-spawned agent needs to invoke odin commands, it must use `odin-dev`

**Why this matters**: The dev instance runs against the dev TaskIt backend (port 9101). If you accidentally run `odin` (stable), it either hits the wrong backend, runs stale orchestrator code, or both — and the failure looks like a bug in your code when it's just the wrong binary.

**Quick check**: If something fails unexpectedly, look at the traceback path:
- `pipx/venvs/odin/lib/...` → you ran `odin` (stable) by mistake
- `harness-kit-dev/odin/src/...` → you ran `odin-dev` (correct)

### Updating

```bash
# Update stable to latest main
cd harness-kit-stable && git pull && cd odin && pipx install . --force

# Dev tracks your working tree automatically (editable install)
# Just edit code — odin-dev picks it up
```

## Gotchas

- Each clone has its own venv, SQLite db, celery dirs — fully isolated
- First `./dev.sh` in each clone takes ~60s (provisioning), then ~3s
- Don't mix up terminals — check the port in the startup message
- Browser tabs: look at favicon + title prefix to tell them apart
- **Odin CLI**: check `which odin` vs `which odin-dev` — running the wrong binary after a code fix is the #1 confusion source
