"""DAG-aware task executor — Celery tasks for dependency-ordered execution.

Two Celery tasks:
1. poll_and_execute (scheduled by Beat every 5s): finds IN_PROGRESS tasks
   with satisfied dependencies, transitions them to EXECUTING, and fires
   individual execute_single_task calls.
2. execute_single_task: runs `odin exec <task_id>` as a subprocess and
   transitions the task to REVIEW (success) or FAILED (error).

Status lifecycle (never skip a step):
    TODO → IN_PROGRESS → EXECUTING → REVIEW/FAILED

The DAG executor never touches TODO tasks. Moving a task to IN_PROGRESS is
an explicit human or odin action (e.g., `odin plan --quick`, drag on kanban).
"""

import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

try:
    from celery import shared_task
except ImportError:
    # Fallback: make functions callable without Celery (tests, dev without Redis)
    def shared_task(*args, **kwargs):
        def decorator(func):
            func.delay = lambda *a, **kw: func(*a, **kw)
            return func
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .dependencies import DepStatus, check_deps
from .execution.utils import resolve_working_dir
from .kanban_ordering import move_task
from .models import CommentType, Task, TaskComment, TaskHistory, TaskStatus
from .scheduling import maybe_finalize_schedule_run
from .utils.logger import setup_logger

logger = setup_logger("taskit.dag_executor")
_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
LOG_TAIL_MAX_LINES = 12
LOG_TAIL_MAX_CHARS = 600
CANCEL_POLL_INTERVAL_SECONDS = 1.0


@shared_task(name="tasks.dag_executor.poll_and_execute")
def poll_and_execute():
    """Poll for IN_PROGRESS tasks ready for execution.

    Scheduled by Celery Beat every N seconds. Checks dependency satisfaction,
    respects concurrency limits, and fires execute_single_task for ready tasks.

    Only IN_PROGRESS tasks are considered. TODO tasks are never touched —
    moving a task to IN_PROGRESS is an explicit action by the user or odin.
    """
    max_concurrency = getattr(settings, "DAG_EXECUTOR_MAX_CONCURRENCY", 3)

    executing_count = Task.objects.filter(status=TaskStatus.EXECUTING).count()
    available_slots = max_concurrency - executing_count
    if available_slots <= 0:
        logger.debug("No available slots (executing=%d, max=%d)", executing_count, max_concurrency)
        return

    candidates = Task.objects.select_related("board").filter(
        status=TaskStatus.IN_PROGRESS,
    ).order_by("created_at")

    logger.debug(
        "poll_and_execute: executing=%d, available_slots=%d, max=%d",
        executing_count, available_slots, max_concurrency,
    )

    ready_tasks = []
    for task in candidates:
        if len(ready_tasks) >= available_slots:
            break
        if not task.assignee_id:
            continue

        dep_status = check_deps(task)
        if dep_status == DepStatus.READY:
            ready_tasks.append(task)
        else:
            logger.debug(
                "[task:%s] Dep check: %s", task.id, dep_status.name,
            )

    if not ready_tasks:
        return

    for task in ready_tasks:
        run_token = uuid.uuid4().hex
        with transaction.atomic():
            locked_task = Task.objects.select_for_update().get(id=task.id)
            if locked_task.status != TaskStatus.IN_PROGRESS:
                continue

            metadata = dict(locked_task.metadata or {})
            metadata.pop("ignore_execution_results", None)
            metadata.pop("stopped_run_token", None)
            metadata.pop("execution_stopped_at", None)
            metadata["active_execution"] = {
                "strategy": "celery_dag",
                "run_token": run_token,
                "queued_at": time.time(),
                "cancel_requested": False,
            }
            locked_task.status = TaskStatus.EXECUTING
            locked_task.metadata = metadata
            locked_task.save(update_fields=["status", "metadata", "last_updated_at"])

            TaskHistory.objects.create(
                task=locked_task,
                schedule_run=locked_task.current_schedule_run,
                field_name="status",
                old_value=TaskStatus.IN_PROGRESS,
                new_value=TaskStatus.EXECUTING,
                changed_by="odin+dag-executor@system",
            )

        # Create worktree if spec has a branch (worktree isolation)
        if locked_task.spec and (locked_task.spec.metadata or {}).get("branch"):
            try:
                worktree_path = _create_task_worktree(locked_task)
                if worktree_path:
                    metadata = dict(locked_task.metadata or {})
                    metadata["working_dir"] = str(worktree_path)
                    metadata["worktree_path"] = str(worktree_path)
                    metadata["branch"] = f"task/{locked_task.spec.odin_id}/{locked_task.id}"
                    metadata["merge_status"] = "pending"
                    locked_task.metadata = metadata
                    locked_task.save(update_fields=["metadata"])
            except Exception:
                logger.exception("Worktree creation failed for task %s, running in project root", locked_task.id)

        logger.info("Task %s: IN_PROGRESS → EXECUTING, firing execution", task.id)
        async_result = execute_single_task.delay(task.id, run_token)
        latest = Task.objects.get(id=task.id)
        metadata = dict(latest.metadata or {})
        active = dict(metadata.get("active_execution") or {})
        if active.get("run_token") == run_token:
            active["celery_task_id"] = async_result.id
            metadata["active_execution"] = active
            latest.metadata = metadata
            latest.save(update_fields=["metadata"])


@shared_task(name="tasks.dag_executor.execute_single_task")
def execute_single_task(task_id, run_token=None):
    """Execute a single task via `odin exec <task_id>`.

    On success: transitions to REVIEW (human QA gate).
    On failure: transitions to FAILED.
    Odin's own execution logic handles the detailed status updates
    and comment recording — this is the outer wrapper.
    """
    try:
        task = Task.objects.select_related("board").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("Task %s not found for execution", task_id)
        return

    if task.status != TaskStatus.EXECUTING:
        logger.warning("Task %s is %s, expected EXECUTING — skipping", task_id, task.status)
        return

    active_exec = dict((task.metadata or {}).get("active_execution") or {})
    if run_token and active_exec.get("run_token") and active_exec.get("run_token") != run_token:
        logger.warning(
            "Task %s run_token mismatch (expected=%s got=%s) — skipping",
            task_id, active_exec.get("run_token"), run_token,
        )
        return

    cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")

    working_dir = resolve_working_dir(task)

    # Write resolved execution context back to task.metadata so the UI can display it
    md = dict(task.metadata or {})
    if working_dir and not md.get("working_dir"):
        md["working_dir"] = working_dir
    active = dict(md.get("active_execution") or {})
    if run_token and not active.get("run_token"):
        active["run_token"] = run_token
    md["active_execution"] = active
    if md != (task.metadata or {}):
        task.metadata = md
        task.save(update_fields=["metadata"])

    cmd = [cli_path, "exec", str(task.id)]

    # Log output to spec-named file for easy discovery
    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)
    spec_tag = f"spec_{task.spec_id}" if task.spec_id else "no_spec"
    log_file = log_dir / f"{spec_tag}_task_{task.id}.log"

    logger.info(
        "Executing task %s: cmd=%s, cwd=%s, log=%s, run_token=%s",
        task.id, cmd, working_dir, log_file.name, run_token or "-",
    )

    exit_code, failure_stage = _run_subprocess_with_cancellation(
        task_id=task.id,
        cmd=cmd,
        working_dir=working_dir,
        log_file=log_file,
        run_token=run_token,
    )

    # Re-read task to check if odin already updated the status
    task.refresh_from_db()
    if task.status != TaskStatus.EXECUTING:
        _append_summary(log_file, task, exit_code)
        logger.info("Task %s status already changed to %s by odin", task_id, task.status)
        return

    # Check for a user-requested stop that landed in metadata before the
    # process exited. The stop_execution endpoint writes these flags *before*
    # signalling the process, so we MUST honor them here — this is not a
    # failure, it is a deliberate stop, and we route the task to the user's
    # target status instead of FAILED.
    metadata = dict(task.metadata or {})
    pending_stop_target = metadata.get("pending_stop_target")
    guard_honored = bool(metadata.get("ignore_execution_results") and pending_stop_target)

    if guard_honored:
        new_status = pending_stop_target
        actor = metadata.get("pending_stop_updated_by") or "odin+dag-executor@system"
        stop_reason = metadata.get("pending_stop_reason") or "user_stop"
        verb = "Stopped"
        excerpt = ""
    elif exit_code == 0:
        new_status = TaskStatus.REVIEW
        actor = "odin+dag-executor@system"
        verb = "Completed"
        excerpt = ""
    else:
        new_status = TaskStatus.FAILED
        actor = "odin+dag-executor@system"
        verb = "Failed"
        excerpt = _read_log_tail(log_file)

    task.kanban_position = move_task(task, target_status=new_status, target_index=None)
    task.status = new_status
    metadata.pop("active_execution", None)

    if guard_honored:
        # We've applied the user's intent — clear the pending_* keys so they
        # don't leak into a future execution. Leave ignore_execution_results
        # and stopped_run_token intact so any late execution_result webhook
        # still gets discarded by downstream guards.
        metadata.pop("pending_stop_target", None)
        metadata.pop("pending_stop_updated_by", None)
        metadata.pop("pending_stop_reason", None)
        metadata["stop_generation"] = int(metadata.get("stop_generation", 0) or 0) + 1
        metadata["last_stop_request"] = {
            "actor": actor,
            "target_status": new_status,
            "reason": stop_reason,
            "at": timezone.now().isoformat(),
            "origin": "taskit_dag_executor",
        }
        logger.info(
            "[task:%s] User stop honored in dag_executor: target=%s actor=%s",
            task_id, new_status, actor,
        )
    elif new_status == TaskStatus.FAILED:
        failure_type, reason = _classify_failure(exit_code, failure_stage, excerpt)
        metadata["last_failure_type"] = failure_type
        metadata["last_failure_reason"] = reason
        metadata["last_failure_origin"] = "taskit_dag_executor"
        logger.info(
            "[task:%s] Fallback failure synthesized: type=%s stage=%s reason=%s",
            task_id, failure_type, failure_stage, reason[:200],
        )

    task.metadata = metadata
    task.save(update_fields=["status", "kanban_position", "metadata", "last_updated_at"])

    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="status",
        old_value=TaskStatus.EXECUTING,
        new_value=new_status,
        changed_by=actor,
    )

    # Merge is deferred until reflection passes (REVIEW → TESTING) so that
    # reflection-driven re-executions are included in the spec branch.
    # See views.py _merge_task_on_reflection_pass().

    if guard_honored:
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email=actor,
            author_label="taskit-dag-executor",
            content=f"Execution stopped by user. Status changed to {new_status}.",
            comment_type=CommentType.STATUS_UPDATE,
        )
    elif new_status == TaskStatus.REVIEW:
        from .views import _trigger_auto_reflection
        _trigger_auto_reflection(task)

    # On failure, preserve the worktree so a human can inspect it.
    # The branch is already preserved by not merging.

    if not guard_honored and new_status == TaskStatus.FAILED:
        failure_type = metadata.get("last_failure_type", "agent_execution_failure")
        reason = metadata.get("last_failure_reason", f"odin exec exited with code {exit_code}")
        body = [
            f"Failed: {reason}",
            f"Failure type: {failure_type}",
            "Reason: " + reason,
            "Origin: taskit_dag_executor",
        ]
        if excerpt:
            body.append(f"Debug: {excerpt}")
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="odin+dag-executor@system",
            author_label="odin-dag-executor",
            content="\n".join(body),
        )
    maybe_finalize_schedule_run(task, new_status)

    task.refresh_from_db()
    _append_summary(log_file, task, exit_code)
    logger.info("Task %s %s (exit_code=%s) → %s", task_id, verb.lower(), exit_code, new_status)


@shared_task(name="tasks.dag_executor.summarize_single_task")
def summarize_single_task(task_id):
    """Run ``odin summarize <task_id>`` as a subprocess.

    Unlike execute_single_task, this does not change task status — the task
    stays in whatever status it's currently in.  The result is a new comment
    with comment_type=summary posted by the odin orchestrator.
    """
    try:
        task = Task.objects.select_related("board").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("Task %s not found for summarize", task_id)
        return

    cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")

    working_dir = resolve_working_dir(task)

    cmd = [cli_path, "summarize", str(task.id)]

    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"summarize_task_{task.id}.log"

    logger.info("Summarizing task %s: cmd=%s, cwd=%s", task.id, cmd, working_dir)

    try:
        with open(log_file, "w") as f:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
        if result.returncode == 0:
            logger.info("Summarize completed for task %s", task_id)
        else:
            logger.warning(
                "Summarize failed for task %s (exit_code=%s)", task_id, result.returncode
            )
    except subprocess.TimeoutExpired:
        logger.error("Summarize timed out for task %s", task_id)
    except Exception:
        logger.exception("Failed to run odin summarize for task %s", task_id)

def _run_subprocess_with_cancellation(task_id, cmd, working_dir, log_file, run_token=None):
    """Run odin exec while supporting stop requests via task metadata."""
    failure_stage = "none"
    env = os.environ.copy()
    if run_token:
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

            task = Task.objects.get(id=task_id)
            metadata = dict(task.metadata or {})
            active = dict(metadata.get("active_execution") or {})
            if run_token and not active.get("run_token"):
                active["run_token"] = run_token
            active["pid"] = proc.pid
            metadata["active_execution"] = active
            task.metadata = metadata
            task.save(update_fields=["metadata"])

            while True:
                try:
                    exit_code = proc.wait(timeout=CANCEL_POLL_INTERVAL_SECONDS)
                    if exit_code != 0 and failure_stage == "none":
                        failure_stage = "odin_non_zero_exit"
                    return exit_code, failure_stage
                except subprocess.TimeoutExpired:
                    task.refresh_from_db()
                    active = dict((task.metadata or {}).get("active_execution") or {})
                    if run_token and active.get("run_token") and active.get("run_token") != run_token:
                        _terminate_process(proc, force=True)
                        return -1, "run_token_mismatch"
                    if active.get("cancel_requested"):
                        _terminate_process(proc, force=False)
                        return -1, "cancelled"
    except Exception:
        logger.exception("Failed to execute task %s", task_id)
        return -1, "spawn_exception"


def _terminate_process(proc, force=False):
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        logger.exception("Failed to send %s to pid=%s", sig, proc.pid)
        return

    if force:
        return

    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass


@shared_task(name="tasks.dag_executor.execute_reflection")
def execute_reflection(report_id):
    """Execute a reflection audit via `odin reflect` as a Celery task.

    Mirrors execute_single_task pattern: blocking subprocess.run() inside
    a Celery worker, with timeout and fallback status handling.
    """
    from .models import ReflectionReport, ReflectionStatus
    from django.utils import timezone

    try:
        report = ReflectionReport.objects.select_related("task", "task__board").get(id=report_id)
    except ReflectionReport.DoesNotExist:
        logger.error("ReflectionReport %s not found", report_id)
        return

    if report.status != ReflectionStatus.PENDING:
        logger.info("ReflectionReport %s is %s, expected PENDING — skipping", report_id, report.status)
        return

    report.status = ReflectionStatus.RUNNING
    report.save(update_fields=["status"])

    task = report.task
    cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")

    working_dir = resolve_working_dir(task)

    cmd = [
        cli_path, "reflect", str(task.id),
        "--report-id", str(report.id),
        "--model", report.reviewer_model,
        "--agent", report.reviewer_agent,
    ]

    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"reflect_{task.id}_{report.id}.log"

    logger.info(
        "Executing reflection: cmd=%s, cwd=%s, report_id=%s", cmd, working_dir, report.id
    )

    try:
        with open(log_file, "w") as f:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        logger.error("Reflection %s timed out after 300s", report_id)
        exit_code = -1
    except Exception:
        logger.exception("Failed to execute reflection %s", report_id)
        exit_code = -1

    # Odin normally PATCHes the report itself. If it didn't, set a fallback status.
    report.refresh_from_db()
    if report.status == ReflectionStatus.RUNNING:
        report.status = ReflectionStatus.FAILED if exit_code != 0 else ReflectionStatus.COMPLETED
        report.error_message = "Reflection process exited without updating report"
        report.completed_at = timezone.now()
        report.save(update_fields=["status", "error_message", "completed_at"])
        logger.warning("Reflection %s fallback status: %s (exit_code=%s)", report_id, report.status, exit_code)
    else:
        logger.info("Reflection %s completed with status: %s", report_id, report.status)


@shared_task(name="tasks.dag_executor.merge_task_on_reflection")
def merge_task_on_reflection(task_id):
    """Merge a task branch into its spec branch, then advance REVIEW → TESTING.

    Runs in the Celery worker where odin is importable.
    Called after reflection passes.  The status transition is deferred to
    *after* the merge so that downstream tasks never fork from a spec
    branch that is missing upstream code.
    """
    from .models import CommentType

    try:
        task = Task.objects.select_related("spec", "board").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("merge_task_on_reflection: Task %s not found", task_id)
        return

    branch = (task.metadata or {}).get("branch")
    if not branch:
        # No branch — nothing to merge, but still advance status.
        _advance_task_to_testing(task)
        return

    if (task.metadata or {}).get("merge_status") == "merged":
        # Already merged (duplicate dispatch) — ensure status is advanced.
        _advance_task_to_testing(task)
        return

    try:
        merge_result = _merge_task_branch(task)
        metadata = dict(task.metadata or {})
        if merge_result.success and merge_result.noop:
            metadata["merge_status"] = "noop"
        elif merge_result.success:
            metadata["merge_status"] = "merged"
        elif merge_result.conflict:
            metadata["merge_status"] = "conflict"
        else:
            metadata["merge_status"] = "error"
        if merge_result.error:
            metadata["merge_error"] = merge_result.error
        if merge_result.diff_stat:
            metadata["diff_stat"] = merge_result.diff_stat
        task.metadata = metadata
        task.save(update_fields=["metadata"])
        logger.info("[task:%s] Post-reflection merge: %s", task.id, metadata["merge_status"])

        # Post a comment so the merge result is visible in the task timeline
        spec_branch = f"spec/{task.spec.odin_id}" if task.spec else "spec branch"
        if merge_result.success and not merge_result.noop:
            comment_text = f"Merged `{branch}` into `{spec_branch}`"
            if merge_result.diff_stat:
                comment_text += f"\n```\n{merge_result.diff_stat}\n```"
        elif merge_result.success and merge_result.noop:
            comment_text = (
                f"No changes to merge from `{branch}` "
                f"(branch already up to date with `{spec_branch}`)"
            )
        elif merge_result.conflict:
            comment_text = (
                f"Merge conflict merging `{branch}` into `{spec_branch}`: "
                f"{merge_result.error or 'see logs'}"
            )
        else:
            comment_text = (
                f"Merge failed for `{branch}` into `{spec_branch}`: "
                f"{merge_result.error or 'unknown error'}"
            )
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="system@taskit",
            author_label="system",
            content=comment_text,
            comment_type=CommentType.STATUS_UPDATE,
        )

        # Update spec worktree to reflect the merged code
        if merge_result.success and not merge_result.noop:
            try:
                wt = _get_worktree_manager(task)
                if wt:
                    wt.update_spec_worktree(task.spec.odin_id)
            except Exception:
                logger.warning(
                    "Failed to update spec worktree after merge for task %s",
                    task.id, exc_info=True,
                )

        # Advance REVIEW → TESTING only after merge succeeds (or noop).
        # On conflict/error the task stays in REVIEW so the user can
        # resolve the issue before downstream tasks fork.
        if merge_result.success:
            _advance_task_to_testing(task)
        else:
            logger.warning(
                "Task %s stays in REVIEW — merge %s: %s",
                task.id, metadata["merge_status"], merge_result.error,
            )
    except Exception as exc:
        logger.exception("Post-reflection merge failed for task %s", task.id)
        metadata = dict(task.metadata or {})
        metadata["merge_status"] = "error"
        task.metadata = metadata
        task.save(update_fields=["metadata"])
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="system@taskit",
            author_label="system",
            content=f"Post-reflection merge failed: {exc}",
            comment_type=CommentType.STATUS_UPDATE,
        )


def _advance_task_to_testing(task):
    """Transition task REVIEW → TESTING and record history.

    Called by merge_task_on_reflection after a successful merge (or when
    no merge is needed).  Idempotent — skips if task is no longer REVIEW.
    """
    task.refresh_from_db(fields=["status"])
    if task.status != TaskStatus.REVIEW:
        logger.info(
            "Task %s already moved from REVIEW (now %s) — skipping advance",
            task.id, task.status,
        )
        return

    old_status = task.status
    task.status = TaskStatus.TESTING
    task.save(update_fields=["status"])
    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="status",
        old_value=old_status,
        new_value=TaskStatus.TESTING,
        changed_by="system@taskit",
    )
    maybe_finalize_schedule_run(task, TaskStatus.TESTING)
    logger.info(
        "Advanced task %s from REVIEW → TESTING (post-merge)",
        task.id,
    )


def _append_summary(log_file, task, exit_code):
    """Append a human-readable summary to the end of a task log file."""
    try:
        assignee_name = task.assignee.name if task.assignee else "unassigned"
        with open(log_file, "a") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"SUMMARY: task={task.id} spec={task.spec_id or 'none'}\n")
            f.write(f"  agent={assignee_name} status={task.status} exit_code={exit_code}\n")
            f.write(f"  title={task.title}\n")
            f.write(f"{'=' * 60}\n")
    except Exception:
        pass  # Best-effort — don't fail execution over logging


def _read_log_tail(
    log_file: Path,
    max_lines: int = LOG_TAIL_MAX_LINES,
    max_chars: int = LOG_TAIL_MAX_CHARS,
) -> str:
    try:
        if not log_file.exists():
            return ""
        lines = log_file.read_text(errors="replace").splitlines()
        tail = "\n".join(lines[-max_lines:])
        return _sanitize_ansi(tail)[:max_chars]
    except Exception:
        return ""


def _sanitize_ansi(text: str) -> str:
    """Strip ANSI control sequences from CLI logs before storing/displaying."""
    if not text:
        return ""
    return _ANSI_RE.sub("", text)


def _extract_actionable_reason(excerpt: str) -> str:
    """Pick the most actionable line from the fallback log excerpt.

    Priority order:
    1. Known prefix patterns (auth errors, explicit failure/reason lines)
    2. Python exception lines (last line starting with an exception class name)
    3. Empty string (caller falls back to generic message)
    """
    if not excerpt:
        return ""
    lines = [ln.strip() for ln in excerpt.splitlines() if ln.strip()]
    if not lines:
        return ""

    # 1. Known prefixes — scan bottom-up for explicit failure messages
    prefixes = (
        "authentication error:",
        "taskit returned 401 unauthorized",
        "cannot connect to taskit",
        "login failed",
        "reason:",
        "failed:",
    )
    for line in reversed(lines):
        low = line.lower()
        if low.startswith(prefixes):
            if ":" in line:
                return line.split(":", 1)[1].strip()
            return line
        if "401 unauthorized" in low:
            return line

    # 2. Python exception — last line matching ExceptionClass: message
    for line in reversed(lines):
        if "Error:" in line or "Exception:" in line:
            return line
    return ""


def _get_worktree_manager(task):
    """Instantiate a WorktreeManager from the project root (board working dir).

    Uses board.working_dir (the actual git project root) rather than
    resolve_working_dir() which may return a task-level worktree path.
    Falls back to resolve_working_dir() if no board working dir is available.
    """
    board = getattr(task, "board", None)
    working_dir = (board.working_dir if board and board.working_dir else None) or resolve_working_dir(task)
    if not working_dir:
        return None
    try:
        from odin.worktree import WorktreeManager
        project_root = Path(working_dir)
        return WorktreeManager(project_root)
    except ImportError:
        logger.warning("odin.worktree not available — cannot manage worktrees")
        return None


def _create_task_worktree(task):
    """Create a worktree for a task. Returns the worktree path or None."""
    wt = _get_worktree_manager(task)
    if not wt:
        return None
    spec_id = task.spec.odin_id
    return wt.create_task_worktree(spec_id, str(task.id))


def _merge_task_branch(task):
    """Merge a task's branch into its spec branch. Returns MergeResult."""
    wt = _get_worktree_manager(task)
    if not wt:
        try:
            from odin.worktree import MergeResult
        except ImportError:
            # Fallback: define a simple result struct when odin is not available
            class MergeResult:
                def __init__(self, success, error=None, conflict=False, noop=False, diff_stat=None):
                    self.success = success
                    self.error = error
                    self.conflict = conflict
                    self.noop = noop
                    self.diff_stat = diff_stat
        return MergeResult(success=False, error="WorktreeManager not available")
    spec_id = task.spec.odin_id
    return wt.merge_task_into_spec(spec_id, str(task.id), task.title)


def _cleanup_task_worktree(task):
    """Remove a task's worktree and branch."""
    wt = _get_worktree_manager(task)
    if wt:
        spec_id = task.spec.odin_id
        wt.cleanup_task_worktree(spec_id, str(task.id))


def _remove_task_worktree(task):
    """Remove a task's worktree but preserve its branch for recovery."""
    wt = _get_worktree_manager(task)
    if wt:
        spec_id = task.spec.odin_id
        wt.remove_task_worktree(spec_id, str(task.id))


def _transition_spec_tasks_to_done(spec, pr_url):
    """Move all TESTING tasks in a spec to DONE with PR URL comment.

    Called after the user creates a spec PR (via `odin spec finalize` or UI).
    Idempotent: only affects tasks still in TESTING.
    """
    from .models import CommentType

    tasks = Task.objects.filter(spec=spec, status=TaskStatus.TESTING)
    for task in tasks:
        old_status = task.status
        task.status = TaskStatus.DONE
        task.save(update_fields=["status", "last_updated_at"])
        TaskHistory.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            field_name="status",
            old_value=old_status,
            new_value=TaskStatus.DONE,
            changed_by="system@taskit",
        )
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="system@taskit",
            author_label="system",
            content=f"Finalized: PR {pr_url}",
            comment_type=CommentType.STATUS_UPDATE,
        )
        maybe_finalize_schedule_run(task, TaskStatus.DONE)
        logger.info("Task %s: TESTING → DONE (finalize, pr=%s)", task.id, pr_url)


def _classify_failure(exit_code: int, failure_stage: str, excerpt: str) -> tuple[str, str]:
    """Classify fallback failures and choose the best user-facing reason."""
    if failure_stage in ("cancelled", "run_token_mismatch"):
        return ("cancelled", "Execution stopped by user request")
    if failure_stage == "timeout":
        return ("timeout", "Task execution timed out after 600s")
    if failure_stage == "spawn_exception":
        return ("internal_error", "Failed to launch odin subprocess")

    reason_from_log = _extract_actionable_reason(excerpt)
    low_reason = reason_from_log.lower()
    if (
        "authentication error" in low_reason
        or "401 unauthorized" in low_reason
        or ("odin_admin_user" in low_reason and "odin_admin_password" in low_reason)
    ):
        return ("backend_auth_failure", reason_from_log or "TaskIt authentication failed")

    if reason_from_log:
        return ("agent_execution_failure", reason_from_log)
    return ("agent_execution_failure", f"odin exec exited with code {exit_code}")
