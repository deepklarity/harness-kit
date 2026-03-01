"""Tmux session management for agent execution.

Each CLI task runs inside a named tmux session (``odin-<task_id>``),
giving full terminal visibility.  Users can attach to watch live output
or interact, and detach without stopping the task.

tmux is required for CLI agent execution.  The orchestrator checks
``is_available()`` and raises an error if tmux is missing.
"""

import asyncio
import logging
import os
import shlex
import shutil
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


SESSION_PREFIX = "odin-"


def is_available() -> bool:
    """Check if tmux is on PATH."""
    return shutil.which("tmux") is not None


def session_name(task_id: str) -> str:
    """Generate a tmux session name for a task."""
    return f"{SESSION_PREFIX}{task_id[:8]}"


async def launch(
    cmd: List[str],
    working_dir: str,
    task_id: str,
    output_file: str,
    env_unset: Optional[List[str]] = None,
) -> str:
    """Launch a command in a new detached tmux session.

    Output is piped through ``tee`` so it appears in both the terminal
    (visible when attached) and the log file (for ``odin tail``).

    A marker file (``<output_file>.exit``) is written with the command's
    exit code when it finishes.

    Returns the session name.
    """
    sess = session_name(task_id)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    # Build wrapper script
    unset_lines = ""
    if env_unset:
        unset_lines = " ".join(f"unset {v};" for v in env_unset) + "\n"

    marker = output_file + ".exit"
    script_content = (
        "#!/usr/bin/env bash\n"
        "set -o pipefail\n"
        f"{unset_lines}"
        f"{shlex.join(cmd)} 2>&1 | tee {shlex.quote(output_file)}\n"
        f"echo $? > {shlex.quote(marker)}\n"
    )

    script_dir = Path(output_file).parent
    script_path = script_dir / f"tmux_{task_id[:8]}.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # Clean up stale session if one exists from a previous run
    if await has_session(task_id):
        if await _session_has_clients(sess):
            raise RuntimeError(
                f"tmux session '{sess}' is in use (someone is attached). "
                f"Detach or kill it manually: tmux kill-session -t {sess}"
            )
        # Stale session with no one attached — safe to reclaim
        await kill_session(task_id)

    tmux_cmd = [
        "tmux", "new-session", "-d",
        "-s", sess,
        "-c", working_dir,
        str(script_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *tmux_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to create tmux session '{sess}': {stderr.decode().strip()}"
        )

    return sess


def _read_tail(path: Path, num_bytes: int = 8192) -> str:
    """Read the last *num_bytes* of a file.  O(1) regardless of file size."""
    try:
        size = path.stat().st_size
    except (OSError, FileNotFoundError):
        return ""
    if size == 0:
        return ""
    with open(path, "rb") as f:
        offset = max(0, size - num_bytes)
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


async def wait_for_exit(
    task_id: str,
    output_file: str,
    timeout: Optional[float] = None,
    completion_checker: Optional[Callable[[str], bool]] = None,
) -> int:
    """Block until the tmux session exits, then return the process exit code.

    Polls ``tmux has-session`` at 0.5 s intervals.  When the session is
    gone, reads the exit code from the marker file.  If timeout is None,
    waits indefinitely. Returns -1 on timeout (and kills the session).

    If *completion_checker* is provided, it is called every ~2 seconds with
    the tail of the output file.  When the checker returns True, a 10-second
    grace period starts — if the session doesn't exit naturally within that
    window, it is killed and exit code 0 is returned (the agent finished
    successfully; only child processes like MCP servers were lingering).
    """
    marker = Path(output_file + ".exit")
    output_path = Path(output_file)
    elapsed = 0.0
    interval = 0.5
    check_interval = 2.0  # how often to run the completion checker
    since_last_check = 0.0
    grace_period = 10.0
    completion_detected_at: Optional[float] = None

    while timeout is None or elapsed < timeout:
        if not await has_session(task_id):
            # Session ended — brief pause for file flush, then read marker
            await asyncio.sleep(0.3)
            if marker.exists():
                try:
                    return int(marker.read_text().strip())
                except (ValueError, FileNotFoundError):
                    return 1
            # If completion was detected, the agent succeeded
            if completion_detected_at is not None:
                return 0
            return 1

        # Check for completion via output content
        if completion_checker and completion_detected_at is None:
            since_last_check += interval
            if since_last_check >= check_interval:
                since_last_check = 0.0
                tail = _read_tail(output_path)
                if tail and completion_checker(tail):
                    completion_detected_at = elapsed
                    logger.info(
                        "Completion detected for task %s at %.1fs — "
                        "starting %.0fs grace period",
                        task_id[:8], elapsed, grace_period,
                    )

        # Grace period expired — force kill
        if completion_detected_at is not None:
            if elapsed - completion_detected_at >= grace_period:
                logger.info(
                    "Grace period expired for task %s — killing session "
                    "(MCP children likely lingering)",
                    task_id[:8],
                )
                await kill_session(task_id)
                return 0

        await asyncio.sleep(interval)
        elapsed += interval

    # Timeout — kill the session
    await kill_session(task_id)
    return -1


async def has_session(task_id: str) -> bool:
    """Check if a tmux session for this task is alive."""
    sess = session_name(task_id)
    proc = await asyncio.create_subprocess_exec(
        "tmux", "has-session", "-t", sess,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


async def _session_has_clients(sess: str) -> bool:
    """Check if a tmux session has any attached clients."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "list-clients", "-t", sess,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    # list-clients outputs one line per client; empty = no one attached
    return bool(stdout and stdout.strip())


async def kill_session(task_id: str) -> bool:
    """Kill a task's tmux session.  Returns True if the session existed."""
    sess = session_name(task_id)
    proc = await asyncio.create_subprocess_exec(
        "tmux", "kill-session", "-t", sess,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


def attach_sync(task_id: str) -> None:
    """Attach to a task's tmux session (replaces current process)."""
    sess = session_name(task_id)
    os.execvp("tmux", ["tmux", "attach-session", "-t", sess])


def launch_and_attach(
    cmd: List[str],
    working_dir: str,
    session_id: str,
    output_file: str,
    env_unset: Optional[List[str]] = None,
    initial_message_file: Optional[str] = None,
) -> int:
    """Launch interactive command in tmux and attach (blocking).

    Uses script(1) to capture a full transcript while keeping the
    terminal interactive. Blocks until the user exits, then returns
    the exit code.

    Command args containing ``__FILE__:/path`` are expanded inline in
    the wrapper script via ``$(cat /path)`` so that long text (like
    system prompts) doesn't hit shell escaping limits.

    If *initial_message_file* is given, its contents are pasted into the
    tmux pane as the first user message after the CLI starts up.
    """
    import platform
    import subprocess
    import time as _time

    sess = f"{SESSION_PREFIX}plan-{session_id[:8]}"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    # Build wrapper script that uses script(1) for transcript capture
    unset_lines = ""
    if env_unset:
        unset_lines = " ".join(f"unset {v};" for v in env_unset) + "\n"

    marker = output_file + ".exit"
    escaped_output = shlex.quote(output_file)
    escaped_marker = shlex.quote(marker)

    # Build the command string, expanding __FILE__: markers
    cmd_parts = []
    for arg in cmd:
        if arg.startswith("__FILE__:"):
            file_path = arg[len("__FILE__:"):]
            # Use $(cat <file>) so the shell reads the file at runtime
            cmd_parts.append('"$(cat ' + shlex.quote(file_path) + ')"')
        else:
            cmd_parts.append(shlex.quote(arg))
    cmd_str = " ".join(cmd_parts)

    # macOS and Linux have different script(1) syntax
    if platform.system() == "Darwin":
        # macOS script: script -q <file> <command...>
        script_line = f"script -q {escaped_output} bash -c {shlex.quote(cmd_str)}"
    else:
        script_line = f"script -q -c {shlex.quote(cmd_str)} {escaped_output}"

    script_content = (
        "#!/usr/bin/env bash\n"
        f"{unset_lines}"
        f"{script_line}\n"
        f"echo $? > {escaped_marker}\n"
    )

    script_dir = Path(output_file).parent
    script_path = script_dir / f"tmux_plan_{session_id[:8]}.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # Create detached tmux session
    tmux_new = [
        "tmux", "new-session", "-d",
        "-s", sess,
        "-c", working_dir,
        str(script_path),
    ]

    result = subprocess.run(
        tmux_new,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create tmux session '{sess}': {result.stderr.strip()}"
        )

    # Send initial message if provided (paste spec into the agent prompt)
    if initial_message_file:
        if not _wait_for_prompt(sess):
            # Timeout — fall back to sending anyway (user can re-type)
            logger.warning(
                "Timed out waiting for CLI prompt in session %s — "
                "sending initial message anyway",
                sess,
            )
        _send_initial_message(sess, initial_message_file)

    # Attach in the foreground — blocks until the session ends
    try:
        attach_result = subprocess.run(
            ["tmux", "attach-session", "-t", sess],
        )
    except KeyboardInterrupt:
        # User hit Ctrl+C while attached — kill the session
        subprocess.run(
            ["tmux", "kill-session", "-t", sess],
            capture_output=True,
        )
        return 1

    # Read exit code from marker file
    marker_path = Path(marker)
    if marker_path.exists():
        try:
            return int(marker_path.read_text().strip())
        except (ValueError, FileNotFoundError):
            return 1
    return attach_result.returncode


def _wait_for_prompt(session: str, timeout: float = 30, interval: float = 0.5) -> bool:
    """Poll tmux pane content until the CLI prompt appears ready.

    Checks for common ready indicators: '>' prompt, '?' prompt,
    or any content suggesting the CLI has rendered its input area.
    Returns True if ready, False on timeout.
    """
    import subprocess
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            content = result.stdout.strip()
            # Claude Code shows ">" when ready for input.
            # Also check for "?" (permission prompts) or the word "tips"
            # which appears in the startup banner.
            if content and (">" in content or "?" in content or "tips" in content.lower()):
                return True
        _time.sleep(interval)
    return False


def _send_initial_message(session: str, message_file: str) -> None:
    """Send the contents of a file as the first message in a tmux session.

    Uses tmux load-buffer + paste-buffer to handle multi-line text
    reliably, then sends Enter to submit.
    """
    import subprocess

    # Load file into tmux buffer
    subprocess.run(
        ["tmux", "load-buffer", message_file],
        capture_output=True,
    )
    # Paste the buffer into the pane
    subprocess.run(
        ["tmux", "paste-buffer", "-t", session],
        capture_output=True,
    )
    # Send Enter to submit the message
    subprocess.run(
        ["tmux", "send-keys", "-t", session, "Enter"],
        capture_output=True,
    )
