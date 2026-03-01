"""Abstract base for execution strategies."""

import os
import signal
import time
from abc import ABC, abstractmethod


def spawn_summarize_subprocess(task) -> None:
    """Spawn ``odin summarize <task_id>`` as a detached subprocess.

    Shared implementation used by ExecutionStrategy.trigger_summarize()
    and as a direct fallback when no strategy is configured.
    """
    import subprocess
    from pathlib import Path
    from django.conf import settings
    from .utils import resolve_working_dir
    from ..utils.logger import logger

    cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")
    working_dir = resolve_working_dir(task)

    cmd = [cli_path, "summarize", str(task.id)]

    logger.important(
        "Triggering odin summarize: cmd=%s, cwd=%s, task_id=%s",
        cmd, working_dir, task.id,
    )

    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"summarize_task_{task.id}.log"

    try:
        with open(log_file, "w") as f:
            proc = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        logger.info(
            "Odin summarize subprocess started: PID=%s, log=%s", proc.pid, log_file
        )
    except Exception:
        logger.error(
            "Failed to start odin summarize subprocess for task %s",
            task.id, exc_info=True,
        )


class ExecutionStrategy(ABC):
    """Interface for triggering Odin execution when a task moves to IN_PROGRESS."""

    @abstractmethod
    def trigger(self, task) -> None:
        """Fire-and-forget execution of a task.

        Args:
            task: Django Task model instance with id, assignee, etc.
        """
        ...

    @abstractmethod
    def stop(self, task, force: bool = False) -> dict:
        """Best-effort stop of an active execution.

        Returns:
            dict: {"ok": bool, "engine": str, "details"?: str, "error"?: str}
        """
        ...

    def trigger_summarize(self, task) -> None:
        """Fire-and-forget summarize of a task.

        Default: runs ``odin summarize <task_id>`` as a detached subprocess.
        Subclasses may override to use Celery or other dispatch mechanisms.
        """
        spawn_summarize_subprocess(task)


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, force: bool = False, timeout_s: float = 8.0) -> bool:
    """Terminate a process group started via start_new_session=True."""
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    if force:
        return True

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.killpg(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        return False
