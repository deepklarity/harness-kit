# Sandbox Phase 3: Production Fix Plan

**Date:** 2026-04-09
**Branch:** feat/sandbox
**Status:** Ready for execution
**Prerequisite:** Read `odin/docs/philosophy.md` (done — tenets #4 Proof of Work, #11 First Principles, #16 Modular Composition govern this work)

## Problem Statement

Sandbox execution works mechanically (GLM task 86 reached REVIEW) but the path is fragile. Five issues need isolated fixes before E2E is reliable. This plan addresses them in dependency order with one change per step.

## Architecture Decision: Hybrid SDK

**Decision:** Use OpenSandbox SDK for lifecycle (create/destroy/resource limits) but use `docker exec` for command execution instead of `sandbox.commands.run()`.

**Why:**
- SDK `Sandbox.create()` gives us resource limits, volume management, metadata — valuable
- SDK `sandbox.commands.run()` with SSE streaming is the source of most bugs (hangs, output loss, background mode quirks)
- `docker exec` is battle-tested, synchronous, and gives us direct stdout/stderr
- The file-based wrapper script approach already works — `docker exec` just replaces the SDK's `commands.run()` call

**What changes:**
- `execute_in_sandbox()` calls `docker exec <container_id> bash /logs/sandbox_run.sh` instead of `sandbox.commands.run()`
- Container ID is available from `sandbox.id` after `Sandbox.create()`
- Everything else (wrapper script, exit marker polling, bind mounts) stays identical

---

## Phases

### Phase 1: Fix Silent Error Masking (sandbox.py)
**Files:** `odin/src/odin/sandbox.py`
**Risk:** Low — isolated change, no behavior change on happy path
**Tenet:** #4 Proof of Work — a masked exit code is anti-proof

**What:**
- Lines 422-425: Change `except (ValueError, OSError): exit_code = 0` to `exit_code = 1` (failure, not success)
- Add a warning log when exit marker is unreadable
- If exit marker file doesn't exist after polling completes, that's also exit_code = 1

**Verification:**
```bash
cd odin && python -m pytest tests/sandbox/ -v
```

---

### Phase 2: Hybrid Execution — Replace SDK commands.run() with docker exec
**Files:** `odin/src/odin/sandbox.py`
**Risk:** Medium — changes the execution path, but wrapper script + polling are unchanged
**Tenet:** #16 Modular Composition — SDK for lifecycle, Docker for execution

**What:**
1. In `create_sandbox()`: capture and store the container ID from `sandbox.id`
2. In `execute_in_sandbox()`: replace `sandbox.commands.run(RunCommandOpts(...))` with:
   ```python
   proc = await asyncio.create_subprocess_exec(
       "docker", "exec", "-d", container_id, "bash", "/logs/sandbox_run.sh",
       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
   )
   ```
3. Rest of function (exit marker polling, output reading) stays identical
4. Remove the `from opensandbox import RunCommandOpts` import

**Why `-d` (detached):** The wrapper script handles output via `tee` to bind-mounted files. We don't need docker exec's stdout — we read from host files. Detached mode returns immediately, matching the current `background=True` behavior.

**Verification:**
```bash
# Unit: existing tests still pass
cd odin && python -m pytest tests/sandbox/ -v

# Manual: run sdk_test.py with echo and file tests
cd odin && python sandbox/testing_tools/sdk_test.py --echo --file
```

---

### Phase 3: MCP Environment Propagation Diagnostic
**Files:** `odin/src/odin/sandbox.py`, `odin/src/odin/mcps/taskit_mcp/config.py`
**Risk:** Low — diagnostic first, then targeted fix
**Tenet:** Methodical Problem-Solving — observe before acting

**What:**
The wrapper script already dumps `env | sort > /logs/sandbox_env.txt` (line ~374). After Phase 2's docker exec change:

1. Run `odin exec <task> --sandbox` on a test task
2. Check `.odin/logs/sandbox_env.txt` for `TASKIT_URL`
3. **If TASKIT_URL is present:** The issue is how the opencode/kilo CLI passes env to MCP child processes. Fix: inject `TASKIT_URL` directly into the MCP server command args (e.g., `env TASKIT_URL=... taskit-mcp`) instead of relying on the CLI's `"environment"` config block
4. **If TASKIT_URL is absent:** The SDK/docker exec isn't propagating env vars from `create_sandbox()`. Fix: pass env vars explicitly via `docker exec -e TASKIT_URL=... <container_id> bash /logs/sandbox_run.sh`

**Concrete fix (likely path — CLI doesn't propagate "environment" to MCP children):**

In `_server_entry_opencode()` (config.py ~line 140), change from:
```python
{"type": "local", "command": ["taskit-mcp"], "environment": env}
```
To wrapping the command with explicit env:
```python
{"type": "local", "command": ["env", f"TASKIT_URL={env['TASKIT_URL']}", ..., "taskit-mcp"]}
```

This makes the env vars part of the command itself, not dependent on the CLI's env propagation.

**Verification:**
```bash
# Run sandbox with a task that calls taskit_add_comment
odin exec <task_id> --sandbox
# Check logs for MCP errors
grep -i "nodename\|dns\|resolve" .odin/logs/trace_*.jsonl
```

---

### Phase 4: Remove Dead Code + Cleanup
**Files:** `odin/src/odin/sandbox.py`
**Risk:** Low — removing unused code
**Tenet:** #12 No Slop

**What:**
1. Remove `wrap_command()` (lines 498-550) — never called, superseded by SDK lifecycle + docker exec
2. Remove `_shell_quote()` (lines 557-564) — only used by `wrap_command()`
3. Remove `remap_command_paths()` if it's only used internally by removed code (check orchestrator.py usage first)
4. Clean up any unused imports

**Verification:**
```bash
cd odin && python -m pytest tests/ -v
grep -r "wrap_command\|_shell_quote" odin/src/
```

---

### Phase 5: Reflection Sandbox Gap
**Files:** `taskit/taskit-backend/tasks/dag_executor.py`
**Risk:** Low — additive change
**Tenet:** #4 Proof of Work — reflections should respect the same isolation as execution

**What:**
In `execute_reflection()` (~line 418-488), add the same sandbox flag logic that exists in `execute_single_task()`:
```python
if getattr(task.board, "sandbox_enabled", False):
    cmd.append("--sandbox")
```

**Verification:**
```bash
cd taskit/taskit-backend && python -m pytest tasks/tests/ -v -k "reflection"
# Manual: enable sandbox on board, trigger reflection, verify it runs sandboxed
```

---

### Phase 6: PTY + Permission Verification
**Files:** None (diagnostic only, fix if needed)
**Risk:** Low — observational
**Depends on:** Phase 2 (need docker exec path working first)

**What:**
1. Run the isolated test from the gaps doc:
   ```bash
   docker run --rm odin-sandbox:latest script -q -c "kilo run --format json --auto 'run echo hello'" /dev/null
   ```
2. If `script` doesn't propagate PTY to kilo subagents, try alternatives:
   - `unbuffer` (from `expect` package)
   - `socat` PTY allocation
   - Setting `TERM=xterm` explicitly in wrapper script
3. Test with opencode (GLM) — GLM succeeded without PTY issues in task 86, so this may be kilo-specific

**Verification:** Kilo subagent bash commands auto-approve without blocking.

---

## Execution Order

```
Phase 1 (exit code fix)     ──→ independent, do first
Phase 2 (hybrid docker exec) ──→ independent, do second (biggest change)
Phase 3 (MCP env)            ──→ depends on Phase 2 (need docker exec path)
Phase 4 (dead code removal)  ──→ depends on Phase 2 (remove old paths)
Phase 5 (reflection gap)     ──→ independent, can parallel with any phase
Phase 6 (PTY verification)   ──→ depends on Phase 2
```

**Parallelizable:** Phase 1 + Phase 5 can run in parallel. Phase 3 + Phase 4 can run in parallel after Phase 2.

## Files Modified (complete list)

| File | Phases | Change Type |
|------|--------|-------------|
| `odin/src/odin/sandbox.py` | 1, 2, 4 | Fix, refactor, cleanup |
| `odin/src/odin/mcps/taskit_mcp/config.py` | 3 | Fix (conditional) |
| `taskit/taskit-backend/tasks/dag_executor.py` | 5 | Additive |

## What's NOT in Scope

- **Board model/API/UI** — proven solid, no changes
- **Celery flag passthrough** — proven solid, no changes
- **CLI --sandbox/--no-sandbox** — proven solid, no changes
- **dev.sh auto-reinstall** — proven solid, no changes
- **Credential staging** — proven solid, Docker tests pass (15/15)
- **SDK lifecycle (create/destroy)** — keeping as-is, only replacing command execution
- **Sandbox.create() hang recovery** — keeping the timeout + reconnect logic, it works

## Success Criteria

1. `odin exec <task_id> --sandbox` completes with correct exit code (no silent masking)
2. MCP `taskit_add_comment` succeeds from inside sandbox (comment appears on dashboard)
3. Task reaches REVIEW status via sandboxed execution
4. Reflection tasks respect board's `sandbox_enabled` setting
5. No dead code in sandbox.py
