# Plan: Sandbox Execution for Odin

**Created:** 2026-04-08
**Status:** Phase 0-1 re-evaluated and verified. Phase 2 partial (Docker fallback works, OpenSandbox SDK path + harness-in-sandbox untested).
**Integration point:** `orchestrator.py:3099` — between `build_execute_command()` and execution
**Technology:** OpenSandbox SDK (https://github.com/alibaba/OpenSandbox) with raw Docker fallback

## Problem Statement

Odin harnesses run as trusted subprocesses with full host access. This means:
- A harness can read `~/.ssh/`, `~/Documents/`, or any file on the machine
- Parallel tasks share Chrome state (profile dirs, lock files, debug ports)
- Permission-bypass flags (`--dangerously-skip-permissions`, `--yolo`, `--full-auto`) have real blast radius

We want: configurable per-task sandboxing that restricts the harness to its worktree + credentials, with per-sandbox Chrome, while keeping worktrees on host disk for IDE access and debugging.

## Architecture Decision

**OpenSandbox SDK** as the primary sandbox runtime, with raw Docker as fallback.

OpenSandbox provides a standardized, programmatic API for sandbox lifecycle management:
- `Sandbox.create()` — create isolated containers with volumes, env, resource limits
- `sandbox.commands.run()` — execute commands with streaming stdout/stderr
- `sandbox.kill()` — cleanup
- Network policies, egress control, resource limits — all via API
- Python async/await SDK — native integration with odin's async orchestrator

**Two execution paths:**
1. **Primary (OpenSandbox):** `create_sandbox()` → `execute_in_sandbox()` → `destroy_sandbox()`
   - Uses OpenSandbox SDK for full lifecycle management
   - Requires `opensandbox-server` running locally
2. **Fallback (raw Docker):** `wrap_command()` produces a `docker run` command for tmux
   - Used when OpenSandbox server is not available
   - Same isolation guarantees, less programmatic control

## Philosophy Alignment

| Tenet | How |
|-------|-----|
| #1 Determinism | Same task produces same result with or without sandbox |
| #8 Good Enough | Raw Docker, not a platform. ~200 lines of new code. |
| #10 Questions to Human | Docker missing? Fail loudly, don't silently fall back |
| #11 First Principles | Config flag, not pipeline stage. Like `worktree_enabled`. |
| #13 Human-First Legibility | Worktree on host, bind-mounted in. `cd` + IDE = unchanged. |
| #14 Platform Agnostic | Docker on macOS, Linux, Windows/WSL |
| #16 Modular Composition | `sandbox.py` is a module. Harness doesn't know about it. |

## Credential Strategy

OAuth tokens (Claude `~/.claude/`, Codex `~/.codex/`, etc.) are **copied into the container at startup** (not mounted). This is a one-way door:
- Container can read and refresh tokens
- Refreshed tokens are ephemeral (lost when container exits)
- Host credentials are never modified by the sandbox
- Each harness declares its credential paths via `credential_paths()` on `BaseHarness`

## Harness Credential Map

| Harness | Registered Name | CLI Binary | Credential Path | CLI-Based? |
|---------|-----------------|-----------|-----------------|-----------|
| Claude | `claude` | `claude` | `~/.claude/` | Yes |
| Codex | `codex` | `codex` | `~/.codex/` | Yes |
| Gemini | `gemini` | `gemini` | `~/.gemini/`, `~/.config/google-cloud/` | Yes |
| GLM | `glm` | `opencode` | `~/.opencode/` | Yes |
| Qwen | `qwen` | `qwen` | `~/.qwen/` | Yes |
| MiniMax | `minimax` | `kilo` | `~/.kilo/`, `~/.kilocode/` | Yes |
| Mock | `mock` | N/A | N/A | No (API, skipped) |

## Phases

---

### Phase 0: Test Infrastructure  [DONE]
> Skill: `/hk-test-creator` — re-evaluated from scratch. Deletion test applied to every test.

#### Verification
```
command: python -m pytest tests/sandbox/unit/ -v
output: 38 passed — one file, zero mocks, every test breaks if its function is deleted
command: python -m pytest tests/sandbox/docker/ -v
output: 15 passed — real Docker containers, fails (not skips) without Docker
        (added file-level credential staging + multi-file same-parent tests)
date: 2026-04-09
```

**Goal:** Write all tests before any implementation exists. Tests define the contract.

**Test categories:**

#### A. Unit tests (`tests/unit/test_sandbox.py`)

Static, no Docker, no I/O. Test the `wrap_command()` function and `SandboxConfig` model.

| # | Scenario | What to assert |
|---|----------|---------------|
| 1 | `wrap_command()` produces correct `docker run` for Claude harness | Command list contains: `docker run --rm --init --cap-drop ALL --security-opt no-new-privileges`, worktree bind mount, log dir bind mount, correct image, original command preserved at end |
| 2 | `wrap_command()` produces correct mounts for each harness type | Each harness's credential paths appear as mounts in the output command |
| 3 | `wrap_command()` with host networking | `--network=host` in command |
| 4 | `wrap_command()` with bridge networking | `--network=bridge` in command, `--add-host=host.docker.internal:host-gateway` present |
| 5 | `wrap_command()` rewrites TASKIT_URL for bridge mode | `localhost:8000` becomes `host.docker.internal:8000` in env vars |
| 6 | `wrap_command()` does NOT rewrite TASKIT_URL for host mode | `localhost:8000` stays as-is |
| 7 | Credential paths are per-harness | Claude gets `~/.claude/`, Codex gets `~/.codex/`, etc. |
| 8 | Empty credential paths (Mock harness) | No credential mounts in output |
| 9 | `SandboxConfig` defaults | `enabled=False`, `image="odin-sandbox:latest"`, `network="host"` |
| 10 | `SandboxConfig` merges into `OdinConfig` | `sandbox_enabled` field exists, default False, serializes to/from YAML |
| 11 | `is_available()` returns False when Docker not on PATH | Mock `shutil.which("docker")` to return None |
| 12 | `is_available()` returns True when Docker present | Mock `shutil.which("docker")` to return path |
| 13 | Working directory inside container is `/workspace` | `--workdir /workspace` in command |
| 14 | Log dir mount target is `/logs` | Bind mount maps host log_dir to `/logs` |
| 15 | Original command is appended verbatim | The harness command list appears at the end of docker run, unmodified |
| 16 | Extra mounts from config | `sandbox_extra_mounts` config items appear as additional `--mount` flags |

#### B. Unit tests: orchestrator integration (`tests/unit/test_sandbox_orchestrator.py`)

Test that the orchestrator calls `wrap_command()` at the right time.

| # | Scenario | What to assert |
|---|----------|---------------|
| 17 | `sandbox_enabled=True` wraps the command | The command passed to `_execute_via_tmux` starts with `docker run` |
| 18 | `sandbox_enabled=False` does NOT wrap | The command passed to `_execute_via_tmux` is the original harness command |
| 19 | API harness (Mock) is never sandboxed | When `build_execute_command()` returns None, no sandbox wrapping happens |
| 20 | Sandbox wrapping preserves working_dir for tmux | tmux `working_dir` is the host worktree path (not `/workspace`) |
| 21 | Sandbox failure (Docker not available) raises clear error | When `is_available()` is False and `sandbox_enabled=True`, error message says "Docker not found" |

#### C. Mock tests (`tests/mock/test_sandbox_execution.py`)

Mocked subprocess — verify the full pipeline from `_execute_task` through sandbox wrapping to result parsing.

| # | Scenario | What to assert |
|---|----------|---------------|
| 22 | Sandboxed Claude task produces identical `TaskResult` to unsandboxed | Same `success`, `output`, `error`, `duration_ms` structure |
| 23 | Sandboxed task output is captured to host log files | Output file and trace file on host disk contain expected content |
| 24 | Sandboxed task with exit code 0 → success=True | Result parsing unchanged by sandbox wrapping |
| 25 | Sandboxed task with exit code 1 → success=False | Error handling unchanged by sandbox wrapping |
| 26 | Sandboxed task timeout → timeout error | Timeout propagates through Docker correctly |
| 27 | MCP config path is remapped to container path | `--mcp-config` flag points to `/logs/mcp_<task_id>.json` (container path) |
| 28 | MCP env vars are passed through to container | `-e TASKIT_URL=... -e TASKIT_AUTH_TOKEN=...` in docker command |

#### D. Error case tests (`tests/unit/test_sandbox_errors.py`)

| # | Scenario | What to assert |
|---|----------|---------------|
| 29 | Worktree path doesn't exist | Clear error before Docker is invoked |
| 30 | Credential path doesn't exist (e.g., user never logged into Gemini) | Skips that mount gracefully, logs a warning |
| 31 | Docker image not built yet | Error message includes "Run `odin sandbox build`" |
| 32 | Docker daemon not running | Error distinguishes "docker installed but not running" from "docker not found" |
| 33 | Container OOM kill | Error message translated from Docker exit code 137 to "Container ran out of memory" |

#### E. Credential copy tests (`tests/unit/test_sandbox_credentials.py`)

| # | Scenario | What to assert |
|---|----------|---------------|
| 34 | Credentials are copied to temp dir, not mounted from home | No `--mount` referencing `~/.claude/` directly; instead, temp dir path used |
| 35 | Temp dir is cleaned up after container exits | Cleanup function registered, temp path removed |
| 36 | Credential copy is a snapshot (modifying host creds after copy has no effect) | Conceptual — assert the copy logic uses `shutil.copytree`, not symlinks |
| 37 | Multiple harness credential dirs are all copied | Claude + Codex creds both present in temp dir if task is reassigned |

---

### Phase 1: Core Implementation  [DONE]
> Implementation passes all re-evaluated tests without modification.

#### Verification
```
command: python -m pytest tests/ --tb=line
output: 890 passed, 7 failed (pre-existing, unrelated). Zero regressions.
command: python -m pytest tests/sandbox/docker/ -v
output: 13 passed — wrap_command produces commands that execute correctly in Docker,
        bind mounts work bidirectionally, credential staging works end-to-end in real containers
date: 2026-04-09
```

**Goal:** Implement `sandbox.py`, add `credential_paths()` to harnesses, add config fields. All Phase 0 unit/mock tests pass.
**Result:** 73/73 tests passing. 831 existing tests unaffected (7 pre-existing failures unchanged).

**Files to create/modify:**

| File | Change |
|------|--------|
| `src/odin/sandbox.py` | **New.** `SandboxConfig`, `wrap_command()`, `is_available()`, `prepare_credentials()`, `cleanup_credentials()` |
| `src/odin/models.py` | Add `sandbox_enabled`, `sandbox_image`, `sandbox_network`, `sandbox_extra_mounts` to `OdinConfig` |
| `src/odin/harnesses/base.py` | Add `credential_paths() -> list[str]` with default `[]` |
| `src/odin/harnesses/claude.py` | Override `credential_paths()` → `["~/.claude"]` |
| `src/odin/harnesses/codex.py` | Override `credential_paths()` → `["~/.codex"]` |
| `src/odin/harnesses/gemini.py` | Override `credential_paths()` → `["~/.gemini"]` |
| `src/odin/harnesses/qwen.py` | Override `credential_paths()` → `["~/.qwen"]` |
| `src/odin/harnesses/glm.py` | Override `credential_paths()` → `["~/.opencode"]` |
| `src/odin/harnesses/minimax.py` | Override `credential_paths()` → `["~/.kilo", "~/.kilocode"]` |
| `src/odin/orchestrator.py` | 5-line insertion at line ~3099: if sandbox_enabled, call `wrap_command()` |

**NOT in this phase:** Dockerfile, CLI subcommand, UI settings. Only the core module and wiring.

**Verification:**
```bash
cd odin/
python -m pytest tests/unit/test_sandbox.py tests/unit/test_sandbox_orchestrator.py tests/unit/test_sandbox_errors.py tests/unit/test_sandbox_credentials.py tests/mock/test_sandbox_execution.py -v
```

All tests green = Phase 1 complete.

---

### Phase 2: Container Image & Manual Validation  [PARTIAL]
> Skill: `/hk-test-creator` for any new tests
> Skill: `/hk-rca` if harness fails inside sandbox

**Done:**
- Dockerfile built and image working (`odin sandbox build`)
- CLI subcommands (`odin sandbox {status,setup,build,test}`)
- Docker tests passing (13 tests against real containers)
- `opensandbox` SDK installed, API signatures verified and fixed against real package
- `sandbox.py` fixed: `Volume`/`Host` SDK types, direct mode (not server proxy), `skip_health_check=True`
- Full SDK lifecycle verified: `create_sandbox` → `execute_in_sandbox` → `destroy_sandbox` against real server
- Volume mounts verified: host→container read, container→host write, output file streaming

**Harness execution verified:**
- All 6 CLIs installed in image (claude, gemini, codex, qwen, kilo, opencode)
- Gemini harness runs inside sandbox, calls real API, returns stream-json output (exit=0, 11s, 7184 tokens)
- Credential mounting fixed: per-file auth (oauth_creds.json, settings.json), not entire config dir
- Staging dir is writable (for token refresh / state), host originals untouched
- Output streams to host file via SDK handlers

**Harness-in-sandbox checklist:**
- [x] gemini — exit=0, poem output, 4.3s. File-based auth (oauth_creds.json).
- [x] codex — exit=0, poem output. File-based auth (auth.json).
- [x] claude — exit=0, poem output, 5.9s. Env-based auth (CLAUDE_CODE_OAUTH_TOKEN via `odin sandbox setup` → `claude setup-token`).
- [x] qwen — exit=0, poem output, 4.7s. File-based auth (oauth_creds.json). Previous failure was expired token, not a sandbox issue.
- [x] kilo (minimax) — exit=0, poem output, 13.3s. File-based auth (~/.config/kilo/).
- [x] opencode (glm) — exit=0, poem output, 4.9s. File-based auth (~/.config/opencode/).

**Not done:**
- Output parity test (sandboxed vs unsandboxed TaskResult comparison) — belongs in e2e tests (Phase 2C, test #49), requires running a real harness both ways

**macOS Docker Desktop notes:**
- `DOCKER_HOST=unix:///Users/<user>/.docker/run/docker.sock` required (no `/var/run/docker.sock` by default)
- `use_server_proxy=False` (direct mode) required — proxy mode times out on Docker Desktop
- `skip_health_check=True` required — health check ping fails through proxy
- Bridge networking works; host networking silently ignored on Docker Desktop
- `allowed_host_paths` in server config must include paths used for worktrees/logs

#### Verification
```
command: python3 -c "from odin.sandbox import create_sandbox, execute_in_sandbox, destroy_sandbox..."
output: Sandbox created, cat /workspace/hello.txt → 'hello from host',
        container write → visible on host, output file on host disk
date: 2026-04-09
```

**Goal:** Build a real Docker image, run real harnesses inside it, verify output parity with unsandboxed execution. This is the phase the user runs manually.

#### 2A: Dockerfile

**New file:** `odin/sandbox/Dockerfile`

```dockerfile
FROM debian:bookworm-slim
# Node.js (for MCP servers, chrome-devtools-mcp)
# Python 3.11+ (for taskit-mcp)
# Git (for worktree operations inside container)
# Chrome headless (for chrome-devtools-mcp)
# NOTE: Harness CLIs are NOT baked in — mounted from host at runtime
```

**New file:** `odin/sandbox/entrypoint.sh`

Copies credentials from mounted temp dir to container home, then exec's the harness command.

**CLI mount strategy:** Rather than baking CLIs into the image (maintenance burden), mount host binaries:
```
--mount type=bind,src=$(which claude),dst=/usr/local/bin/claude,readonly
```
For Node.js-based CLIs (gemini, kilo, opencode), mount the entire install directory.

#### 2B: Build command

```bash
odin sandbox build    # builds odin-sandbox:latest from odin/sandbox/Dockerfile
odin sandbox status   # shows image version, Docker status, disk usage
```

#### 2C: Manual validation scripts

**New file:** `odin/tests/integration/test_sandbox_live.py`

These are the tests the user runs manually to validate real sandbox behavior. Marked with `@pytest.mark.sandbox` (excluded from default pytest runs).

| # | Test | Manual equivalent | What to verify |
|---|------|-------------------|----------------|
| 38 | Sandbox smoke test | `odin sandbox test` | Container starts, runs `echo hello`, exits 0 |
| 39 | Claude in sandbox — hello world | `odin exec <task_id> --sandbox` | Claude produces output, output file on host matches, task succeeds |
| 40 | Gemini in sandbox — hello world | Same with gemini agent | Same verification |
| 41 | Codex in sandbox — hello world | Same with codex agent | Same verification |
| 42 | Qwen in sandbox — hello world | Same with qwen agent | Same verification |
| 43 | All credential dirs mounted | Inspect running container | `ls /home/user/.claude/` shows token files |
| 44 | Worktree visible in container | Inspect running container | `/workspace/` contains expected files |
| 45 | Worktree changes visible on host | Create file in container /workspace | File appears in host worktree immediately |
| 46 | Log files on host | After task completes | `.odin/logs/task_<id>.out` and `.trace.jsonl` exist and contain output |
| 47 | MCP taskit works in sandbox | Task posts a status comment | Comment appears on TaskIt dashboard |
| 48 | Chrome isolation | Two parallel sandboxed tasks with Chrome | Both complete without lock errors |
| 49 | Output parity | Run same spec sandboxed and unsandboxed | TaskResult fields are structurally identical (success, output format, error format) |
| 50 | Sandbox flag in config | Set `sandbox_enabled: true` in `.odin/config.yaml` | All subsequent `odin exec` runs use sandbox |
| 51 | Sandbox flag on CLI | `odin exec <task_id> --sandbox` | Single task runs in sandbox, others don't |

**Manual CLI commands the user runs themselves:**

```bash
# Prerequisites
cd odin/
pip install -e ".[dev]"

# 1. Setup OpenSandbox (installs SDK + server + init config)
odin sandbox setup

# 2. Build the sandbox image
odin sandbox build

# 3. Start OpenSandbox server (in a separate terminal)
opensandbox-server

# 4. Check everything is ready
odin sandbox status
# Expected: all green checks

# 5. Smoke test
odin sandbox test
# Expected: "Sandbox OK — OpenSandbox created sandbox, ran echo, destroyed."

# 6. Run a single task sandboxed (from temp_test_dir/)
cd temp_test_dir/
# Add to .odin/config.yaml: sandbox_enabled: true
odin plan ../sample_specs/mini_spec.md --auto
odin exec <task_id>
# Expected: task completes, output in .odin/logs/, worktree has changes

# 7. Verify output parity — run same task without sandbox
# Set sandbox_enabled: false in config
odin exec <other_task_id>
# Expected: same output structure, same TaskResult shape

# 8. Verify Chrome isolation — run two tasks in parallel with sandbox
odin exec <task_a> &
odin exec <task_b> &
wait
# Expected: both complete, no Chrome lock errors

# 9. Verify host filesystem transparency
ls .odin/worktrees/<spec>/<task>/
# Expected: files written by sandboxed harness are visible on host

# 10. Error cases
# Stop OpenSandbox server, then:
odin exec <task_id>  # with sandbox_enabled: true
# Expected: falls back to raw Docker wrapping with warning

# Stop Docker too, then:
odin exec <task_id>  # with sandbox_enabled: true
# Expected: clear error about Docker not running

# 11. Fallback test (no OpenSandbox server, Docker only)
# Stop opensandbox-server, keep Docker running
odin sandbox test
# Expected: "Sandbox OK (Docker fallback)"
```

**Verification for Phase 2:**
```bash
# Automated integration tests (requires Docker + at least one harness CLI)
python -m pytest tests/integration/test_sandbox_live.py -v -m sandbox

# Manual verification (user runs these themselves)
# Follow the CLI commands above
```

Phase 2 is complete when: user has personally run sandboxed tasks with at least 2 different harnesses, verified output parity, verified Chrome isolation, and confirmed error messages are clear.

---

### Phase 3: Wire End-to-End  [NOT STARTED]
> Skill: `/hk-test-creator` — map execution paths through Celery/CLI before writing tests

**Goal:** Integrate sandbox into the full Odin workflow — config file, Celery executor, spec-level sandbox toggle.

#### Verification
```
(empty — phase not started)
```

| Change | Detail |
|--------|--------|
| `odin exec --sandbox` CLI flag | Overrides config for single execution |
| `odin exec --no-sandbox` CLI flag | Disables sandbox even if config says enabled |
| Config file support | `sandbox_enabled: true` in `.odin/config.yaml` applies to all tasks |
| Celery DAG executor | `dag_executor.py` passes sandbox flag through to `exec_task()` |
| Spec-level override | `sandbox: true` in spec YAML enables for all tasks in spec |
| Resource limits | `--memory` flag on Docker, configurable via `sandbox_memory_limit` |
| `odin sandbox shell <task_id>` | Opens shell in running task's container |

**Tests for Phase 3:** (`tests/mock/test_sandbox_workflow.py`)

| # | Scenario |
|---|----------|
| 52 | CLI `--sandbox` flag overrides config `sandbox_enabled: false` |
| 53 | CLI `--no-sandbox` flag overrides config `sandbox_enabled: true` |
| 54 | Spec-level `sandbox: true` enables for all tasks in spec |
| 55 | Celery executor passes sandbox flag correctly |
| 56 | Memory limit appears in Docker command when configured |
| 57 | `odin sandbox shell` finds the right container by task_id |

---

### Phase 4: UI Integration

**Goal:** Sandbox toggle on TaskIt dashboard.

| Change | Detail |
|--------|--------|
| Board-level setting | `sandbox_enabled` on board model |
| Task-level indicator | Badge showing "sandboxed" on task card |
| Spec creation UI | Checkbox for "Run in sandbox" |
| Sandbox status in task detail | Shows container ID, image version, mount paths |

This phase is backend (Django model + serializer + API) + frontend (React component). Follows existing board settings pattern.

---

### Phase 5: Hardening (Optional, Based on Need)

| Change | When to do it |
|--------|---------------|
| Bridge networking with egress allowlist | When network isolation is actually needed |
| Per-task scoped auth tokens | When running untrusted specs |
| Sandbox image auto-update check | When image staleness causes real problems |
| Resource benchmarking (4 concurrent sandboxes) | Before recommending sandbox for daily use |
| `odin sandbox prune` — cleanup dangling containers/images | When disk usage becomes a problem |

---

## Test Layout

```
tests/sandbox/
  unit/                                 ← pure logic, zero mocks, no external deps (38 tests)
    test_config_and_wrapping.py         # wrap_command, remap_command_paths, _shell_quote, exit codes,
                                        # harness credential paths (parametrized)
  docker/                               ← needs Docker daemon + image built, FAILS if missing (15 tests)
    conftest.py                         # session fixtures that fail (not skip) without Docker
    test_docker.py                      # container lifecycle, bind mounts, credentials, env vars,
                                        # wrap_command real execution
  e2e/                                  ← needs OpenSandbox server + harness CLIs
    (empty — Phase 2 completion)        # create_sandbox → execute_in_sandbox, harness inside sandbox,
                                        # output parity
```

Run commands:
```bash
python -m pytest tests/sandbox/unit/ -v             # no deps, instant
python -m pytest tests/sandbox/docker/ -v -m sandbox # needs Docker + image
python -m pytest tests/sandbox/e2e/ -v -m sandbox   # needs everything
```

## File Map

```
odin/
  src/odin/
    sandbox.py                          # Core module: OpenSandbox SDK + Docker fallback
    models.py                           # OdinConfig sandbox fields
    orchestrator.py                     # Two-tier sandbox integration
    cli.py                              # odin sandbox {status,setup,build,test}
    harnesses/
      base.py                           # credential_paths() default
      claude.py                         # credential_paths() → ~/.claude
      codex.py                          # credential_paths() → ~/.codex
      gemini.py                         # credential_paths() → ~/.gemini
      qwen.py                           # credential_paths() → ~/.qwen
      glm.py                            # credential_paths() → ~/.opencode
      minimax.py                        # credential_paths() → ~/.kilo, ~/.kilocode
  sandbox/
    Dockerfile                          # Container image
```

## Resumption Notes

To continue this work in a new session:
1. Read this file first — check phase statuses and verification blocks
2. Empty verification block = phase not done, regardless of what the status says
3. Use `/hk-test-creator` before writing any new tests
4. Use `/hk-rca` if anything produces unexpected results
5. Before marking a phase done: "Can the user run a command and see it work?"

## Open Questions

1. **CLI binary mounting vs baking into image** — For Node.js CLIs (gemini, kilo, opencode), mounting from host may not work cleanly (need entire node_modules). May need to bake these into the image after all. Decide during Phase 2 based on empirical testing.
2. **macOS Docker filesystem performance** — Bind-mount performance on macOS Docker (VirtioFS) may be slow for large worktrees. Measure during Phase 2.
3. **Container reuse** — Should we keep containers alive between tasks for faster startup? Current plan: ephemeral (new container per task). Revisit if startup overhead is a problem.
4. **Chrome inside container on ARM64** — Chrome headless on ARM64 Linux inside Docker on Apple Silicon needs verification. May need Chromium instead of Chrome.
