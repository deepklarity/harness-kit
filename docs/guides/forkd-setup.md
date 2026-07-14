# Forkd Sandbox Setup

Forkd support lets Odin run agent CLIs inside forkd microVM sandboxes. Agents can use allow-all flags inside the VM, while Odin stages only the task workspace and selected auth/config from the host.

This setup is experimental and machine-sensitive. Treat it as a reviewer checklist, not a zero-config install.

## What Forkd Provides

- Per-task microVM execution for supported CLI harnesses.
- Host workspace staged into `/tmp/odin-workspace`.
- Selected credentials/config copied into the guest.
- TaskIt MCP proof/comments through a forkd-local shim.
- Chrome DevTools proof when the selected snapshot includes Chromium.
- Final workspace result, mapped outputs, stdout, and stderr uploaded after the agent exits.
- Live stdout trace upload while the agent runs.

## Prerequisites

- Linux host with sudo access.
- Python, Node/npm, and normal Harness Kit dev dependencies.
- Built or installed forkd CLI and controller.
- A forkd kernel image.
- forkd scripts directory containing `host-tap.sh`.
- Agent CLIs installed on the host: `claude`, `codex`, `gemini`, `opencode`, etc.
- Agent auth already initialized on the host.

Common discovery paths used by `dev.sh`:

```bash
~/forkd-poc/bin/forkd
~/forkd-poc/bin/forkd-controller
~/forkd-poc/vmlinux
~/forkd-poc/forkd/scripts
/usr/local/share/forkd/scripts
/opt/forkd/scripts
```

## Environment

Set these when auto-discovery is not enough:

```bash
export FORKD_BIN="$HOME/forkd-poc/bin/forkd"
export FORKD_CONTROLLER_BIN="$HOME/forkd-poc/bin/forkd-controller"
export FORKD_KERNEL="$HOME/forkd-poc/vmlinux"
export FORKD_SCRIPTS_DIR="$HOME/forkd-poc/forkd/scripts"
export FORKD_CONTROLLER_URL="http://127.0.0.1:8889"
export FORKD_TAP="forkd-tap0"
export FORKD_MEM_SIZE_MIB="4096"
```

Optional switches:

```bash
export FORKD_SETUP_TAP=0          # skip host tap setup
export FORKD_PROVISION_BROWSER=0  # skip browser snapshot provisioning
export FORKD_START_CONTROLLER=0   # skip controller startup
```

`dev.sh` also reads:

```bash
export FORKD_CONTROLLER_BIND="127.0.0.1:8889"
export FORKD_SNAPSHOT_ROOT="/var/lib/forkd/snapshots"
export FORKD_AUDIT_LOG="/tmp/odin-forkd-controller-audit.log"
export FORKD_BROWSER_SNAPSHOT_TAG="odin-node22-4g-cli-browser"
export FORKD_BROWSER_ROOTFS_SIZE_MIB="8192"
export FORKD_IMAGE="node:22-slim"
export FORKD_CACHE_DIR="$HOME/.cache/odin/forkd"
```

## Agent Auth

Configure auth on the host before running forkd tasks:

- Claude: install/login with the normal `claude` CLI flow on the host. Odin stages `~/.claude` and `~/.claude.json` when present, then runs Claude inside the VM with `--dangerously-skip-permissions`.
- Codex: install/login with the normal `codex` CLI flow on the host. Odin stages `~/.codex/auth.json` when present.
- Gemini: run `gemini` interactively once so `~/.gemini` has valid OAuth credentials, or configure API-key auth intentionally. Odin refreshes expiring OAuth on the host before copying credentials into forkd.
- MiniMax: set `MINIMAX_API_KEY` or configure opencode/kilo auth files. Odin stages opencode/kilo auth/config files when present.
- GLM: set `ZAI_API_KEY` or configure opencode auth files. Odin stages opencode auth/config files when present.

Odin copies selected auth/config into the VM. It does not mount the whole home directory, and refreshed/modified credentials inside the VM are not copied back to the host.

## Config

Start from `odin/config/config.sample.yaml` and copy relevant values into your local `.odin/config.yaml` or `~/.odin/config.yaml`.

Minimum forkd fields per sandboxed agent:

```yaml
run_in_forkd: true
forkd_bin: ${FORKD_BIN}
forkd_kernel: ${FORKD_KERNEL}
forkd_scripts_dir: ${FORKD_SCRIPTS_DIR}
forkd_mode: controller
forkd_controller_url: http://127.0.0.1:8889
forkd_snapshot_tag: odin-node22-4g-cli-browser
forkd_mem_size_mib: 4096
forkd_per_child_netns: true
```

Full Odin forkd settings reference:

| Setting | Default | Purpose |
| --- | --- | --- |
| `run_in_forkd` | `false` | Enables forkd wrapping for that agent. |
| `forkd_bin` | auto-discover `forkd` | Path to forkd CLI. |
| `forkd_kernel` | auto-discover common paths | Kernel image used by forkd. Required for forkd runs. |
| `forkd_scripts_dir` | auto-discover common paths | Directory containing forkd helper scripts such as `host-tap.sh`. |
| `forkd_use_sudo` | `false` | Prefixes forkd CLI calls with sudo in CLI mode. Controller startup already uses sudo in `dev.sh`. |
| `forkd_mode` | `cli` | Use `controller` for this branch's expected path. |
| `forkd_controller_url` | `http://127.0.0.1:8889` | Controller API URL. |
| `forkd_snapshot_tag` | derived from image | Snapshot tag to fork. Browser proof expects `odin-node22-4g-cli-browser`. |
| `forkd_per_child_netns` | `true` | Requests per-child network namespaces where supported. |
| `forkd_image` | `node:22-slim` | Base image used when building rootfs/snapshot. |
| `forkd_extra` | `python3`, `ca-certificates`, `git`, `chromium` | Extra packages included in rootfs builds. |
| `forkd_cache_dir` | `~/.cache/odin/forkd` | Rootfs cache directory. |
| `forkd_rootfs_size_mib` | `4096` | Default rootfs size; browser provisioning uses `FORKD_BROWSER_ROOTFS_SIZE_MIB` and defaults to `8192`. |
| `forkd_mem_size_mib` | `4096` | Guest memory size. Keep at least `4096` for browser/CLI tasks. |
| `forkd_tap` | `forkd-tap0` | Host tap device name. Must match setup script/controller use. |
| `forkd_init_git` | `true` | Initializes a baseline git commit inside the staged guest workspace. |
| `forkd_workspace_excludes` | repo defaults | Paths excluded from workspace staging/restoration, including local config/auth directories. |

Recommended per-agent config for first validation:

```yaml
agents:
  gemini:
    cli_command: gemini
    run_in_forkd: true
    forkd_bin: ${FORKD_BIN}
    forkd_kernel: ${FORKD_KERNEL}
    forkd_scripts_dir: ${FORKD_SCRIPTS_DIR}
    forkd_mode: controller
    forkd_controller_url: http://127.0.0.1:8889
    forkd_snapshot_tag: odin-node22-4g-cli-browser
    forkd_mem_size_mib: 4096
    forkd_per_child_netns: true
```

After Gemini works, enable the same forkd block for Claude, Codex, MiniMax, and GLM.

## Agent-Friendly Setup Sequence

Use this exact order on a fresh machine:

1. Clone the Harness Kit branch.
2. Install/build forkd so `forkd`, `forkd-controller`, `vmlinux`, and `forkd/scripts` exist.
3. Export `FORKD_BIN`, `FORKD_CONTROLLER_BIN`, `FORKD_KERNEL`, and `FORKD_SCRIPTS_DIR` if they are not in the common discovery paths.
4. Run host CLI auth for the agents you plan to test.
5. Copy `odin/config/config.sample.yaml` into `.odin/config.yaml` or `~/.odin/config.yaml` and verify the forkd fields.
6. Run `./dev.sh` and enter sudo password when asked.
7. Confirm frontend/backend/controller are up before assigning tasks.
8. Run the smoke tests below in order.

## Startup

Run:

```bash
./dev.sh
```

On a forkd-capable machine, `dev.sh` will:

- create Python/frontend dependencies as usual;
- create the forkd tap if `host-tap.sh` is available and the tap is missing;
- provision the browser rootfs/snapshot unless disabled;
- start `forkd-controller` unless disabled;
- start TaskIt backend, frontend, and Celery.

The browser snapshot provisioning script is:

```bash
scripts/provision_forkd_browser.sh
```

It builds/reuses a Chromium-capable rootfs and creates/registers the default snapshot tag:

```bash
odin-node22-4g-cli-browser
```

## Smoke Tests

Use a real TaskIt board and run these in order:

1. Simple forkd task that edits a small file.
2. Gemini forkd task.
3. TaskIt MCP status/proof comment task.
4. Chrome DevTools task with screenshot proof.
5. Reflection on a completed forkd task.
6. Rerun a completed/failed task.
7. Live trace check during a longer task.
8. Two parallel forkd tasks only after single-task flow is stable.

Focused local tests:

```bash
python3 -m pytest odin/tests/mock/test_forkd_harness.py -q
```

## Troubleshooting

Triage order for agents or humans:

1. Check `git status --short` so local scratch files are not confused with setup issues.
2. Check forkd binaries and kernel paths.
3. Check tap creation and sudo.
4. Check controller health at `$FORKD_CONTROLLER_URL/v1/snapshots`.
5. Check browser snapshot availability if Chrome DevTools is enabled.
6. Check host agent auth.
7. Check TaskIt MCP proof/comment output.
8. Only then debug Odin task logic.

Common failures:

- `forkd-controller not found`: set `FORKD_CONTROLLER_BIN` or install/build forkd.
- `forkd binary/kernel not found`: set `FORKD_BIN` and `FORKD_KERNEL`.
- tap errors: verify sudo, `FORKD_SCRIPTS_DIR/host-tap.sh`, and that `FORKD_TAP` is not already in a bad state.
- browser proof fails preflight: selected snapshot/rootfs does not contain Chromium; rerun browser provisioning.
- controller does not know snapshot: stop the old controller and rerun `./dev.sh` so `/var/lib/forkd/state.json` is reloaded.
- Gemini auth fails: run `gemini` on the host interactively, then retry.
- GLM exits without final `ODIN-STATUS`: forkd now synthesizes success only if the raw trace proves a TaskIt proof comment was successfully created; otherwise the task fails with the raw GLM tail.

## Current Limitations

- This is not yet a clean-machine one-command setup.
- forkd install/build is not vendored by Harness Kit.
- sudo and `/var/lib/forkd` access are required for tap/snapshot/controller workflows.
- Networking and tap behavior can vary by host.
- Browser proof depends on a prebuilt Chromium snapshot.
- Parallel forkd execution should be treated as experimental until validated on the target machine.
