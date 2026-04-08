import threading
from typing import Callable, Optional

import ptyprocess


class PtySession:
    """Manages a single odin plan PTY subprocess."""

    def __init__(self, cmd: list, cwd: str, env: dict, spec_odin_id: str = "",
                 dimensions: tuple[int, int] = (24, 80), direct: bool = False):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.spec_odin_id = spec_odin_id
        self.dimensions = dimensions
        self.direct = direct
        self._proc: Optional[ptyprocess.PtyProcess] = None
        self._running = False

    def start(self, on_output: Callable[[bytes], None], on_exit: Callable[[int], None]):
        """Spawn the PTY process and read output in a background thread."""
        self._proc = ptyprocess.PtyProcess.spawn(
            self.cmd, cwd=self.cwd, env=self.env,
            dimensions=self.dimensions,
        )
        self._running = True

        def _reader():
            while self._running:
                try:
                    data = self._proc.read(1024)
                    if data:
                        on_output(data)
                except EOFError:
                    break
                except Exception:
                    break
            if self._proc.isalive():
                rc = self._proc.wait()
            else:
                # Process already exited — retrieve stored exit status.
                # ptyprocess sets exitstatus after the process dies;
                # signalstatus is set if killed by a signal.
                rc = self._proc.exitstatus
                if rc is None:
                    rc = self._proc.signalstatus or 1
            self._running = False
            on_exit(rc)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()

    def write(self, data: bytes):
        if self._proc and self._proc.isalive():
            self._proc.write(data)

    def resize(self, rows: int, cols: int):
        if self._proc and self._proc.isalive():
            self._proc.setwinsize(rows, cols)

    def graceful_stop(self, timeout: float = 60.0):
        """Stop the planning session so odin can finish cleanly.

        In tmux mode: kills the tmux session.  When the session dies,
        ``tmux attach-session`` returns normally (no signal), the event loop
        stays alive, and ``plan()`` continues to create tasks.

        In direct mode: sends Ctrl-C to the PTY which interrupts the agent
        subprocess.  The ``InteractivePlanSession._run_direct()`` method
        catches the resulting KeyboardInterrupt, allowing the orchestrator
        to continue with task creation.

        Falls back to SIGKILL after *timeout* seconds.
        """
        if not self._proc or not self._proc.isalive():
            return

        if self.direct:
            self._graceful_stop_direct(timeout)
        else:
            self._graceful_stop_tmux(timeout)

    def _graceful_stop_direct(self, timeout: float):
        """Send Ctrl-C to the PTY and wait for odin to finish."""

        def _send_ctrl_c_and_wait():
            import time

            # Send Ctrl-C to interrupt the agent subprocess.
            try:
                self._proc.write(b'\x03')
            except Exception:
                pass

            # Wait for odin to finish (read plan, create tasks, exit).
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self._proc.isalive():
                    return
                time.sleep(0.5)
            # Process didn't exit in time — force-kill.
            self.terminate()

        threading.Thread(target=_send_ctrl_c_and_wait, daemon=True).start()

    def _graceful_stop_tmux(self, timeout: float):
        """Kill the odin planning tmux session and wait for odin to finish."""
        import subprocess as _sp

        def _kill_tmux_and_wait():
            import time

            # Find and kill odin planning tmux sessions.
            try:
                result = _sp.run(
                    ["tmux", "list-sessions", "-F", "#{session_name}"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if line.startswith("odin-plan-"):
                        _sp.run(
                            ["tmux", "kill-session", "-t", line],
                            capture_output=True, timeout=5,
                        )
            except Exception:
                pass

            # Wait for odin to finish (read plan, create tasks, exit).
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self._proc.isalive():
                    return
                time.sleep(0.5)
            # Process didn't exit in time — force-kill.
            self.terminate()

        threading.Thread(target=_kill_tmux_and_wait, daemon=True).start()

    def terminate(self):
        self._running = False
        if self._proc and self._proc.isalive():
            self._proc.terminate(force=True)
