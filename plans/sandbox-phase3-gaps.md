# Sandbox Phase 3: Gaps, Open Issues, and Path Forward

**Date:** 2026-04-09
**Status:** Phase 2 mechanically works. Phase 3 wiring has gaps that need isolated fixes before E2E is reliable.

## Honest Assessment

After 12+ hours of E2E testing, the sandbox **can** execute a harness inside a container, produce a file on the host, and return a result. But the path there is fragile — too many moving parts changed simultaneously, and debugging was reactive (fix one thing, hit the next). This document separates what's solid from what needs isolated work.

## What's Proven Solid (no more work needed)

| Layer | Evidence |
|-------|----------|
| Board model + API + UI toggle | `sandbox_enabled` field, serializer, SettingsView toggle — all work |
| Celery `--sandbox` flag passthrough | dag_executor reads `task.board.sandbox_enabled`, appends flag. Verified via `ps` |
| CLI `--sandbox`/`--no-sandbox` flags | Config override works. Verified in odin detail log |
| `dev.sh` auto-reinstall on pyproject change | Hash-based check, installs opensandbox automatically |
| Docker image + bind mounts | `odin-sandbox:latest` runs, worktree at `/workspace`, logs at `/logs/`, files visible on host |
| Credential staging | `prepare_credentials()` works for files and dirs. Docker tests pass (15/15) |
| `odin sandbox test --agent <name>` | Simple harness test (poem prompt) works for all 6 CLIs |
| SDK lifecycle (create/execute/destroy) | `sdk_test.py --all` passes: echo, file bind mount, MCP connectivity |

## Open Issues (must fix, in priority order)

### 1. MCP `taskit_add_comment` fails inside sandbox — DNS resolution

**Status:** OPEN — primary blocker for question flow and proof posting
**Symptom:** `[Errno 8] nodename nor servname provided, or not known` on every MCP tool call
**What we know:**
- `host.docker.internal` resolves fine from container shell (`getent hosts`, `curl` both work)
- `TASKIT_URL=http://host.docker.internal:9100` is in the `opencode.json` `"environment"` block
- Container-level env vars are set correctly (verified with `env | sort` inside container)
- The `taskit-mcp` server reads `TASKIT_URL` from `os.environ.get()` (server.py:79)
- The opencode CLI spawns MCP servers as child processes

**What we DON'T know:**
- Does the opencode/kilo CLI actually pass the `"environment"` config block as env vars to spawned MCP server processes?
- Or does it use a different mechanism (stdin/config injection)?
- Does the `script -q -c` PTY wrapper affect env propagation to child processes?

**Isolated test to answer this:**
```bash
# Inside a running sandbox container, directly test if taskit-mcp can reach backend:
TASKIT_URL=http://host.docker.internal:9100 TASKIT_TASK_ID=99 taskit-mcp &
# Then call it via stdio... 

# Simpler: check what env the MCP server process actually sees
# Add to sandbox wrapper script: env dump before CLI starts (DONE — sandbox_env.txt)
```

**Next step:** Run `odin exec <task> --sandbox`, check `.odin/logs/sandbox_env.txt` for `TASKIT_URL`. If present, the container env is fine and the issue is how opencode passes env to MCP processes. If absent, the SDK isn't propagating env vars.

### 2. `execute_in_sandbox` architecture — SDK streaming vs file-based

**Status:** Redesigned to file-based (background command + exit marker polling). Needs validation.
**What changed:** Replaced SDK SSE streaming with tmux-like approach:
- Write wrapper script to bind-mounted `/logs/`
- Run as background command via `RunCommandOpts(background=True)`
- Poll exit marker file on host
- Read output from host files

**What we know:**
- Background execution works (command starts, exit marker appears)
- `tail -f trace.jsonl` works from host — output is live
- Exit marker correctly captures exit code
- GLM task 86 reached REVIEW status via this path

**What might break:**
- `script -q -c` PTY wrapper: Linux syntax verified (`script -q -c "cmd" /dev/null`), but may add terminal control chars to output
- `PIPESTATUS[0]` in bash: may not capture the exit code correctly through `script | tee` pipeline
- Long-running tasks: polling at 2s intervals might miss edge cases

**Isolated test:**
```bash
# Test the exact wrapper script manually inside a container
docker run --rm -it odin-sandbox:latest bash
# Then paste the wrapper script and verify output + exit code
```

### 3. PTY for `--auto` permission approval

**Status:** Added `script -q -c` wrapper. Untested with kilo subagents.
**What we know:**
- On host, tmux provides PTY → kilo `--auto` auto-approves all permissions including subagent bash
- In container without PTY, kilo subagents block on `bash → ask`
- `script` provides a PTY on Linux

**What we DON'T know:**
- Does `script -q -c` in the wrapper actually propagate the PTY to kilo's subagents?
- Does opencode/glm have the same subagent permission issue?
- GLM task 86 succeeded without hitting permission blocks — maybe GLM doesn't use subagents the same way

**Isolated test:**
```bash
# Inside container with script wrapper:
script -q -c "opencode run --format json --auto 'run python3 --version'" /dev/null
# Check if bash permission is auto-approved (no block)
```

### 4. `opencode.json` permissions for non-interactive execution

**Status:** Added `"*": "allow"` blanket + explicit denies. Needs verification that kilo respects it.
**What we know:**
- Kilo has its own built-in permission ruleset that overrides opencode.json for some tools
- `"bash": "allow"` in opencode.json was NOT sufficient — `external_directory` also needed
- Added `"*": "allow"` as blanket, with `question/plan/todo: deny`

**What we DON'T know:**
- Does kilo respect `"*": "allow"` from opencode.json?
- Or does it only check specific tool names?
- Does GLM (opencode) have the same permission system?

### 5. Sandbox.create() intermittent hang

**Status:** Timeout + recovery via Sandbox.connect() added. Works sometimes.
**What we know:**
- SDK bug: HTTP connection closed between create and endpoint resolution
- 30s timeout fires, recovery finds sandbox on server, reconnects
- But sometimes the timeout doesn't fire (unclear why — might be event loop blocked)

**Risk:** Low — the file-based execution approach means even if create hangs, we can detect and recover.

## Micro-Scoped Test Plan

Each test is independent, takes <5 minutes, and answers one question.

### Test A: Container env propagation to MCP server
**Goal:** Verify `TASKIT_URL` reaches `taskit-mcp` process inside sandbox
**Method:** `odin exec <task> --sandbox`, then check `.odin/logs/sandbox_env.txt`
**Pass:** `TASKIT_URL=http://host.docker.internal:9100` in env dump
**If fail:** Container env not propagated — fix in `create_sandbox()` env handling

### Test B: MCP server direct connectivity
**Goal:** Verify `taskit-mcp` can reach backend from inside container
**Method:** `docker exec <cid> sh -c 'TASKIT_URL=http://host.docker.internal:9100 TASKIT_TASK_ID=1 python3 -c "from odin.mcps.taskit_mcp.server import _create_client; c=_create_client(1); print(c)"'`
**Pass:** No DNS error
**If fail:** DNS issue specific to Python's httpx/urllib inside container — check `/etc/resolv.conf`

### Test C: PTY + permission approval
**Goal:** Verify `script` wrapper allows kilo `--auto` to approve subagent bash
**Method:** `docker run --rm odin-sandbox:latest script -q -c "kilo run --format json --auto 'run echo hello'" /dev/null`
**Pass:** Kilo runs, echo succeeds, no permission block
**If fail:** `script` doesn't propagate PTY to subagents — need different approach (e.g., `unbuffer`, `expect`)

### Test D: Exit marker reliability
**Goal:** Verify wrapper script writes correct exit code
**Method:** Run `sdk_test.py --file` (already passing) + test with a failing command
**Pass:** Exit marker contains correct code (0 for success, non-zero for failure)

### Test E: Full E2E with MCP (after A+B pass)
**Goal:** Agent calls `taskit_add_comment`, posts proof, task moves to REVIEW
**Method:** `odin exec <task> --sandbox` with question smoke spec
**Pass:** Comment appears on TaskIt dashboard, task in REVIEW

## Architecture Decision Needed

The current sandbox execution path has accumulated complexity:
- SDK streaming → replaced with file-based
- SSE hang → replaced with background execution + polling
- PTY → `script` wrapper
- MCP env → double rewrite (config + container env)

**Question:** Should we simplify by dropping the OpenSandbox SDK for execution entirely and using raw `docker run` (the original fallback path) with a proper wrapper script? The SDK adds value for sandbox lifecycle (create/destroy/resource limits) but the execution path via execd SSE is the source of most bugs.

**Hybrid approach:** Use SDK for `Sandbox.create()` (gets us resource limits, volumes, metadata) but use `docker exec` for command execution instead of `sandbox.commands.run()`. This avoids the execd SSE entirely while keeping the SDK's lifecycle management.

## Files to Review Before Next Session

| File | What to check |
|------|---------------|
| `odin/src/odin/sandbox.py` | Many patches accumulated — needs cleanup pass |
| `odin/src/odin/orchestrator.py` | Permission dict, MCP env rewrite, sandbox path logging |
| `odin/src/odin/mcps/taskit_mcp/config.py` | `_server_entry_opencode()` — is `"environment"` the right key? |
| `odin/sandbox/testing_tools/` | Scripts created this session — verify they still work after sandbox.py changes |
| `docs/testing_process/sandbox_testing.md` | Update with this session's final state |
