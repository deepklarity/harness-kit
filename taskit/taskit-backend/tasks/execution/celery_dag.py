"""Celery DAG execution strategy — no-op trigger.

When this strategy is active, moving a task to IN_PROGRESS does NOT
immediately fire `odin exec`. Instead, the DAG executor (Celery Beat task)
polls for IN_PROGRESS tasks, checks dependency satisfaction, and fires
execution only when all dependencies are met.
"""

import logging

try:
    from celery.result import AsyncResult
except Exception:  # pragma: no cover - celery optional in some test/dev contexts
    AsyncResult = None

from .base import ExecutionStrategy, _terminate_pid

logger = logging.getLogger(__name__)


class CeleryDAGStrategy(ExecutionStrategy):
    """No-op trigger — the DAG executor Celery Beat task handles execution."""

    def trigger(self, task) -> None:
        logger.info(
            "Task %s moved to IN_PROGRESS — DAG executor will pick it up "
            "(assignee=%s, deps=%s)",
            task.id,
            getattr(task, "assignee_id", None),
            task.depends_on,
        )

    def trigger_summarize(self, task) -> None:
        from tasks.dag_executor import summarize_single_task
        logger.info("Task %s: dispatching summarize via Celery", task.id)
        summarize_single_task.delay(task.id)

    def stop(self, task, force: bool = False) -> dict:
        md = dict(task.metadata or {})
        active = dict(md.get("active_execution") or {})
        active["cancel_requested"] = True
        md["active_execution"] = active
        task.metadata = md
        task.save(update_fields=["metadata"])

        celery_task_id = active.get("celery_task_id")
        if celery_task_id and AsyncResult is not None:
            try:
                AsyncResult(celery_task_id).revoke(terminate=True, signal="SIGTERM")
            except Exception:
                logger.exception("Failed to revoke celery task %s", celery_task_id)

        pid = active.get("pid")
        if pid:
            if _terminate_pid(int(pid), force=force):
                return {
                    "ok": True,
                    "engine": "celery_dag",
                    "details": f"Stopped process group rooted at PID {pid}.",
                }
            return {
                "ok": False,
                "engine": "celery_dag",
                "error": f"Failed to terminate PID {pid}.",
            }

        # Queued but not yet started is considered a successful stop after revoke.
        if celery_task_id:
            return {
                "ok": True,
                "engine": "celery_dag",
                "details": f"Revoked queued Celery task {celery_task_id}.",
            }

        return {
            "ok": False,
            "engine": "celery_dag",
            "error": "No active execution metadata found (missing celery task id/pid).",
        }
