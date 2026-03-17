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

## Gotchas

- Each clone has its own venv, SQLite db, celery dirs — fully isolated
- First `./dev.sh` in each clone takes ~60s (provisioning), then ~3s
- Don't mix up terminals — check the port in the startup message
- Browser tabs: look at favicon + title prefix to tell them apart
