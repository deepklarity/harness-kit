# Planning PTY Session — Detailed Trace

## 1. PlanningTerminal — xterm.js init and WebSocket

**File**: `taskit/taskit-frontend/src/components/PlanningTerminal.tsx`
**Function**: `useEffect([specId])`
**Called by**: component mount when `SpecDetailView` sets `terminalVisible=true`

Key logic:
- `WS_BASE` derived from `VITE_HARNESS_TIME_API_URL` — `http://` → `ws://`, `https://` → `wss://`
- Terminal opens on mount; WebSocket URL: `${WS_BASE}/ws/planning/${specId}/`
- `ws.onopen` sends `{type: "start", rows: term.rows, cols: term.cols}` — consumer uses dimensions for PTY; ignores if session already exists
- Binary messages (PTY output) arrive as `Blob` → converted to `Uint8Array` → `term.write()`
- `planningCompleteRef` (ref, not state) used in `ws.onclose` to distinguish normal close from disconnect — avoids stale closure bug
- `ResizeObserver` watches the terminal div; on resize calls `fitAddon.fit()` then sends resize message
- Cleanup: closes WebSocket + disposes Terminal + disconnects ResizeObserver

Data in: `specId: string`
Side effects: opens WebSocket, mounts xterm.js to DOM

---

## 2. PlanningConsumer.connect — WebSocket handshake

**File**: `taskit/taskit-backend/tasks/consumers.py`
**Class**: `PlanningConsumer`
**Function**: `connect()`

Key logic:
- Rejects with code 4004 if spec not found
- Rejects with code 4001 if `spec.status != "planning"` (guards against connecting to completed/failed specs)
- Checks `_active_sessions` before accepting — handles reconnect transparently
- Reconnect: replays `session['buffer']` as a single bytes send (entire scrollback in one message)
- If `session['merged']` is True (planning finished while disconnected): sends `planning_complete` JSON immediately, then removes session

Data in: spec_pk from URL route kwargs
Side effects: accepts WebSocket connection, may replay buffer

---

## 3. _build_planning_command — command assembly

**File**: `taskit/taskit-backend/tasks/consumers.py`
**Function**: `_build_planning_command(spec_path, planner_config)`

Key logic:
- Base command: `["odin", "plan", spec_path, "--direct"]`
- `--direct` always included — web UI always uses direct mode (no tmux)
- `--quick` appended if `config.get("quick")` is truthy
- `--auto` appended if `config.get("auto")` is truthy
- Agent resolution (backward-compat): new UI sends `config["agent"]`; legacy UI sent agent name in `config["model"]`
- Model resolution: looks for `config["base_model"]` or `config["baseModel"]`; if not set, falls back to `config["model"]` when agent was also found via `config["agent"]`
- Appends `--base-agent <agent>` and `--base-model <model>` if resolved

Data in: `spec_path: str`, `planner_config: dict`
Data out: `list[str]` command

---

## 4. _start_planning — PTY launch

**File**: `taskit/taskit-backend/tasks/consumers.py`
**Function**: `PlanningConsumer._start_planning(dimensions)`

Key logic:
- `dimensions` tuple `(rows, cols)` from the frontend's `start` message (default 24×80)
- `cwd` priority: `spec.board.working_dir` → `ODIN_WORKING_DIR` env var → `os.getcwd()`
- Temp file created in `cwd` with `NamedTemporaryFile(suffix=".md", prefix=f"spec_{spec_pk}_", delete=False)` — file persists until `_complete_planning()` deletes it
- `ODIN_FORCED_BACKEND_URL` injected into PTY environment so odin calls back to the correct Django instance
- Session dict registered in `_active_sessions` BEFORE `PtySession.start()` — prevents race on immediate output
- `run_start = datetime.now(timezone.utc)` — used in `_complete_planning()` to find the odin-created sibling spec
- `is_direct = "--direct" in cmd` — controls auto-kickoff/auto-exit behavior in `on_output`
- `PtySession` created with `direct=True` and frontend-provided `dimensions`

Data in: spec content, board working_dir, planner_config, terminal dimensions
Side effects: creates temp file on disk, registers session, spawns PTY process

### Auto-kickoff and auto-exit (direct mode only)

The `on_output` callback handles two auto-send phases:
1. **Kickoff**: watches buffer for `shortcuts` (Claude Code welcome screen). After 1.5s delay, writes `_KICKOFF_MESSAGE + '\r'` to PTY. This replaces the tmux paste mechanism.
2. **Auto-exit**: after kickoff, watches for `.odin/plans/plan_` + `wrote` (plan file written). Once detected and the `>` prompt reappears, writes `/exit\r` after 2s delay. This causes Claude Code to exit, allowing `odin plan` to proceed with task creation.

Data in: PTY output chunks (bytes)
Side effects: writes to PTY stdin (kickoff message, /exit command)

---

## 5. PtySession — PTY subprocess management

**File**: `taskit/taskit-backend/tasks/pty_session.py`
**Class**: `PtySession`

Key logic:
- `ptyprocess.PtyProcess.spawn()` gives the subprocess a real terminal file descriptor — interactive prompts work correctly
- Terminal dimensions from constructor `(rows, cols)` — set from frontend's initial `start` message (no longer hardcoded 24×80)
- `direct: bool` — controls graceful stop behavior
- Reader runs in a daemon thread — reads 1024 bytes at a time, calls `on_output(data)` for each chunk
- EOF from PTY process → exits read loop → calls `on_exit(rc)`
- `rc`: calls `self._proc.wait()` only if process is still alive, otherwise returns 0
- `write(data)`: checks `isalive()` before writing — silent no-op if process already exited
- `resize(rows, cols)`: calls `ptyprocess.setwinsize()` — resizes the PTY window in the kernel
- `graceful_stop()`: dispatches to `_graceful_stop_direct()` (sends Ctrl-C `\x03` to PTY) or `_graceful_stop_tmux()` (kills tmux session). Both wait up to `timeout` seconds for odin to finish task creation, then force-kill.

Data flow: PTY output bytes → `on_output` callback → `consumers.py:on_output()` → WebSocket

---

## 6. _complete_planning — task merge and status update

**File**: `taskit/taskit-backend/tasks/consumers.py`
**Function**: `_complete_planning(spec_pk_str, session)`

Key logic:
- Runs regardless of whether a consumer is connected — planning completion is never blocked on the frontend
- Deletes temp spec file from disk (`os.unlink`) — silent on `OSError`
- Sibling spec lookup: `Spec.objects.filter(created_at__gte=run_start, board=spec.board).exclude(pk=spec.pk).afirst()` — finds the spec odin registered during the run
- Task migration: `Task.objects.filter(spec=odin_spec).aupdate(spec=spec)` — bulk update, no individual saves
- `spec.odin_id` updated to match sibling's `odin_id` (used for future odin exec calls)
- Sibling spec deleted after task migration
- `spec.status = STATUS_PLANNING_COMPLETE`, then `asave()`
- Sets `session['merged'] = True` before sending WS message — reconnecting client checks this flag
- Entire function wrapped in `try/except Exception: pass` — failure is silent; spec may be left in `planning` state

Side effects: DB writes (task update, spec status update, sibling delete), temp file delete, WebSocket message send

---

## 7. SpecDetailView — post-completion UI

**File**: `taskit/taskit-frontend/src/components/SpecDetailView.tsx`

Key logic:
- `onComplete` prop passed to `PlanningTerminal` = `refetchSpec` — re-fetches spec on `planning_complete` message
- After refetch, `spec.status` is `planning_complete` → status badge updates
- Tasks section renders tasks that are now linked to the spec
- `terminalVisible` stays `true` — terminal is not unmounted, user can read the output
- Fresh navigation to `planning_complete` spec: `terminalVisible=false` → static "Planning completed" banner shown instead of terminal
- Fresh navigation to `planning_failed` spec: shows error banner + "Retry Planning" button
- Retry: calls `retryPlanning(specId)` → `POST /api/specs/{id}/retry-planning/` → resets status to `planning` → `useEffect` on status triggers `setTerminalKey(k+1)` → `PlanningTerminal` remounts with fresh WebSocket
