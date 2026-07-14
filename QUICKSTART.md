# QUICKSTART

Ten-minute path from a fresh clone to a merged sample task, using whatever
single AI provider you have. No kit-specific knowledge assumed.

> This is the short version. For the full guided tour (boards, specs, the UI),
> see [`docs/walkthrough.md`](docs/walkthrough.md).

## What you'll do

1. Start the kit (`./dev.sh` brings up backend + UI + workers).
2. Check your provider (`odin doctor`).
3. Run one sample task (a loader picks your provider automatically and runs a
   tiny spec end-to-end: plan → sandbox → review → merge).

---

## Prerequisites

| Need | Version | Notes |
|---|---|---|
| Git | any | |
| Python | **3.10+** | `python3 --version`. dev.sh creates an isolated `.venv/` — it does **not** touch your system Python. |
| Node.js | **18+** | for the dashboard UI. `node --version` |
| **One** AI provider | — | installed **and** authenticated. The sample runs on whichever one you have. |

You need exactly one provider ready. Pick whichever you already use:

| Provider | CLI on PATH | Authenticate with |
|---|---|---|
| **claude** | `claude` | `claude setup-token` (or set `CLAUDE_CODE_OAUTH_TOKEN`) |
| **codex** | `codex` | `codex login` (writes `~/.codex/auth.json`) |
| **glm** | `opencode` | `export ZAI_API_KEY=...` (or `opencode login`) |
| **minimax** | `opencode` | `export MINIMAX_API_KEY=...` |
| **agy** | `agy` | `export AGY_SECRET=...` (or `agy login`) |

> **Sandbox note.** Full task execution runs agents inside a microsandbox VM
> (`msb` — HVF on macOS, KVM on Linux). If `msb` isn't installed, `odin doctor`
> WARNs on the sandbox row — planning still works, but execution waits until
> the sandbox is available. `odin doctor` shows the install command. Platform
> notes and known gaps live in
> [`docs/guides/forkd-setup.md`](docs/guides/forkd-setup.md).

---

## Step 1 — Start the kit

```bash
git clone https://github.com/deepklarity/harness-kit.git
cd harness-kit
./dev.sh
```

The first run takes ~60s: it creates `.venv/`, installs backend + odin + frontend
deps, migrates SQLite, and starts three services (backend `:9100`, dashboard
`:9200`, Celery workers). **Leave this terminal running** — `dev.sh` supervises
the services; `Ctrl-C` stops them.

> On a machine with externally-managed Python (Debian/Ubuntu, PEP 668) you may
> see a pip error here. dev.sh installs into its own `.venv/`, so this should
> not happen — if it does, ensure `python3-venv` is installed
> (`apt install python3-venv`).

---

## Step 2 — Get the `odin` CLI and check your provider

Open a **second terminal** in the repo root, activate the venv dev.sh created,
and run doctor:

```bash
source .venv/bin/activate
odin doctor
```

You should see at least one row under **`[agents]`** marked `✓` (installed +
authenticated) and a final `RESULT: OK`. If every agent is `!` (warn), authenticate
one provider from the table above, then re-run `odin doctor`.

`odin doctor` exits `0` when at least one provider is usable, `1` when none are.

---

## Step 3 — Run the sample

```bash
python3 odin/sample_specs/quickstart/run.py
```

This loader:

1. asks `odin doctor` which providers are available (reusing doctor's probe —
   no hardcoded claude/glm assumption),
2. picks the best one (cheapest-capable first, per the kit's own routing), and
3. runs the sample spec with `odin plan ... --base-agent <picked>`.

The sample spec (`odin/sample_specs/quickstart/quickstart_spec.md`) adds a
`--verbose` flag to a tiny `toy.py`. With the TaskIt backend up (Step 1),
`--auto` auto-queues the task; the Celery DAG executor then runs it through
**dispatch → sandbox → review → merge**.

Preview the pick without running anything:

```bash
python3 odin/sample_specs/quickstart/run.py --dry-run
```

---

## Watch it run

```bash
odin watch                 # live task status in the terminal
```

Or open the dashboard: **http://localhost:9200**

When the task reaches **done**, the `--verbose` change is merged into the spec
branch. Verify the result:

```bash
python3 odin/sample_specs/quickstart/toy.py --verbose
```

You should see each step logged to stderr (`[step] load`, ...) and `done: <step>`
on stdout.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `RESULT: FAIL — no agent CLI is installed AND authenticated` | Authenticate one provider (table above), then `odin doctor` again. |
| `odin: command not found` | You skipped `source .venv/bin/activate` in the current terminal. |
| `RESULT: FAIL` even though a provider is auth'd | Run `odin doctor` (no `--json`) and read the `fix:` lines. |
| Tasks stay queued, never execute | Sandbox (`msb`) missing or services down — check `odin doctor`'s sandbox/services rows; ensure `./dev.sh` is still running in terminal 1. |
| Port already in use | Stop other instances: `./dev.sh` traps `Ctrl-C`; or `lsof -i :9100` / `:9200`. |

If a service misbehaves, `tail -20 logs/backend.log` (or `frontend.log`, `celery.log`) usually names the cause.

---

## Next

- Write your own spec: `odin plan my_spec.md --auto` (see `odin/sample_specs/` for examples).
- Full UI + spec tour: [`docs/walkthrough.md`](docs/walkthrough.md).
