import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Spec, SpecComment, Task
from .pty_session import PtySession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session registry — persists PTY sessions across WebSocket reconnections.
# Key: spec_pk (str), Value: session dict.
# ---------------------------------------------------------------------------
_active_sessions: dict[str, dict] = {}
_MAX_BUFFER = 1_048_576  # 1 MB cap per session


_KICKOFF_MESSAGE = (
    "I need help planning this task. The full specification and instructions "
    "are in the system prompt.\n\n"
    "Please analyze the spec and help me decompose it into sub-tasks. When "
    "we're satisfied with the plan, write the final JSON to the file path "
    "specified in the system prompt."
)


def _build_planning_command(spec_path: str, planner_config: dict | None) -> list[str]:
    """Build the odin plan command from planner_config.

    Always passes --direct when called from the web UI so that odin
    runs the agent as a direct subprocess (no tmux wrapping — the PTY
    from PlanningConsumer is the terminal).

    Backward compatibility:
    - legacy UI stored the base agent under planner_config["model"]
    - new UI stores planner_config["agent"] and planner_config["model"]
    """
    config = planner_config or {}
    cmd = ["odin", "plan", spec_path, "--direct"]
    if config.get("quick"):
        cmd.append("--quick")
    if config.get("auto"):
        cmd.append("--auto")
    if config.get("skip_reflection"):
        cmd.append("--skip-reflection")

    base_agent = config.get("agent") or config.get("base_agent")
    base_model = config.get("base_model") or config.get("baseModel")

    if base_agent:
        if not base_model and config.get("model"):
            base_model = config["model"]
    elif config.get("model"):
        # Legacy planner_config stored the agent name in "model".
        base_agent = config["model"]

    if base_agent:
        cmd.extend(["--base-agent", base_agent])
    if base_model:
        cmd.extend(["--base-model", base_model])
    return cmd


class PlanningConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.spec_pk = self.scope["url_route"]["kwargs"]["spec_pk"]
        try:
            self.spec = await (
                Spec.objects
                .select_related("board")
                .aget(pk=self.spec_pk)
            )
        except Spec.DoesNotExist:
            await self.close(code=4004)
            return
        if self.spec.status != Spec.STATUS_PLANNING:
            await self.close(code=4001)
            return
        await self.accept()
        self.pty = None
        self.loop = asyncio.get_event_loop()
        self.run_start = None
        self._planning_complete = False
        self._disconnected = False

        # Check for an existing background session (user navigated away and back).
        session = _active_sessions.get(str(self.spec_pk))
        if session:
            session['consumer'] = self
            self.pty = session['pty']
            self.run_start = session['run_start']

            # Replay everything the terminal emitted while we were away.
            if session['buffer']:
                await self.send(bytes_data=bytes(session['buffer']))

            if session.get('merged'):
                # Planning finished and merged while the user was away.
                self._planning_complete = True
                await self.send(text_data=json.dumps({
                    "type": "planning_complete",
                    "spec_id": int(self.spec_pk),
                    "exit_code": session.get('exit_code', 0),
                }))
                _active_sessions.pop(str(self.spec_pk), None)
            # If complete but not yet merged, _complete_planning will send
            # the planning_complete message to us once it finishes.

    async def disconnect(self, close_code):
        self._disconnected = True
        session = _active_sessions.get(str(self.spec_pk))
        if session:
            # Detach — the PTY keeps running in the background.
            session['consumer'] = None
        # Do NOT kill the PTY or change the spec status.

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        msg = json.loads(text_data)
        msg_type = msg.get("type")
        if msg_type == "start":
            # Only start a new process if there is no active session.
            if not _active_sessions.get(str(self.spec_pk)):
                # Guard against stale planning state after server restart:
                # if the spec already has tasks, planning completed before
                # the server died — repair the status instead of re-running.
                if await self._recover_if_already_planned():
                    return
                rows = msg.get("rows", 24)
                cols = msg.get("cols", 80)
                await self._start_planning(dimensions=(rows, cols))
        elif msg_type == "stop" and self.pty:
            # UI equivalent of double Ctrl-C — gracefully interrupt so
            # odin can read the plan file and create tasks before exiting.
            self.pty.graceful_stop()
        elif msg_type == "input" and self.pty:
            self.pty.write(msg["data"].encode())
        elif msg_type == "resize" and self.pty:
            self.pty.resize(msg.get("rows", 24), msg.get("cols", 80))

    async def _start_planning(self, dimensions: tuple[int, int] = (24, 80)):
        board_cwd = (self.spec.board.working_dir or "").strip()
        cwd = board_cwd or os.environ.get("ODIN_WORKING_DIR") or os.getcwd()

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix=f"spec_{self.spec_pk}_",
                dir=cwd, delete=False,
            ) as f:
                f.write(self.spec.content)
                spec_path = f.name
        except OSError as exc:
            logger.error("[planning] Cannot write spec file to cwd=%s: %s", cwd, exc)
            await self._fail_planning(f"Working directory not found: {cwd}")
            return

        config = self.spec.planner_config or {}
        cmd = _build_planning_command(spec_path, config)

        env = {**os.environ}
        env.setdefault("TERM", "xterm-256color")
        backend_url = os.environ.get("TASKIT_INTERNAL_URL", "http://localhost:8000")
        env["ODIN_FORCED_BACKEND_URL"] = backend_url

        self.run_start = datetime.now(timezone.utc)
        loop = self.loop
        spec_pk_str = str(self.spec_pk)

        session: dict = {
            'pty': None,
            'buffer': bytearray(),
            'run_start': self.run_start,
            'spec_path': spec_path,
            'consumer': self,
            'complete': False,
            'merged': False,
            'exit_code': None,
            'board_pk': self.spec.board_id,
        }
        _active_sessions[spec_pk_str] = session

        is_direct = "--direct" in cmd

        def on_output(data: bytes):
            buf = session['buffer']
            buf.extend(data)
            if len(buf) > _MAX_BUFFER:
                del buf[:len(buf) - _MAX_BUFFER]
            consumer = session.get('consumer')
            if consumer and not getattr(consumer, '_disconnected', True):
                try:
                    asyncio.run_coroutine_threadsafe(
                        consumer.send(bytes_data=data), loop
                    )
                except Exception:
                    pass

            # In direct mode, auto-send the kickoff message once the
            # agent prompt is ready.  Exit is manual — the user clicks
            # Stop when they see planning is complete.
            if is_direct and not session.get('kickoff_sent'):
                tail = bytes(buf[-1000:]).decode('utf-8', errors='replace')
                if 'shortcuts' in tail.lower():
                    session['kickoff_sent'] = True
                    _start_kickoff_poller(session)

        def _start_kickoff_poller(sess: dict):
            """Poll for the '>' input prompt, then send the kickoff message."""
            import time

            def _poll():
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    tail = bytes(sess['buffer'][-200:]).decode('utf-8', errors='replace')
                    if '>' in tail or '❯' in tail:
                        break
                    time.sleep(0.3)
                pty = sess.get('pty')
                if pty:
                    try:
                        pty.write((_KICKOFF_MESSAGE + '\r').encode())
                    except Exception:
                        pass

            threading.Thread(target=_poll, daemon=True).start()

        def on_exit(rc: int):
            session['complete'] = True
            session['exit_code'] = rc
            asyncio.run_coroutine_threadsafe(
                _complete_planning(spec_pk_str, session), loop
            )

        try:
            self.pty = PtySession(
                cmd=cmd, cwd=cwd, env=env,
                spec_odin_id=self.spec.odin_id,
                dimensions=dimensions,
                direct=is_direct,
            )
            session['pty'] = self.pty
            self.pty.start(on_output=on_output, on_exit=on_exit)
        except Exception as exc:
            logger.error("[planning] Failed to start PTY for spec_pk=%s: %s", self.spec_pk, exc)
            _active_sessions.pop(spec_pk_str, None)
            await self._fail_planning(f"Failed to start planning process: {exc}")

    async def _recover_if_already_planned(self) -> bool:
        """Check if planning already completed before a server restart.

        When the server dies mid-planning, the spec stays in 'planning'
        status but tasks may have already been created.  Detect this by
        checking for existing tasks linked to the spec.  If found, repair
        the status to 'planning_complete' and notify the frontend.

        Returns True if recovery happened (caller should skip re-planning).
        """
        task_count = await Task.objects.filter(spec_id=self.spec_pk).acount()
        if task_count == 0:
            return False

        logger.info(
            "[planning] Spec %s already has %d tasks — recovering from stale planning state",
            self.spec_pk, task_count,
        )
        try:
            self.spec.status = Spec.STATUS_PLANNING_COMPLETE
            await self.spec.asave(update_fields=["status"])
        except Exception:
            logger.exception("[planning] Failed to repair status for spec %s", self.spec_pk)

        await self.send(text_data=json.dumps({
            "type": "planning_complete",
            "spec_id": int(self.spec_pk),
            "exit_code": 0,
        }))
        return True

    async def _fail_planning(self, reason: str):
        """Mark spec as planning_failed and notify the connected client."""
        try:
            self.spec.status = Spec.STATUS_PLANNING_FAILED
            await self.spec.asave(update_fields=["status"])
        except Exception:
            logger.exception("[planning] Failed to mark spec %s as planning_failed", self.spec_pk)
        try:
            await self.send(text_data=json.dumps({
                "type": "planning_complete",
                "spec_id": int(self.spec_pk),
                "exit_code": 1,
                "error": reason,
            }))
        except Exception:
            pass


async def _complete_planning(spec_pk_str: str, session: dict):
    """Merge odin-created tasks into the UI spec. Works whether a consumer
    is connected or not — if one is attached it receives the WS message."""
    spec_path = session.get('spec_path', '')
    try:
        os.unlink(spec_path)
    except OSError:
        pass

    try:
        spec = await Spec.objects.select_related("board").aget(pk=int(spec_pk_str))
        run_start = session.get('run_start')
        if run_start:
            odin_spec = await Spec.objects.filter(
                created_at__gte=run_start,
                board=spec.board,
            ).exclude(pk=spec.pk).afirst()

            if odin_spec:
                await Task.objects.filter(spec=odin_spec).aupdate(spec=spec)
                await SpecComment.objects.filter(spec=odin_spec).aupdate(spec=spec)
                spec.odin_id = odin_spec.odin_id
                # Carry over metadata (working_dir, branch, worktree_path, etc.)
                odin_meta = odin_spec.metadata or {}
                ui_meta = spec.metadata or {}
                merged = {**odin_meta, **ui_meta}
                spec.metadata = merged
                await sync_to_async(odin_spec.delete)()
            else:
                logger.warning("[planning] No odin spec found — odin may not have created tasks")

        exit_code = session.get('exit_code', 0)
        if exit_code != 0:
            spec.status = Spec.STATUS_PLANNING_FAILED
            logger.warning("[planning] odin plan exited with code %s for spec_pk=%s — marking planning_failed", exit_code, spec_pk_str)
        else:
            spec.status = Spec.STATUS_PLANNING_COMPLETE
        await spec.asave()
    except Exception:
        logger.exception("[planning] Error in _complete_planning for spec_pk=%s", spec_pk_str)
        try:
            spec = await Spec.objects.aget(pk=int(spec_pk_str))
            if spec.status == Spec.STATUS_PLANNING:
                spec.status = Spec.STATUS_PLANNING_FAILED
                await spec.asave(update_fields=["status"])
        except Exception:
            logger.exception("[planning] Failed to mark spec as planning_failed for spec_pk=%s", spec_pk_str)

    session['merged'] = True

    # Notify the attached consumer, if any.
    consumer = session.get('consumer')
    if consumer and not getattr(consumer, '_disconnected', True):
        consumer._planning_complete = True
        try:
            await consumer.send(text_data=json.dumps({
                "type": "planning_complete",
                "spec_id": int(spec_pk_str),
                "exit_code": session.get('exit_code', 0),
            }))
        except Exception:
            pass

    _active_sessions.pop(spec_pk_str, None)


# ---------------------------------------------------------------------------
# SessionConsumer — streams task execution / reflection JSONL traces live.
#
# One-way read-only stream. On connect, the consumer resolves which JSONL
# file to tail (reflection RUNNING > task EXECUTING > most-recent on-disk),
# sends the current contents as a chunk, then polls for appends. Rerun is
# detected via (inode, size) change — on change, a reset frame is emitted
# and streaming restarts from offset 0.
#
# Frames sent to client (JSON):
#   {"type": "meta",  "session_type": "task_execution"|"reflection",
#    "live": bool, "exists": bool, "task_id": int, "report_id": int|null}
#   {"type": "chunk", "data": "<raw jsonl text>"}
#   {"type": "reset", "session_type": ...}   # on rerun (new file/inode)
#   {"type": "eof"}                          # run finished and file stable
#   {"type": "error", "message": str}
# ---------------------------------------------------------------------------

_SESSION_POLL_INTERVAL = 0.5   # seconds between tail polls
_SESSION_CHUNK_SIZE = 64 * 1024  # per-read cap for memory safety
_SESSION_IDLE_EOF_POLLS = 4    # polls after terminal before emitting eof


class SessionConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.task_pk = int(self.scope["url_route"]["kwargs"]["task_pk"])
        self._closed = False
        self._poll_task: asyncio.Task | None = None
        try:
            task = await sync_to_async(
                lambda: Task.objects.select_related("board", "spec").get(pk=self.task_pk)
            )()
        except Task.DoesNotExist:
            await self.close(code=4004)
            return
        self.task = task
        await self.accept()
        self._poll_task = asyncio.create_task(self._stream_loop())

    async def disconnect(self, close_code):
        self._closed = True
        task = self._poll_task
        if task and not task.done():
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        # Read-only stream; ignore client input.
        return

    # ---- internals ----------------------------------------------------

    async def _resolve(self):
        """Return fresh SessionInfo for this task (or None)."""
        from .session_resolver import resolve_session

        def _fetch():
            task = Task.objects.select_related("board", "spec").get(pk=self.task_pk)
            return task, resolve_session(task)

        try:
            task, info = await sync_to_async(_fetch)()
            self.task = task
            return info
        except Task.DoesNotExist:
            return None

    async def _send_json(self, payload: dict) -> bool:
        if self._closed:
            return False
        try:
            await self.send(text_data=json.dumps(payload))
            return True
        except Exception:
            return False

    @staticmethod
    def _file_signature(path: str):
        """Return (inode, size) for a path, or None if missing."""
        try:
            st = os.stat(path)
            return (st.st_ino, st.st_size)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return None

    @staticmethod
    def _read_from(path: str, offset: int) -> tuple[bytes, int]:
        """Read bytes from `offset` to EOF, capped at _SESSION_CHUNK_SIZE."""
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(_SESSION_CHUNK_SIZE)
                return data, offset + len(data)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return b"", offset

    async def _stream_loop(self):
        try:
            info = await self._resolve()
            if info is None:
                logger.info("[session] task=%s — no session found", self.task_pk)
                await self._send_json({
                    "type": "meta",
                    "available": False,
                    "session_type": None,
                    "live": False,
                    "exists": False,
                    "task_id": self.task_pk,
                    "report_id": None,
                })
                # Keep connection open for a beat, then close; frontend will
                # reopen when the task enters an executing state.
                await self._send_json({"type": "eof"})
                return

            logger.info(
                "[session] task=%s — resolved path=%s, type=%s, live=%s, exists=%s, size=%s",
                self.task_pk, info.jsonl_path, info.session_type,
                info.live, info.exists, info.size,
            )

            await self._send_json({
                "type": "meta",
                "available": True,
                "session_type": info.session_type,
                "live": info.live,
                "exists": info.exists,
                "task_id": info.task_id,
                "report_id": info.report_id,
                "jsonl_path": info.jsonl_path,
            })

            current_path = info.jsonl_path
            current_session_type = info.session_type
            current_sig = self._file_signature(current_path)
            offset = 0
            idle_polls_after_terminal = 0

            logger.info(
                "[session] task=%s — initial sig=%s",
                self.task_pk, current_sig,
            )

            while not self._closed:
                # Check if the active session has switched (e.g. task rerun
                # replaced the file, or a reflection started). Re-resolve is
                # cheap — one DB hit + two stats.
                fresh = await self._resolve()
                if fresh is None:
                    await self._send_json({"type": "eof"})
                    return

                # Session kind changed (exec → reflection or vice versa):
                if fresh.session_type != current_session_type or fresh.jsonl_path != current_path:
                    await self._send_json({
                        "type": "reset",
                        "session_type": fresh.session_type,
                        "live": fresh.live,
                        "report_id": fresh.report_id,
                    })
                    current_path = fresh.jsonl_path
                    current_session_type = fresh.session_type
                    current_sig = self._file_signature(current_path)
                    offset = 0
                    idle_polls_after_terminal = 0

                # Same path — check inode change (rerun replaced the file):
                new_sig = self._file_signature(current_path)
                if new_sig is not None and current_sig is not None and new_sig[0] != current_sig[0]:
                    await self._send_json({
                        "type": "reset",
                        "session_type": current_session_type,
                        "live": fresh.live,
                        "report_id": fresh.report_id,
                    })
                    offset = 0
                    current_sig = new_sig
                    idle_polls_after_terminal = 0
                elif new_sig is not None and current_sig is None:
                    # File didn't exist on connect, now does.
                    current_sig = new_sig
                    idle_polls_after_terminal = 0

                # Read any new bytes.
                if new_sig is not None and new_sig[1] > offset:
                    data, offset = await sync_to_async(self._read_from)(current_path, offset)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        if not await self._send_json({"type": "chunk", "data": text}):
                            return
                        current_sig = (new_sig[0], offset)
                        idle_polls_after_terminal = 0
                        # Drain in a tight loop if more is available.
                        while not self._closed:
                            sig2 = self._file_signature(current_path)
                            if sig2 is None or sig2[1] <= offset:
                                break
                            data, offset = await sync_to_async(self._read_from)(current_path, offset)
                            if not data:
                                break
                            text = data.decode("utf-8", errors="replace")
                            if not await self._send_json({"type": "chunk", "data": text}):
                                return
                            current_sig = (sig2[0], offset)

                # Terminal detection: if the run is no longer live and the
                # file has been stable for a few polls, emit eof and stop.
                if not fresh.live:
                    idle_polls_after_terminal += 1
                    if idle_polls_after_terminal >= _SESSION_IDLE_EOF_POLLS:
                        await self._send_json({"type": "eof"})
                        return

                await asyncio.sleep(_SESSION_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[session] stream_loop crashed for task=%s", self.task_pk)
            await self._send_json({"type": "error", "message": str(exc)})
