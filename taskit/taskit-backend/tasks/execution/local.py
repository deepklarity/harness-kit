"""Local subprocess execution strategy."""

import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings

from .base import ExecutionStrategy, _terminate_pid
from .utils import resolve_working_dir
from ..utils.logger import logger


class LocalOdinStrategy(ExecutionStrategy):
    """Trigger Odin execution via local subprocess.

    Runs `odin exec <task_id>` as a detached background process.
    Logs subprocess output to logs/odin_exec_<task_id>.log.
    """

    def trigger(self, task) -> None:
        cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")
        run_token = uuid.uuid4().hex

        working_dir = resolve_working_dir(task)

        # Write resolved execution context back to task.metadata so the UI can display it
        md = dict(task.metadata or {})
        if working_dir and not md.get("working_dir"):
            md["working_dir"] = working_dir
        md.pop("ignore_execution_results", None)
        md.pop("stopped_run_token", None)
        md.pop("execution_stopped_at", None)
        md["active_execution"] = {
            "strategy": "local",
            "run_token": run_token,
            "cancel_requested": False,
        }
        if md != (task.metadata or {}):
            task.metadata = md
            task.save(update_fields=["metadata"])

        cmd = [cli_path, "exec", str(task.id)]

        logger.important(
            "Triggering odin execution: cmd=%s, cwd=%s, task_id=%s, assignee=%s",
            cmd, working_dir, task.id, getattr(task, "assignee_id", None),
        )

        # Log output to spec-named file for easy discovery
        log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        spec_tag = f"spec_{task.spec_id}" if task.spec_id else "no_spec"
        log_file = log_dir / f"{spec_tag}_task_{task.id}.log"
        env = os.environ.copy()
        env["ODIN_TASK_RUN_TOKEN"] = run_token

        try:
            with open(log_file, "w") as f:
                proc = subprocess.Popen(
                    cmd,
                    cwd=working_dir,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
            md = dict(task.metadata or {})
            active = dict(md.get("active_execution") or {})
            active["pid"] = proc.pid
            md["active_execution"] = active
            task.metadata = md
            task.save(update_fields=["metadata"])
            logger.info(
                "Odin subprocess started: PID=%s, log=%s", proc.pid, log_file
            )
        except Exception:
            logger.error(
                "Failed to start odin subprocess for task %s", task.id, exc_info=True
            )

    def stop(self, task, force: bool = False) -> dict:
        md = dict(task.metadata or {})
        active = dict(md.get("active_execution") or {})
        pid = active.get("pid")
        if not pid:
            return {
                "ok": False,
                "engine": "local",
                "error": "No active subprocess PID found for this task.",
            }

        active["cancel_requested"] = True
        md["active_execution"] = active
        task.metadata = md
        task.save(update_fields=["metadata"])

        if _terminate_pid(int(pid), force=force):
            return {
                "ok": True,
                "engine": "local",
                "details": f"Stopped process group rooted at PID {pid}.",
            }
        return {
            "ok": False,
            "engine": "local",
            "error": f"Failed to terminate PID {pid}.",
        }
