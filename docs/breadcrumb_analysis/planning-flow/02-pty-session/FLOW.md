# Planning PTY Session

Trigger: `PlanningTerminal` mounts in `SpecDetailView` when `spec.status === 'planning'`
End state: spec `status=planning_complete`, tasks merged into spec, terminal shows `[Planning complete]`, `SpecDetailView` refetches spec.

## Flow

`PlanningTerminal.tsx` :: `useEffect([specId])`
  → initializes xterm.js `Terminal` + `FitAddon`
  → opens WebSocket to `ws://{host}/ws/planning/{specId}/`
  → `ws.onopen` → sends `{type: "start", rows, cols}` (initial terminal dimensions)

`tasks/consumers.py` :: `PlanningConsumer.connect()`
  → validates spec exists; closes with 4004 if not found
  → closes with 4001 if `spec.status != "planning"`
  → checks `_active_sessions[spec_pk]` for existing session (reconnect path)
  → [reconnect] replays `session['buffer']` bytes to terminal
  → [reconnect + merged] sends `{type: "planning_complete"}` and clears session
  → [new session] accepts and waits for `start` message

`tasks/consumers.py` :: `PlanningConsumer.receive()` type=`start`
  → only acts if no active session exists for this spec
  → extracts `rows`, `cols` from start message (defaults 24×80)
  → calls `_start_planning(dimensions=(rows, cols))`

`tasks/consumers.py` :: `PlanningConsumer._start_planning()`
  → writes `spec.content` to temp `.md` file in board's `working_dir`
  → calls `_build_planning_command(spec_path, planner_config)`
  → command: `odin plan <spec_path> --direct [--quick] [--auto] [--base-agent X] [--base-model Y]`
  → `--direct` always passed from web UI (skips tmux, agent runs as direct subprocess)
  → registers session dict in `_active_sessions[spec_pk]`
  → creates `PtySession(dimensions=dimensions, direct=True)` and calls `.start(on_output, on_exit)`

`tasks/pty_session.py` :: `PtySession.start()`
  → spawns `ptyprocess.PtyProcess` with cmd, cwd, env, and caller-provided dimensions
  → background reader thread: reads 1024-byte chunks, calls `on_output(data)` per chunk
  → on EOF: calls `on_exit(rc)`

`tasks/consumers.py` :: `on_output(data)`  [background thread → event loop]
  → appends to `session['buffer']` (capped at 1 MB)
  → if consumer is connected: `asyncio.run_coroutine_threadsafe(consumer.send(bytes_data=data))`

  [direct mode: auto-kickoff]
  → watches for `shortcuts` in buffer tail (Claude Code welcome screen fully rendered)
  → `threading.Timer(1.5s)` → writes `_KICKOFF_MESSAGE + '\r'` to PTY

  [direct mode: auto-exit]
  → watches for `.odin/plans/plan_` + `wrote` in buffer (plan file written)
  → waits for `>` prompt to reappear in last 50 chars (agent idle)
  → `threading.Timer(2.0s)` → writes `/exit\r` to PTY
  → Claude Code exits → `subprocess.run()` in odin returns → odin creates tasks → process exits

`PlanningTerminal.tsx` :: `ws.onmessage`
  → binary message (Blob) → `arrayBuffer()` → `term.write(Uint8Array)`
  → string JSON `{type: "planning_complete"}` → writes `[Planning complete]` to terminal → calls `onComplete()`

  [user types in terminal]
  `PlanningTerminal.tsx` :: `term.onData()`
    → `ws.send({type: "input", data})`
  `PlanningConsumer.receive()` type=`input`
    → `pty.write(data.encode())`

  [terminal resized]
  `PlanningTerminal.tsx` :: `ResizeObserver`
    → `fitAddon.fit()` → `ws.send({type: "resize", rows, cols})`
  `PlanningConsumer.receive()` type=`resize`
    → `pty.resize(rows, cols)` → `ptyprocess.setwinsize(rows, cols)`

`tasks/consumers.py` :: `on_exit(rc)`  [background thread]
  → sets `session['complete'] = True`, `session['exit_code'] = rc`
  → schedules `_complete_planning(spec_pk_str, session)` on event loop

`tasks/consumers.py` :: `_complete_planning()`  [async, event loop]
  → deletes temp `.md` spec file from disk
  → finds odin-created sibling spec: `Spec.objects.filter(created_at__gte=run_start, board=spec.board).exclude(pk=spec.pk)`
  → if found: moves all its tasks to the UI spec (`Task.objects.filter(spec=odin_spec).aupdate(spec=spec)`)
  → copies `odin_id` from sibling spec to UI spec
  → deletes sibling spec
  → sets `spec.status = STATUS_PLANNING_COMPLETE`, saves
  → sends `{type: "planning_complete", spec_id, exit_code}` to consumer (if connected)
  → removes session from `_active_sessions`

`SpecDetailView.tsx` :: `onComplete` → `refetchSpec()`
  → re-fetches spec → status badge shows "Planning Complete"
  → tasks section renders newly created tasks

## Disconnect / reconnect path

`PlanningConsumer.disconnect()`
  → sets `session['consumer'] = None`
  → PTY process keeps running — buffer keeps accumulating
  → spec status stays `planning`

`PlanningTerminal.tsx` remounts (page refresh / navigate back)
  → new WebSocket opens to same `ws/planning/{specId}/`
  → `PlanningConsumer.connect()` finds existing `_active_sessions[spec_pk]`
  → replays full `session['buffer']` bytes in one send
  → re-attaches `session['consumer'] = self`
  → if `session['merged']` is True: immediately sends `planning_complete`
