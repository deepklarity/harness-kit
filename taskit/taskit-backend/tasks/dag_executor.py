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

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
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
from django.db import transaction
from django.utils import timezone

from .dependencies import DepStatus, check_deps
from .execution.base import _terminate_pid
from .execution.utils import resolve_working_dir
from .failure_tagger import tag_failure_class
from .kanban_ordering import move_task
from .merge_recording import record_merge_attempt
from .mistakes import record_execution_mistake
from .models import BoardMembership, CommentType, MergeTrigger, ReflectionReport, ReflectionStatus, SystemSetting, Task, TaskComment, TaskHistory, TaskRun, TaskRunState, TaskStatus, User
from .scheduling import maybe_finalize_schedule_run
from .sandbox_budget import (
    default_vm_mem_mib,
    compute_reserved_mib,
    get_budget_mib,
    spawn_fits,
)
from . import task_runs
from .similarity import post_twins_comment
from .utils.logger import setup_logger

logger = setup_logger("taskit.dag_executor")
_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
LOG_TAIL_MAX_LINES = 12
LOG_TAIL_MAX_CHARS = 600
CANCEL_POLL_INTERVAL_SECONDS = 1.0
# TaskRun.last_heartbeat write throttle — piggybacks on the cancellation
# poll loop above rather than adding a second periodic mechanism.
TASK_RUN_HEARTBEAT_INTERVAL_SECONDS = 10.0

# Friendly mapping from DepStatus → dispatch_blocked_reason stamp. The UI
# surfaces this in the task metadata; F43/F44 requires that every skipped
# IN_PROGRESS task leaves a visible reason instead of a silent `continue`.
DEP_BLOCKED_REASONS = {
    DepStatus.WAITING: "deps_not_complete",
    DepStatus.BLOCKED: "deps_blocked_failed",
}


class AgentNotEnabledOnBoard(Exception):
    """Raised when dispatch / assign targets an AGENT not on the board roster (W10.4).

    Carries the agent name + board id + the settings URL string so the
    call site (poll_and_executor, TaskViewSet.assign) can render a single
    human-readable line — ``Agent 'gemini' is not enabled on board 42;
    enable it at /api/boards/42/agents/ or pick a different agent.`` —
    instead of falling back silently to another tier. Test pins this
    contract so the next "let me just remove the check" slips on a
    regression test before it reaches a reviewer.
    """

    def __init__(self, *, agent, board_id, settings_url):
        self.agent = agent
        self.board_id = board_id
        self.settings_url = settings_url
        super().__init__(self._message())

    def _message(self):
        return (
            f"Agent '{self.agent}' is not enabled on board {self.board_id}. "
            f"Enable it at {self.settings_url} or pick a different agent."
        )


def _set_dispatch_blocked_reason(task, reason, *, blocked_by=None):
    """Persist `dispatch_blocked_reason` on a task and log it (loud skip).

    Idempotent: skips the DB round-trip if the reason is already correct
    AND no holder list needs refreshing. Used to surface the dispatch
    gate's decision on the task's metadata payload that the UI / spec
    tracing tools consume.

    ``blocked_by`` (optional): a list of share-holder entries (see
    ``sandbox_budget.memory_share_holders``) naming the tasks currently
    holding the memory shares the gate is waiting on. Stored under
    ``metadata.dispatch_blocked_blocked_by`` so the dispatch-blocked banner
    can render "held by task 344, reflection on 346" instead of the bare
    code. On a same-reason re-stamp the holder list is refreshed — shares
    can have moved (one VM released, a reflection finished, a new task
    became the blocker) and the banner must reflect who is actually
    holding the shares right now, not the snapshot from the last poll.
    """
    metadata = dict(task.metadata or {})
    same_reason = metadata.get("dispatch_blocked_reason") == reason
    if same_reason:
        if blocked_by:
            existing = metadata.get("dispatch_blocked_blocked_by") or []
            if existing != list(blocked_by):
                # Holder set drifted since the last stamp — refresh the
                # persisted list so the banner stops advertising stale
                # names. Touch dispatch_blocked_at too so operators can
                # see when the banner last updated.
                metadata["dispatch_blocked_blocked_by"] = list(blocked_by)
                metadata["dispatch_blocked_at"] = timezone.now().isoformat()
                Task.objects.filter(id=task.id).update(metadata=metadata)
                task.metadata = metadata
        return
    metadata["dispatch_blocked_reason"] = reason
    metadata["dispatch_blocked_at"] = timezone.now().isoformat()
    if blocked_by:
        metadata["dispatch_blocked_blocked_by"] = list(blocked_by)
    elif "dispatch_blocked_blocked_by" in metadata:
        # Drop a stale holder list when the stamp is being renewed for a
        # different reason — keeping it would advertise holders that are
        # no longer what blocked this task.
        metadata.pop("dispatch_blocked_blocked_by", None)
    Task.objects.filter(id=task.id).update(metadata=metadata)
    task.metadata = metadata


def _clear_dispatch_blocked_reason(task, *, persist=False):
    """Drop the dispatch_blocked_reason stamp from a task's metadata.

    Called when (a) the task reaches the dispatch gate cleanly, or (b) any
    other code wants to reset the visible signal. When `persist` is False
    the change is only applied to the in-memory instance, so the caller
    controls the save.

    Always drops the companion ``dispatch_blocked_blocked_by`` stamp too —
    the operator-visible "held by task X, reflection on Y" list is part of
    the same banner and must not survive a requeue.
    """
    metadata = dict(task.metadata or {})
    has_any = any(
        k in metadata for k in (
            "dispatch_blocked_reason", "dispatch_blocked_at",
            "dispatch_blocked_blocked_by",
        )
    )
    if not has_any:
        return
    metadata.pop("dispatch_blocked_reason", None)
    metadata.pop("dispatch_blocked_at", None)
    metadata.pop("dispatch_blocked_blocked_by", None)
    if persist:
        Task.objects.filter(id=task.id).update(metadata=metadata)
    task.metadata = metadata


def _auto_assign_from_suggested(task):
    """Default First (F43): if the task has no assignee but metadata hints one,
    resolve and assign instead of skipping the poll.

    Returns (assignee_id, was_auto_assigned). When the metadata hint is
    absent or refers to an unknown / retired agent, returns (task.assignee_id,
    False) — the caller's existing skip path then runs.

    Resolution order:
      1. suggested_agent must be in the active lineup (agent_models.json —
         single source of truth since F45). Retired agents are silently
         absent so legacy plans don't dispatch to dead providers.
      2. The User row is found by email: ``{name}@odin.agent`` or
         ``{name}+{model}@odin.agent`` (Identity emails carry model suffix.)
      3. The task's model_name is filled from the agent's default model
         (F45) so the auto-assigned task is dispatch-ready.
    """
    if task.assignee_id:
        return task.assignee_id, False

    suggested = (task.metadata or {}).get("suggested_agent")
    if not suggested:
        return None, False

    suggested_lower = str(suggested).strip().lower()
    if not suggested_lower:
        return None, False

    # Lazy imports: pricing.py loads agent_models.json at import time; keep it
    # local so test environments that monkeypatch the registry aren't broken.
    from .pricing import get_active_agents, get_agent_default_model

    if suggested_lower not in get_active_agents():
        logger.info(
            "[task:%s] suggested_agent=%r is not in active lineup — skipping auto-assign",
            task.id, suggested,
        )
        return None, False

    # is_active=True excludes retired agents whose User rows persist for FK
    # integrity but must not be auto-assigned (task #135). The active-lineup
    # check above already screens by name; this is the DB-side belt-and-braces.
    new_user = (
        User.objects.filter(
            email__iexact=f"{suggested_lower}@odin.agent",
            is_active=True,
        ).first()
        or User.objects.filter(
            email__istartswith=f"{suggested_lower}+",
            email__iendswith="@odin.agent",
            is_active=True,
        ).order_by("id").first()
    )
    if new_user is None:
        logger.warning(
            "[task:%s] suggested_agent=%r is active in agent_models.json but no "
            "active User row found (seedmodels may not have run) — skipping auto-assign",
            task.id, suggested,
        )
        return None, False

    # W10.4: refuse to auto-assign a task to an agent that is not
    # currently enabled on this board. Without this guard a planned task
    # with ``metadata.suggested_agent`` lands on a roster-disabled agent
    # and the operator learns the cause only via failed dispatch.
    # Better to fail loudly here with the agent name + the settings URL
    # so the next thing the operator does is flip the switch — exactly
    # the symptom board 6 surfaced.
    from .views import _agent_settings_hint
    if (
        task.board_id
        and not BoardMembership.objects.filter(
            board_id=task.board_id, user=new_user,
        ).exists()
    ):
        hint = _agent_settings_hint(task.board_id, new_user.name)
        logger.warning(
            "[task:%s] suggested_agent=%r is active but NOT enabled on "
            "board %s — auto-assign refused. %s",
            task.id, suggested, task.board_id, hint,
        )
        raise AgentNotEnabledOnBoard(
            agent=new_user.name,
            board_id=task.board_id,
            settings_url=hint,
        )

    metadata = dict(task.metadata or {})
    metadata["auto_assigned_from_suggested"] = True

    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="assignee",
        old_value="",
        new_value=str(new_user.id),
        changed_by="odin+dag-executor@system",
    )

    # Default First (F45): if the task has no model, fill it from the agent's
    # active-lineup default. Without this, the auto-assigned task would dispatch
    # with no model and die on ProviderModelNotFoundError.
    if not task.model_name:
        default_model = get_agent_default_model(suggested_lower)
        if default_model:
            metadata["selected_model"] = default_model
            TaskHistory.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                field_name="model",
                old_value="",
                new_value=default_model,
                changed_by="odin+dag-executor@system",
            )

    Task.objects.filter(id=task.id).update(
        assignee_id=new_user.id,
        model_name=(metadata.get("selected_model") or task.model_name),
        metadata=metadata,
    )

    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="system",
        content=(
            f"Auto-assigned from metadata.suggested_agent: "
            f"{suggested_lower} → user {new_user.name}. "
            f"Model: {metadata.get('selected_model') or 'unchanged'}."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    # Mirror the manual assign path — keep board membership consistent so the
    # notification and filtering machinery sees the new agent.
    try:
        BoardMembership.objects.get_or_create(board=task.board, user=new_user)
    except Exception:
        logger.exception(
            "[task:%s] Failed to ensure board membership for auto-assigned %s",
            task.id, new_user.name,
        )

    logger.info(
        "[task:%s] Auto-assigned from suggested_agent=%r → %s",
        task.id, suggested, new_user.name,
    )
    return new_user.id, True


@shared_task(name="tasks.dag_executor.poll_and_execute")
def poll_and_execute():
    """Poll for IN_PROGRESS tasks ready for execution.

    Scheduled by Celery Beat every N seconds. Checks dependency satisfaction,
    respects concurrency limits, and fires execute_single_task for ready tasks.

    F43/F44 dispatch guardrails:
    - Default First auto-assign: if a task reaches the gate with no assignee
      but ``metadata.suggested_agent`` is set, auto-assign from the active
      lineup instead of skipping.
    - Loud skip reasons: every task the poll cannot dispatch gets a
      ``metadata.dispatch_blocked_reason`` stamp the UI / spec tracing tools
      can render. Reasons: ``no_assignee``, ``deps_not_complete``,
      ``deps_blocked_failed``, ``no_worktree_no_optin``.
    - Project-root execution is explicit: tasks without a worktree only
      dispatch when the board opted in via
      ``Board.allow_project_root_execution = True``. Otherwise the task
      transitions to FAILED with a clear reason — never silently runs the
      agent in the project root (task #114 isolation breach).

    Only IN_PROGRESS tasks are considered. TODO tasks are never touched —
    moving a task to IN_PROGRESS is an explicit action by the user or odin.
    """
    try:
        setting = SystemSetting.objects.get(key="executor_max_concurrency")
        max_concurrency = setting.value
    except SystemSetting.DoesNotExist:
        max_concurrency = getattr(settings, "DAG_EXECUTOR_MAX_CONCURRENCY", 3)

    reconcile_task_runs()

    executing_count = Task.objects.filter(status=TaskStatus.EXECUTING).count()
    available_slots = max_concurrency - executing_count
    if available_slots <= 0:
        logger.debug("No available slots (executing=%d, max=%d)", executing_count, max_concurrency)
        return

    # Global memory budget for microVM spawns. Execution and reflection share
    # one budget so the host never over-commits RAM into swap. ``None`` means
    # no budget is configured → gate skipped (legacy behavior, opt-in via env).
    budget_mib = get_budget_mib()
    vm_mem = default_vm_mem_mib()
    if budget_mib is not None:
        budget_remaining = max(0, budget_mib - compute_reserved_mib())
        logger.debug(
            "poll_and_execute: memory budget=%dMiB remaining=%dMiB (vm=%dMiB)",
            budget_mib, budget_remaining, vm_mem,
        )
    else:
        budget_remaining = None

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

        # Default First auto-assign (F43). May mutate the task's assignee
        # + model in place; if it does, re-fetch so the assignee check below
        # observes the new value.
        try:
            assignee_id, was_auto_assigned = _auto_assign_from_suggested(task)
        except Exception:
            logger.exception(
                "[task:%s] Auto-assign from suggested_agent crashed; treating as no_assignee",
                task.id,
            )
            assignee_id, was_auto_assigned = None, False

        if not assignee_id:
            _set_dispatch_blocked_reason(task, "no_assignee")
            logger.info(
                "[task:%s] Poll skip: no assignee and no suggested_agent",
                task.id,
            )
            continue

        if was_auto_assigned:
            task.refresh_from_db(fields=["assignee_id", "model_name", "metadata"])

        dep_status = check_deps(task)
        if dep_status != DepStatus.READY:
            reason = DEP_BLOCKED_REASONS.get(dep_status, f"deps_{dep_status.name.lower()}")
            _set_dispatch_blocked_reason(task, reason)
            logger.info(
                "[task:%s] Poll skip: deps=%s → %s",
                task.id, dep_status.name, reason,
            )
            continue

        _clear_dispatch_blocked_reason(task, persist=True)

        # Memory budget gate: a spawn that would exceed the global budget
        # waits in line (stamped + skipped) instead of booting into swap.
        # ``continue`` (not ``break``) so a later, differently-sized spawn
        # could still fit — uniform VM size today, but the gate stays correct
        # if per-task sizes are introduced. The stamp also carries the
        # current holder list so the dispatch-blocked banner can name each
        # share-holder (task #353) instead of the bare "memory budget full".
        if budget_remaining is not None and vm_mem > budget_remaining:
            from .sandbox_budget import memory_share_holders
            _set_dispatch_blocked_reason(
                task,
                "memory_budget_full",
                blocked_by=memory_share_holders(),
            )
            logger.info(
                "[task:%s] Poll skip: memory budget full "
                "(need %dMiB, %dMiB remaining of %dMiB)",
                task.id, vm_mem, budget_remaining, budget_mib,
            )
            continue

        ready_tasks.append(task)
        if budget_remaining is not None:
            budget_remaining -= vm_mem

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
                # Reserved memory for the global sandbox budget. Accounted by
                # compute_reserved_mib() while the task stays EXECUTING.
                "mem_mib": default_vm_mem_mib(),
            }
            _clear_dispatch_blocked_reason(locked_task)

            # Attempt worktree creation if a spec branch is available. We
            # are explicit about whether the project root will be used —
            # see the gate below.
            worktree_path = None
            try:
                if locked_task.spec_id and (locked_task.spec.metadata or {}).get("branch"):
                    worktree_path = _create_task_worktree_with_retry(locked_task)
                    if worktree_path:
                        metadata["working_dir"] = str(worktree_path)
                        metadata["worktree_path"] = str(worktree_path)
                        metadata["branch"] = (
                            f"task/{locked_task.spec.odin_id}/{locked_task.id}"
                        )
                        metadata["merge_status"] = "pending"
            except Exception:
                worktree_path = None
                logger.exception(
                    "[task:%s] Worktree creation raised; falling through to gate",
                    locked_task.id,
                )

            board = locked_task.board
            allow_project_root = bool(
                getattr(board, "allow_project_root_execution", False)
            )

            if not worktree_path and not allow_project_root:
                # F43 guardrail: never silently dispatch into the board's
                # project root. Mark the task FAILED with a visible reason
                # so the operator sees why the dispatch halted.
                locked_task.status = TaskStatus.FAILED
                metadata["dispatch_blocked_reason"] = "no_worktree_no_optin"
                metadata["dispatch_blocked_at"] = timezone.now().isoformat()
                metadata["last_failure_type"] = "missing_worktree"
                metadata["last_failure_reason"] = (
                    "No task worktree could be created (no spec branch or "
                    "worktree creation failed) and the board does not allow "
                    "project-root execution. Set board.allow_project_root_execution=true "
                    "to opt in, or attach the task to a spec with a branch."
                )
                metadata["last_failure_origin"] = "taskit_dag_executor"
                tag_failure_class(metadata)
                locked_task.metadata = metadata
                locked_task.kanban_position = move_task(
                    locked_task, target_status=TaskStatus.FAILED, target_index=None,
                )
                locked_task.save(update_fields=[
                    "status", "kanban_position", "metadata", "last_updated_at",
                ])
                TaskHistory.objects.create(
                    task=locked_task,
                    schedule_run=locked_task.current_schedule_run,
                    field_name="status",
                    old_value=TaskStatus.IN_PROGRESS,
                    new_value=TaskStatus.FAILED,
                    changed_by="odin+dag-executor@system",
                )
                TaskComment.objects.create(
                    task=locked_task,
                    schedule_run=locked_task.current_schedule_run,
                    author_email="odin+dag-executor@system",
                    author_label="odin-dag-executor",
                    content=(
                        "Failed: dispatch halted — no worktree path resolvable "
                        "and the board has not opted in to project-root execution.\n"
                        "Failure type: missing_worktree\n"
                        "Reason: " + metadata["last_failure_reason"] + "\n"
                        "Origin: taskit_dag_executor"
                    ),
                    comment_type=CommentType.STATUS_UPDATE,
                )
                maybe_finalize_schedule_run(locked_task, TaskStatus.FAILED)
                logger.warning(
                    "[task:%s] Dispatch halted: no worktree, no project-root opt-in "
                    "(board.allow_project_root_execution=False). Marked FAILED.",
                    locked_task.id,
                )
                continue

            if not worktree_path and allow_project_root:
                # Opt-in fallback: surface the warning on the metadata so the
                # UI / diagnostics show that the dispatch is running against
                # the project root (no isolation).
                metadata["project_root_execution_used"] = True
                metadata["project_root_execution_reason"] = (
                    "Task has no spec branch and the board opted in to "
                    "project-root execution. No worktree isolation."
                )
                logger.warning(
                    "[task:%s] Dispatching in project root (board opt-in).",
                    locked_task.id,
                )

            locked_task.status = TaskStatus.EXECUTING
            locked_task.metadata = metadata
            locked_task.save(update_fields=["status", "metadata", "last_updated_at"])
            # F354: rotate any leftover per-task trace files aside BEFORE the
            # new run starts so the progress scanner can't be poisoned by the
            # previous attempt's mtime. Bookkeeping Never Kills the Run — a
            # rotation failure logs a warning and lets the run proceed (the
            # age-vs-run-start guard in _run_progress_mtime catches the
            # poisoned file defensively).
            try:
                from .session_resolver import _rotate_leftover_trace_files_for_task
                rotated = _rotate_leftover_trace_files_for_task(locked_task)
                if rotated:
                    logger.info(
                        "[task:%s] Rotated %d leftover trace file(s) before run: %s",
                        locked_task.id, len(rotated), [p.name for p in rotated],
                    )
            except Exception:
                logger.exception(
                    "[task:%s] Leftover-trace rotation failed; continuing dispatch",
                    locked_task.id,
                )
            task_runs.start_run(locked_task, run_token)

            TaskHistory.objects.create(
                task=locked_task,
                schedule_run=locked_task.current_schedule_run,
                field_name="status",
                old_value=TaskStatus.IN_PROGRESS,
                new_value=TaskStatus.EXECUTING,
                changed_by="odin+dag-executor@system",
            )

        logger.info("Task %s: IN_PROGRESS → EXECUTING, firing execution", task.id)

        # Memory: surface the closest finished twins as starting context.
        # Best-effort — a scoring failure must never block dispatch.
        try:
            post_twins_comment(locked_task)
        except Exception:
            logger.exception("[task:%s] post_twins_comment failed at dispatch", locked_task.id)

        async_result = execute_single_task.delay(task.id, run_token)
        latest = Task.objects.get(id=task.id)
        metadata = dict(latest.metadata or {})
        active = dict(metadata.get("active_execution") or {})
        if active.get("run_token") == run_token:
            celery_task_id = getattr(async_result, "id", None)
            if celery_task_id is not None:
                active["celery_task_id"] = str(celery_task_id)
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

    # Supersession fence (the requeue double-post): odin may have recorded
    # this run's result AND the failure policy requeued under a brand-new run
    # while this subprocess was finishing. The task is EXECUTING again, but
    # under a different run_token — this wrapper must not re-post run A's
    # failure burst (the requeue already owns the task). Only post for the
    # run this wrapper was dispatched for.
    current_run = task_runs.current_running_run(task)
    if (
        run_token
        and current_run is not None
        and current_run.run_token != run_token
    ):
        _append_summary(log_file, task, exit_code)
        logger.info(
            "Task %s run superseded (this=%s current=%s) — wrapper skips post",
            task_id, run_token, current_run.run_token,
        )
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
    task_runs.finish_run(
        run_token, state=TaskRunState.KILLED if guard_honored else TaskRunState.FINISHED,
    )

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
        tag_failure_class(metadata)
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

    # W4.4 policy layer: at the FAILED transition the per-class policy
    # dispatcher decides the automatic action.  AUTO_REQUEUE delegates
    # to _maybe_auto_redispatch_infra_failure below (so the legacy
    # counter / history / continuity / execution-strategy trigger stay
    # in one place — no parallel code path); REASSIGN marks the audit
    # trail (the actual reassign runs at reflection time in
    # views._maybe_reassign_on_quota_failure); HUMAN posts an audit
    # comment and leaves the task FAILED.
    #
    # The legacy infra auto-redispatch hook below is kept for the case
    # where failure_class is missing (a pre-W4.4 metadata write or a
    # tagger miss): it falls back to the legacy type-based classifier
    # so a regression in the tagger doesn't regress auto-retry.
    if not guard_honored and new_status == TaskStatus.FAILED:
        task.refresh_from_db()
        from .failure_policy import apply_failure_policy
        if not apply_failure_policy(task):
            # No policy matched (human action, or failure_class missing).
            # Fall back to the legacy type-based infra classifier for
            # backward compatibility — preserves the auto-retry behavior
            # for stale_execution + agent_execution_failure:no_odin_status
            # even if the policy table hasn't been extended to cover them.
            _maybe_auto_redispatch_infra_failure(task)

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

def _pid_is_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_pid_cmdline_tokens(pid):
    """Return the argv tokens of ``pid``, or None when introspection fails.

    Reads ``/proc/<pid>/cmdline`` on Linux (fast, no subprocess). Falls back to
    ``ps`` elsewhere. Returns None only when neither works — callers treat that
    as "could not verify" rather than "mismatch", so a live PID on a host
    without proc introspection degrades to the pre-fix adopt-if-alive behavior.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        if raw:
            return [t.decode("utf-8", "replace") for t in raw.split(b"\x00") if t]
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        if out.strip():
            return out.split()
    except Exception:  # noqa: BLE001 — ps is best-effort fallback
        pass
    return None


def _pid_cmdline_matches(task_id, pid) -> bool:
    """True if ``pid`` is running ``odin exec <task_id>``.

    After a worker restart a recorded PID may have been recycled by an
    unrelated process. ``os.kill(pid, 0)`` liveness alone can't tell a real
    surviving ``odin exec`` orphan from a recycled PID, so recovery would
    either adopt a foreign process (task hangs) or time out a legitimate
    orphan and re-dispatch (duplicate ``odin exec``). Verifying the cmdline
    distinguishes the two: adopt only when the PID genuinely belongs to an
    ``odin exec`` for THIS task.
    """
    tokens = _read_pid_cmdline_tokens(pid)
    if tokens is None:
        # Cannot introspect — preserve adopt-if-alive behavior rather than
        # risk a spurious re-fire on hosts without proc access.
        return True
    tid = str(task_id)
    has_odin = any(t == "odin" or os.path.basename(t) == "odin" for t in tokens)
    return has_odin and ("exec" in tokens) and (tid in tokens)


def _fail_stale_execution(task, *, reason, failure_type="stale_execution",
                           run_token=None, run_state=TaskRunState.EXPIRED,
                           failure_class_override=None):
    """Mark an abandoned EXECUTING task FAILED and drive it through the
    existing auto-requeue policy (W4.4/W4.10).

    The single shared tail for every "the runner is gone" detector — TaskRun
    lease expiry, dead pid, queued-past-deadline, task timeout — so there is
    one requeue mechanism, not one per detector (task #211: "fewer loops,
    one owner").

    ``failure_class_override`` (task #262) lets a detector that already
    classified the failure (e.g. the error-loop detector knows the
    dominant error class) stamp ``failure_class`` directly instead of
    re-deriving it from ``failure_type`` / ``reason`` via the tagger.
    """
    old_status = task.status
    metadata = dict(task.metadata or {})
    metadata.pop("active_execution", None)
    task_runs.finish_run(run_token, state=run_state)
    metadata["last_failure_type"] = failure_type
    metadata["last_failure_reason"] = reason
    metadata["last_failure_origin"] = "taskit_dag_executor"
    if failure_class_override:
        metadata["failure_class"] = failure_class_override
    else:
        tag_failure_class(metadata)
    task.kanban_position = move_task(task, target_status=TaskStatus.FAILED, target_index=None)
    task.status = TaskStatus.FAILED
    task.metadata = metadata
    task.save(update_fields=["status", "kanban_position", "metadata", "last_updated_at"])

    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="status",
        old_value=old_status,
        new_value=TaskStatus.FAILED,
        changed_by="odin+dag-executor@system",
    )
    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content=(
            "Failed: " + reason + "\nFailure type: " + failure_type
            + "\nOrigin: taskit_dag_executor"
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )
    maybe_finalize_schedule_run(task, TaskStatus.FAILED)
    # Mistakes ledger (task #223): distill this stale-FAILED transition so
    # similar future tasks carry the warning. Idempotent per run_token.
    record_execution_mistake(task, run_token=run_token or "")
    # Error ledger (task #222): when the tagger couldn't classify the
    # failure, surface it for triage. Well-classified failures already
    # have a MistakeEntry driving twins warnings — ErrorEvent is the
    # catch-all the operator triages when something new appears.
    try:
        failure_class = (task.metadata or {}).get("failure_class") or ""
        if failure_class == "unknown":
            from .errors import record_failure_tagger_unknown
            record_failure_tagger_unknown(
                task=task,
                symptom=(task.metadata or {}).get("last_failure_reason") or reason,
                failure_class=failure_class,
                failure_type=(task.metadata or {}).get("last_failure_type") or failure_type,
                failure_reason=(task.metadata or {}).get("last_failure_reason") or reason,
                source_id=f"task-{task.id}:{failure_type}",
            )
    except Exception:
        logger.exception("error ledger: failed to record failure_tagger_unknown for task %s", task.id)
    logger.warning("Reaped stale EXECUTING task %s as FAILED: %s", task.id, reason)
    # W4.4 policy layer: stale_execution is auto-requeue per the
    # policy table (and timeout is human).  apply_failure_policy
    # dispatches on failure_class; the legacy hook below is the
    # fallback for any case where the policy table misses.
    task.refresh_from_db()
    from .failure_policy import apply_failure_policy
    if not apply_failure_policy(task):
        # Pre-W4.4 fallback: legacy type-based infra classifier.
        _maybe_auto_redispatch_infra_failure(task)


def _sweep_orphaned_sandboxes():
    """Best-effort removal of leaked microsandbox VMs (task #211).

    Killing a run's pid group tears down the in-process ``msb run`` that
    hosts the VM in the common case, but msb also tracks each sandbox's
    on-disk state independent of the process that spawned it — exactly how
    3 VMs outlived a worker restart with no supervisor (see the module WHY).
    Reuses the harness's own removal path rather than inventing a second
    kill mechanism: it is prefix-filtered (only ``odin-msb-*``) and refuses
    to touch ``odinbuild``/the ``odin-agents`` snapshot by construction.
    Never raises — a sweep failure must not block run reconciliation.
    """
    try:
        from odin.harnesses.microsandbox import MicrosandboxHarness
    except ImportError:
        return []
    try:
        return MicrosandboxHarness.sweep_startup_orphans()
    except Exception:
        logger.exception("Sandbox sweep failed during task-run reconciliation")
        return []


def _reap_task_run(run, lease_seconds=None, *, reason=None,
                   failure_type="stale_execution", failure_class_override=None):
    """Reap a single TaskRun: verify + kill the supervising pid, sweep any
    leaked sandbox, close the run, and fail+requeue its task.

    ``reason`` overrides the default heartbeat-staleness message so the
    progress-liveness path (a live-but-stuck agent: fresh heartbeat, idle
    trace) can explain the actual cause without duplicating the kill/sweep
    tail.

    ``failure_type`` / ``failure_class_override`` (task #262) let the
    error-loop detector drive the failure classification without
    duplicating the kill mechanics. When ``failure_class_override`` is
    set it is stamped directly; otherwise the tagger classifies from the
    type/reason pair.
    """
    task = run.task
    pid = run.pid
    pid_verified_alive = bool(pid) and _pid_is_alive(pid) and _pid_cmdline_matches(task.id, pid)

    killed = False
    if pid_verified_alive:
        killed = _terminate_pid(int(pid), force=True)

    _sweep_orphaned_sandboxes()

    run_state = TaskRunState.KILLED if killed else TaskRunState.EXPIRED

    if task.status != TaskStatus.EXECUTING:
        # Something else already moved the task on — just close the run row.
        task_runs.finish_run(run.run_token, state=run_state)
        logger.info(
            "TaskRun %s lease expired but task %s is already %s — run closed, no requeue",
            run.run_token[:8], task.id, task.status,
        )
        return

    if reason is None:
        reason = (
            f"No heartbeat for over {lease_seconds}s (last_heartbeat="
            f"{run.last_heartbeat.isoformat()}, pid={pid or 'unknown'}) — the process "
            "supervising this run is gone (worker crash or restart). Sandbox/process "
            + ("killed" if killed else "already gone")
            + f" and the run marked {run_state}."
        )
    _fail_stale_execution(
        task, reason=reason, failure_type=failure_type,
        run_token=run.run_token, run_state=run_state,
        failure_class_override=failure_class_override,
    )


def _reap_expired_task_run_leases():
    """Primary crash detector (task #211): a RUNNING TaskRun whose heartbeat
    has gone stale past the lease window has no live supervisor — the
    process that should be heartbeating it (the subprocess-monitoring loop
    in ``_run_subprocess_with_cancellation`` / ``LocalOdinStrategy``) is
    gone, almost always because the celery worker hosting it crashed or
    restarted mid-run.

    Unlike the legacy timeout checks below (keyed to Task.metadata and a
    worker's own wall-clock deadline), this is heartbeat-driven and therefore
    catches a dead supervisor within one lease window regardless of which
    execution strategy spawned the run or how long the task's own execution
    budget is.
    """
    lease_seconds = int(getattr(settings, "TASK_RUN_LEASE_SECONDS", 180) or 180)
    cutoff = timezone.now() - timedelta(seconds=lease_seconds)
    expired = TaskRun.objects.select_related("task").filter(
        state=TaskRunState.RUNNING, last_heartbeat__lt=cutoff,
    )
    for run in expired:
        _reap_task_run(run, lease_seconds)


def _run_progress_mtime(run):
    """Return the mtime (epoch seconds) of the run's harness trace file, or
    None when no trace file exists.

    The trace JSONL is appended to as the agent emits output, so its mtime is
    the truest "is this run making progress?" signal. A fresh heartbeat only
    proves the *supervising* process is alive, not that the agent inside the
    sandbox is doing anything — the gap that left zombie sandboxes (host
    slept, process up, zero output for hours) undetected by the
    heartbeat-only lease check. Reuses ``session_resolver``'s path resolution
    (metadata["trace_file"] -> board-root logs -> worktree logs) so the
    definition of "the trace" stays in one place.

    Age-vs-run-start guard (F354): the per-task trace file is shared across
    retries (the path is keyed to ``task_<id>``, not the attempt). A fresh
    retry that hasn't written yet is judged by the previous attempt's mtime
    and reaped at birth. Ignore any mtime older than ``run.started_at`` —
    that file belongs to a previous attempt, and the run falls back to the
    lease/heartbeat check (which is the only check that can correctly judge
    a freshly-started run with no output yet). Returns None so the caller
    treats the run as "no trace yet" instead of "stale trace".
    """
    task = run.task
    try:
        from .session_resolver import _log_dir_for_task, _stat, _task_trace_path
    except Exception:
        logger.debug("progress-liveness: session_resolver unavailable", exc_info=True)
        return None
    log_dir = _log_dir_for_task(task)
    if log_dir is None:
        return None
    path = _task_trace_path(task, log_dir, task.id)
    exists, _size, mtime = _stat(path)
    if not exists or mtime is None:
        return None
    # Age-vs-run-start guard: mtime older than the run's started_at (minus a
    # small grace for the dispatch→first-write gap, see below) is a leftover
    # from a previous attempt (dispatch should have rotated it aside, but if
    # it didn't, or a legacy path wrote here, fall through to the
    # lease/heartbeat check rather than poisoning this run with the
    # predecessor's idle time).
    #
    # The 2-second grace absorbs the real-world race where odin emits its
    # first trace line a fraction of a second before the supervisor stamps
    # the run row (and the test-setup race where the trace fixture is
    # written microseconds before task_runs.start_run). Anything past the
    # grace is unambiguously a previous attempt — the F354 poisoned-retry
    # signature. 600s is the progress window; 2s vs 600s leaves the real
    # zombie detection (10-minute idle) completely untouched.
    started_at_epoch = run.started_at.timestamp() if run.started_at else 0
    if started_at_epoch and mtime < started_at_epoch - 2.0:
        return None
    return mtime


def _reap_stalled_progress_runs():
    """Zombie detector (task #235): a RUNNING run whose trace file has gone
    stale past the progress window is making no forward progress — the agent
    inside the sandbox is hung even though the supervising process is still
    alive and heartbeating. That is exactly the "host slept, process up,
    zero output for hours" case the heartbeat-only lease check
    (``_reap_expired_task_run_leases``) cannot see, because a stuck process
    keeps heartbeating.

    Only fires when a trace file exists. A run with no trace (not yet
    written, or a legacy dispatch path) falls back to the heartbeat/pid
    liveness in ``_reap_expired_task_run_leases`` and is never reaped here.

    Runs after the lease check within ``reconcile_task_runs`` so a run with
    both a stale heartbeat and a stale trace is handled once (by the lease
    check) rather than twice.
    """
    progress_window = int(
        getattr(settings, "TASK_RUN_PROGRESS_WINDOW_SECONDS", 600) or 600
    )
    cutoff_epoch = time.time() - progress_window
    running = (
        TaskRun.objects
        .select_related("task", "task__spec", "task__board")
        .filter(state=TaskRunState.RUNNING)
    )
    for run in running:
        mtime = _run_progress_mtime(run)
        if mtime is None:
            continue
        if mtime >= cutoff_epoch:
            continue
        age = int(time.time() - mtime)
        reason = (
            f"No agent progress for ~{age}s (trace file idle; heartbeat fresh, "
            f"pid={run.pid or 'unknown'} alive) — the sandbox process is up but "
            "the agent inside has stopped producing output (host sleep / hung "
            "agent). Run reaped and requeued via the stale-execution path."
        )
        _reap_task_run(run, reason=reason)


def _resolve_run_trace_path(run):
    """Return the trace-file path for *run* (or None), reusing
    ``session_resolver`` so "the trace" is defined in one place."""
    task = run.task
    try:
        from .session_resolver import _log_dir_for_task, _task_trace_path
    except Exception:
        logger.debug("error-loop: session_resolver unavailable", exc_info=True)
        return None
    log_dir = _log_dir_for_task(task)
    if log_dir is None:
        return None
    return _task_trace_path(task, log_dir, task.id)


def _reap_error_loop(run, verdict, *, provider_delay, reassign_enabled):
    """Kill + fail + requeue one looping run.

    Stamps a provider-specific ``next_retry_after`` (so the requeue
    honours the backoff window) BEFORE delegating to the shared fail
    tail — the error_loop policy carries ``backoff_seconds=0``, so
    ``_auto_requeue``'s ``if backoff > 0`` guard leaves this stamp in
    place. Records an ErrorEvent per detection and, when routing
    awareness is enabled, reassigns to a fallback agent for repeated
    provider outages.
    """
    from .error_loop import is_provider_loop
    from .errors import record_error_loop

    task = run.task
    delay = provider_delay if is_provider_loop(verdict) else 0

    # Provider backoff: stamp next_retry_after on the task metadata so
    # the dispatcher's requeue honours the wait-out-the-window delay.
    if delay > 0:
        md = dict(task.metadata or {})
        md["next_retry_after"] = (
            timezone.now() + timedelta(seconds=delay)
        ).isoformat()
        md["error_loop_backoff_seconds"] = delay
        task.metadata = md
        task.save(update_fields=["metadata"])

    # Error ledger — one row per detection (deduped by task+signature).
    try:
        record_error_loop(
            task=task,
            symptom=(
                f"Error loop detected: {verdict.error_lines}/{verdict.total_lines} "
                f"trace lines share signature '{verdict.dominant_signature}' "
                f"({verdict.dominant_class or 'unclassified'})."
            ),
            failure_class=verdict.dominant_class or "error_loop",
            dominant_signature=verdict.dominant_signature,
            error_ratio=verdict.error_ratio,
            error_lines=verdict.error_lines,
            total_lines=verdict.total_lines,
            trace_path=str(_resolve_run_trace_path(run) or ""),
            sample=verdict.sample,
        )
    except Exception:
        logger.exception("error ledger: failed to record error_loop for task %s", task.id)

    # Routing awareness (scope 3, flag-gated): when the provider is down
    # repeatedly, reassign to the next-cheapest viable agent instead of
    # requeuing the same one. OFF by default.
    reassigned_to = None
    if reassign_enabled and is_provider_loop(verdict):
        reassigned_to = _maybe_reassign_loop_agent(task, verdict)

    reason = _error_loop_reason(verdict, delay, reassigned_to)
    _reap_task_run(
        run, reason=reason, failure_type="error_loop",
        failure_class_override="error_loop",
    )


def _maybe_reassign_loop_agent(task, verdict):
    """Reassign a looping task to a fallback agent when routing is enabled.

    Returns the new agent name (for the comment) or None when no
    alternative was available. Reuses the quota-failover agent finder so
    cost-tier preference and active-lineup filtering stay in one place.
    """
    try:
        from .views import _find_alternative_agent
    except Exception:
        logger.debug("error-loop: _find_alternative_agent unavailable", exc_info=True)
        return None
    agent, model_name = _find_alternative_agent(task)
    if agent is None:
        return None
    original = task.assignee.name if task.assignee_id else "(none)"
    task.assignee = agent
    task.model_name = model_name or task.model_name
    task.save(update_fields=["assignee", "model_name", "last_updated_at"])
    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="assignee",
        old_value=original,
        new_value=agent.name,
        changed_by="odin+dag-executor@system",
    )
    return agent.name


def _error_loop_reason(verdict, delay, reassigned_to):
    """Build the operator-visible reason line for an error-loop reap."""
    parts = [
        f"Error loop detected: {verdict.error_lines}/{verdict.total_lines} "
        f"({verdict.error_ratio:.0%}) of the trace tail share the same "
        f"error signature.",
        f"Signature: {verdict.dominant_signature}",
        f"Class: {verdict.dominant_class or 'unclassified'}",
    ]
    if delay > 0:
        parts.append(
            f"Provider loop — requeue waits {delay}s (waiting out the window) "
            f"before retrying."
        )
    if reassigned_to:
        parts.append(f"Routing awareness ON — reassigned to '{reassigned_to}'.")
    sample = (verdict.sample or "").strip()
    if sample:
        parts.append(f"Sample error line: {sample[:200]}")
    return "\n".join(parts)


def _reap_error_loop_runs():
    """Error-loop detector (task #262): a RUNNING run whose trace tail is
    dominated by the same error signature is looping — the CLI is
    retrying a failing provider call forever, the trace keeps growing
    (so the W6.15 progress check sees fresh writes and passes), but no
    WORK is happening. This is the gap that left task 252 EXECUTING for
    an hour in a minimax stream-error loop with quota exhausted.

    Only examines runs with a FRESH trace (stale-trace runs were already
    reaped by ``_reap_stalled_progress_runs`` in the same pass), so the
    extra cost of reading the tail is paid only for runs the other two
    checks could not catch. A run with no trace is left to the lease /
    pid checks.
    """
    threshold = float(getattr(settings, "TASK_RUN_ERROR_LOOP_THRESHOLD", 0.8) or 0.8)
    window = int(getattr(settings, "TASK_RUN_ERROR_LOOP_WINDOW", 50) or 50)
    min_lines = int(getattr(settings, "TASK_RUN_ERROR_LOOP_MIN_LINES", 10) or 10)
    provider_delay = int(
        getattr(settings, "TASK_RUN_ERROR_LOOP_PROVIDER_BACKOFF", 600) or 600
    )
    reassign_enabled = bool(getattr(settings, "TASK_RUN_ERROR_LOOP_REASSIGN", False))

    progress_window = int(
        getattr(settings, "TASK_RUN_PROGRESS_WINDOW_SECONDS", 600) or 600
    )
    cutoff_epoch = time.time() - progress_window

    from .error_loop import detect_error_loop

    running = (
        TaskRun.objects
        .select_related("task", "task__spec", "task__board", "task__assignee")
        .filter(state=TaskRunState.RUNNING)
    )
    for run in running:
        mtime = _run_progress_mtime(run)
        if mtime is None:
            continue          # no trace — lease/pid checks own this run
        if mtime < cutoff_epoch:
            continue          # stale trace — progress check already reaped it
        trace_path = _resolve_run_trace_path(run)
        if trace_path is None:
            continue
        task = run.task
        verdict = detect_error_loop(
            trace_path,
            agent=task.assignee.name if task.assignee_id else "",
            model=task.model_name or "",
            threshold=threshold,
            window=window,
            min_lines=min_lines,
        )
        if verdict is None or not verdict.is_looping:
            continue
        _reap_error_loop(
            run, verdict,
            provider_delay=provider_delay, reassign_enabled=reassign_enabled,
        )


@shared_task(name="tasks.dag_executor.reconcile_task_runs")
def reconcile_task_runs():
    """Single periodic reconciler over every RUNNING TaskRun (task #211).

    One owner for "the runner is gone" detection, invoked three ways:
    inline from ``poll_and_execute`` before each dispatch cycle, on its own
    Beat schedule (``TASK_RUN_RECONCILE_INTERVAL_SECONDS`` — unconditional,
    so runs are supervised under every execution strategy, not just
    celery_dag's dispatch cadence), and once at worker boot
    (``config.celery``'s ``worker_ready`` hook) so a restart never leaves an
    orphan waiting for the first periodic pass.

    Three liveness checks, newest-signal-wins:

    1. Lease check (heartbeat-driven, ``TASK_RUN_LEASE_SECONDS`` ~3 min) —
       catches a *dead supervisor*: the subprocess-monitoring loop that
       heartbeats the run is gone (worker crash/restart).
    2. Progress check (trace-mtime-driven, ``TASK_RUN_PROGRESS_WINDOW_SECONDS``
       ~10 min, task #235) — catches a *live supervisor supervising a dead
       agent*: the process is up and heartbeating but the agent inside has
       stopped producing output (host sleep / hung agent). Only fires when
       a trace file exists; a traceless run falls back to (1).
    3. Error-loop check (trace-tail-driven, task #262) — catches a *live
       supervisor writing only errors*: fresh heartbeat, fresh trace, but
       the trace tail is dominated (>= ``TASK_RUN_ERROR_LOOP_THRESHOLD``)
       by one repeated error signature (provider retry loop / quota
       exhaustion). Only examines fresh-trace runs the other checks left
       alone, so it never duplicates a lease or progress reap.

    Then the legacy Task.metadata-based checks (queued-past-deadline / overall
    task timeout — still needed for "supervisor alive, agent just took too
    long", which none of the above would catch).
    """
    _reap_expired_task_run_leases()
    _reap_stalled_progress_runs()
    _reap_error_loop_runs()
    _recover_stale_executions()


def _recover_stale_executions():
    """Recover EXECUTING tasks whose runner is gone, adopting live orphans.

    A worker restart can orphan a still-running ``odin exec`` child (it runs
    in its own session). If ``active_execution.pid`` is alive AND its cmdline
    is ``odin exec <task_id>``, the orphan is ADOPTED — kept EXECUTING and not
    re-fired (avoids two live ``odin exec`` processes sharing one worktree).
    Only a dead or cmdline-mismatched (pid-recycled) PID — or no PID past the
    queue deadline, or a runnerless timeout — is failed and re-dispatched.

    Folded into ``reconcile_task_runs`` (task #211) as the legacy half of the
    unified reconciler — still needed for tasks whose lease hasn't expired
    (supervisor alive) but which are stuck past their own deadline.
    """
    now = time.time()
    # Queued-but-not-started is NORMAL under load: execute_single_task jobs
    # wait in the celery queue behind long-running executions (worker pool is
    # small, tasks run up to 30 min). The no-pid deadline must cover worst-case
    # queue wait, not a "should have started by now" guess — a 120s deadline
    # repeatedly killed healthy queued dispatches at wave concurrency (F53).
    queued_stale_seconds = int(getattr(settings, "DAG_EXECUTOR_QUEUED_STALE_SECONDS", 1800) or 1800)
    task_timeout_seconds = int(getattr(settings, "DAG_EXECUTOR_TASK_TIMEOUT_SECONDS", 1800) or 1800)

    for task in Task.objects.filter(status=TaskStatus.EXECUTING):
        metadata = dict(task.metadata or {})
        active = dict(metadata.get("active_execution") or {})
        pid = active.get("pid")
        queued_at = float(active.get("queued_at") or 0)
        started_at = float(active.get("started_at") or 0)
        reason = None
        failure_type = "stale_execution"

        # A recorded PID that is alive AND genuinely running `odin exec
        # <task_id>` is a surviving orphan from a worker restart — ADOPT it
        # (keep tracking, do not re-fire). The in-worker wall-clock deadline
        # no longer applies once the worker that owned it is gone, so a
        # verified orphan is left alone even past the timeout; only a dead or
        # mismatched (pid-recycled) PID triggers re-dispatch.
        pid_alive = bool(pid) and _pid_is_alive(pid)
        pid_verified = pid_alive and _pid_cmdline_matches(task.id, pid)
        if pid_verified:
            continue

        if pid and not pid_alive:
            reason = (
                f"Odin execution process pid={pid} is no longer running "
                "and did not report a final result"
            )
        elif pid and pid_alive and not pid_verified:
            reason = (
                f"Recorded execution pid={pid} is alive but its cmdline does "
                f"not match `odin exec {task.id}` (pid recycled by another "
                "process) — treating as a lost runner"
            )
        elif not pid and queued_at and now - queued_at > queued_stale_seconds:
            reason = "Task was marked EXECUTING but no Odin runner process was recorded"
        elif started_at and task_timeout_seconds > 0 and now - started_at > task_timeout_seconds:
            reason = f"Task execution timed out after {task_timeout_seconds}s"
            failure_type = "timeout"

        if not reason:
            continue

        _fail_stale_execution(
            task, reason=reason, failure_type=failure_type,
            run_token=active.get("run_token"), run_state=TaskRunState.EXPIRED,
        )


# Infra-class failures the dag_executor auto-redispatches itself.  Every
# operator-visible failure today (stale_execution recovery kills, disk-full
# silent deaths, provider truncation) required a human to PATCH the task
# back to IN_PROGRESS by hand.  The failure taxonomy already distinguishes
# these from real work failures — the system retries them itself.
INFRA_FAILURE_TYPES = frozenset({"stale_execution", "sandbox_unavailable"})

# Substrings in last_failure_reason that mark an agent_execution_failure as
# an infra-class truncation (provider killed the agent mid-flight, network
# drop, output-cap hit, etc.) rather than a real failure the agent
# reported.  Mirrors the wording emitted by odin.harnesses.base.parse_odin_status
# (the canonical "Agent did not emit an ODIN-STATUS block. Likely the model
# truncated mid-generation or the response terminated silently ...").
INFRA_REASON_SIGNATURES = (
    "did not emit an odin-status",
    "did not emit odin-status",
    "truncated mid-generation",
    "response truncated",
    "terminated silently",
    "produced no output",
)

INFRA_AUTO_REDISPATCH_CAP = 2
INFRA_AUTO_REDISPATCH_META_KEY = "auto_redispatch_count"
INFRA_AUTO_REDISPATCH_HISTORY_KEY = "auto_redispatch_history"
INFRA_AUTO_REDISPATCH_HISTORY_MAX = 10


def _is_infra_failure(task):
    """Return (is_infra, class_label) for a FAILED task's failure classification.

    class_label is a short, stable identifier suitable for the metadata audit
    trail and the status_update comment that names the retry:
      - ``stale_execution`` — the worker died / no ODIN runner recorded
      - ``agent_execution_failure:no_odin_status`` — the agent process was
        killed mid-flight by provider truncation / network drop / output cap
      - ``(False, None)`` — anything else (real failure needing judgment)
    """
    metadata = task.metadata or {}
    failure_type = (metadata.get("last_failure_type") or "").strip().lower()
    if not failure_type:
        return False, None
    if failure_type in INFRA_FAILURE_TYPES:
        return True, failure_type
    if failure_type == "agent_execution_failure":
        reason = (metadata.get("last_failure_reason") or "").lower()
        if any(sig in reason for sig in INFRA_REASON_SIGNATURES):
            return True, "agent_execution_failure:no_odin_status"
    return False, None


def _maybe_auto_redispatch_infra_failure(task, policy=None, *, advice_text=""):
    """Auto-retry a FAILED task whose failure class is auto-requeue (W4.4).

    Two paths converge here:

    1. **Policy-aware** (when ``policy`` is supplied): the dispatcher in
       ``failure_policy.apply_failure_policy`` resolves the per-class
       policy from the failure taxonomy + settings overrides and passes
       it in.  The policy supplies ``max_retries`` (the retry bound) and
       ``description`` (the audit comment wording).  This extends
       auto-retry to ``transport_error`` / ``truncation`` / ``silent_hang``
       / ``lock_race`` (and keeps it for ``stale_execution``).

    2. **Legacy type-based** (when ``policy`` is None): the original
       #137 behaviour — looks up ``last_failure_type`` /
       ``last_failure_reason`` for the legacy infra signatures
       (``stale_execution`` or ``agent_execution_failure`` with the
       "did not emit ODIN-STATUS / truncated / terminated silently"
       signature).  Hardcoded cap ``INFRA_AUTO_REDISPATCH_CAP`` (=2).
       Kept so tasks whose metadata pre-dates W4.4 still auto-retry.

    Failure classes that NEVER auto-retry (and need human judgment):
    ``backend_auth_failure``, ``timeout``, ``missing_worktree``,
    ``llm_call_failure``, agent-reported FAILED in ODIN-STATUS,
    reflection FAIL, etc.

    Cap: ``INFRA_AUTO_REDISPATCH_CAP`` (=2) for legacy path or
    ``policy.max_retries`` for the policy-aware path.  Counter lives at
    ``metadata.auto_redispatch_count``; per-attempt audit entries land
    in ``metadata.auto_redispatch_history`` (capped at 10 entries).

    Continuity: reuses the F45 rework-continuity path
    (``views._record_rework_continuity``) so the audit trail shows the
    agent + model were intentionally preserved across the retry — the
    same continuity contract NEEDS_WORK reflections honour.

    Returns ``True`` if the task was redispatched (FAILED → IN_PROGRESS),
    ``False`` otherwise.
    """
    if task.status != TaskStatus.FAILED:
        return False

    metadata = dict(task.metadata or {})

    # Resolve the class label + cap.  Policy-aware path uses the
    # failure_class + policy table; legacy path uses the type-based
    # classifier.
    if policy is not None:
        if policy.action != "auto_requeue":
            return False
        class_label = (metadata.get("failure_class") or "").strip() or "unknown"
        cap = int(policy.max_retries or 0)
        if cap <= 0:
            return False
        cap_reached_key = "policy_cap_reached"
    else:
        is_infra, class_label = _is_infra_failure(task)
        if not is_infra:
            return False
        cap = INFRA_AUTO_REDISPATCH_CAP
        cap_reached_key = "auto_redispatch_cap_reached"

    counter = int(metadata.get(INFRA_AUTO_REDISPATCH_META_KEY, 0) or 0)
    if counter >= cap:
        # Idempotent: stamp the cap-reached flag at most once per cycle so
        # the audit trail is unambiguous about *when* the cap was hit.
        cap_already_set = bool(metadata.get(cap_reached_key)) or bool(
            metadata.get("auto_redispatch_cap_reached"),
        )
        if not cap_already_set:
            metadata[cap_reached_key] = True
            metadata["auto_redispatch_cap_reached"] = True  # legacy alias
            metadata[f"{cap_reached_key}_at"] = timezone.now().isoformat()
            metadata["auto_redispatch_cap_reached_at"] = timezone.now().isoformat()
            metadata[f"{cap_reached_key}_class"] = class_label
            metadata["auto_redispatch_cap_reached_class"] = class_label
            Task.objects.filter(id=task.id).update(metadata=metadata)
            task.metadata = metadata
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="odin+dag-executor@system",
                author_label="odin-dag-executor",
                content=(
                    f"Auto-redispatch cap reached ({counter}/{cap}) "
                    f"for failure class ({class_label}). Task left FAILED "
                    f"for human review."
                ),
                comment_type=CommentType.STATUS_UPDATE,
            )
        logger.info(
            "[task:%s] Auto-requeue failure class %s but cap (%d) reached — leaving FAILED",
            task.id, class_label, cap,
        )
        return False

    if not task.assignee_id:
        # Without an assignee we can't redispatch. The original failure
        # comment remains; the operator must intervene.
        logger.warning(
            "[task:%s] Auto-requeue failure class %s but task has no assignee — skipping",
            task.id, class_label,
        )
        return False

    original_assignee_id = task.assignee_id
    original_model = task.model_name

    with transaction.atomic():
        locked_task = Task.objects.select_for_update().get(id=task.id)
        if locked_task.status != TaskStatus.FAILED:
            return False  # someone else already moved it

        # Re-check the cap inside the lock in case a concurrent worker beat
        # us to the increment.
        metadata = dict(locked_task.metadata or {})
        counter = int(metadata.get(INFRA_AUTO_REDISPATCH_META_KEY, 0) or 0)
        if counter >= cap:
            return False

        counter += 1
        metadata[INFRA_AUTO_REDISPATCH_META_KEY] = counter
        history = list(metadata.get(INFRA_AUTO_REDISPATCH_HISTORY_KEY) or [])
        history.append({
            "class": class_label,
            "attempt": counter,
            "at": timezone.now().isoformat(),
            "reason": (metadata.get("last_failure_reason") or "")[:300],
            "policy_action": "auto_requeue" if policy else "infra_legacy",
        })
        # Keep the history bounded — the audit trail should be useful, not
        # an unbounded log of every retry.
        metadata[INFRA_AUTO_REDISPATCH_HISTORY_KEY] = history[-INFRA_AUTO_REDISPATCH_HISTORY_MAX:]
        metadata["last_rework_reason"] = "infra_auto_redispatch"
        metadata["last_rework_at"] = timezone.now().isoformat()
        # Task #353: clear the stale dispatch-blocked banner a previous gate
        # left on this task. The auto-redispatch path always proceeds
        # (FAILED → IN_PROGRESS + strategy.trigger), so any pre-existing
        # stamp would lie to the operator about a task that is now live.
        # If a fresh dispatch gate later holds the task, _set_* stamps it
        # again with the live holder list.
        for key in (
            "dispatch_blocked_reason",
            "dispatch_blocked_at",
            "dispatch_blocked_blocked_by",
        ):
            metadata.pop(key, None)
        # NOTE: rework_count is bumped by the F45 _record_rework_continuity
        # call below (it covers both NEEDS_WORK and infra auto-redispatch
        # to keep the counter in one place). Bumping here would double-count.

        locked_task.metadata = metadata
        locked_task.status = TaskStatus.IN_PROGRESS
        locked_task.kanban_position = move_task(
            locked_task, target_status=TaskStatus.IN_PROGRESS, target_index=None,
        )
        locked_task.save(update_fields=[
            "status", "kanban_position", "metadata", "last_updated_at",
        ])

        TaskHistory.objects.create(
            task=locked_task,
            schedule_run=locked_task.current_schedule_run,
            field_name="status",
            old_value=TaskStatus.FAILED,
            new_value=TaskStatus.IN_PROGRESS,
            changed_by="odin+dag-executor@system",
        )

    # Reuse the F45 rework-continuity helper so the audit trail shows the
    # agent + model were intentionally preserved across the auto-retry.
    # suppress_comment=True: we post our own failure-class-naming comment
    # below — the generic continuity comment would clobber that signal.
    try:
        from .views import _record_rework_continuity
        _record_rework_continuity(
            locked_task, original_assignee_id, original_model, suppress_comment=True,
        )
    except Exception:
        logger.exception(
            "[task:%s] Failed to record rework continuity for auto-requeue",
            locked_task.id,
        )

    # Post the failure-class-naming comment — the operator-visible signal
    # that this FAILED → IN_PROGRESS transition was system-driven, not human.
    last_reason = (locked_task.metadata or {}).get("last_failure_reason", "")
    policy_line = (
        f"\nPolicy: {policy.action} (max_retries={policy.max_retries}, "
        f"backoff={policy.backoff_seconds}s). {policy.description}"
        if policy else ""
    )
    advice_block = f"\n\n{advice_text}" if advice_text else ""
    TaskComment.objects.create(
        task=locked_task,
        schedule_run=locked_task.current_schedule_run,
        author_email="odin+dag-executor@system",
        author_label="odin-dag-executor",
        content=(
            f"Auto-redispatch {counter}/{cap}: "
            f"failure class ({class_label}) — retrying with same "
            f"assignee and model.\n"
            f"Failure class: {class_label}\n"
            f"Reason: {last_reason[:300] if last_reason else chr(45)}"
            f"{policy_line}{advice_block}"
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    # Fire the execution strategy so the task actually runs again.  If no
    # strategy is configured the task sits IN_PROGRESS and the next
    # poll_and_execute cycle will pick it up.
    try:
        from .execution import get_strategy
        strategy = get_strategy()
    except Exception:
        logger.exception(
            "[task:%s] Failed to import execution strategy for auto-requeue",
            locked_task.id,
        )
        strategy = None

    if strategy:
        logger.info(
            "[task:%s] Firing execution strategy for auto-requeue %d/%d",
            locked_task.id, counter, cap,
        )
        try:
            strategy.trigger(locked_task)
        except Exception:
            logger.exception(
                "[task:%s] Execution strategy.trigger failed during auto-requeue",
                locked_task.id,
            )
    else:
        logger.warning(
            "[task:%s] Auto-requeue %d/%d fired but no strategy configured — "
            "task sits IN_PROGRESS for poll_and_execute to pick up",
            locked_task.id, counter, cap,
        )

    return True


def _is_macos() -> bool:
    """True on a macOS host (where caffeinate can prevent idle sleep)."""
    return sys.platform == "darwin"


# Sleep-aware deadline clock. Resolved lazily at SleepAwareDeadline init so
# tests can override ``tasks.dag_executor._default_deadline_clock`` with a
# scripted clock without patching the global ``time.monotonic`` (which asyncio
# and other internals also read). Production reads the real ``time.monotonic``.
_default_deadline_clock = time.monotonic


class SleepAwareDeadline:
    """A deadline that excludes host-sleep gaps from the timeout budget.

    The old watchdog computed its deadline with ``time.time()`` (wall-clock),
    so when the host slept overnight the elapsed wall time silently consumed
    the whole budget and the watchdog failed a task that had barely run.

    This clock uses ``time.monotonic()`` as its base. On Linux
    (``CLOCK_MONOTONIC``) the monotonic clock already stops during system
    suspend, so sleep is excluded for free. On macOS ``mach_absolute_time``
    keeps advancing across sleep, so we additionally *detect* a sleep gap: on
    every poll we compare the wall delta since the last sample to the expected
    poll interval; a delta far larger than expected (default: >3x the interval
    and >5s) is treated as host sleep and the deadline is pushed out by the
    excess. Genuine scheduler jitter and short stalls stay well under the
    threshold and are charged normally. The net effect: a task is timed out
    only for time it was actually awake and running.
    """

    def __init__(self, timeout_seconds, clock=None,
                 sleep_gap_factor=3.0, min_sleep_gap=5.0):
        self._clock = clock if clock is not None else _default_deadline_clock
        self._timeout = float(timeout_seconds)
        start = self._clock()
        self._deadline = start + self._timeout
        self._last_sample = start
        self._factor = sleep_gap_factor
        self._min_gap = min_sleep_gap
        self.total_sleep_excluded = 0.0

    def sample(self, expected_interval):
        """Advance one poll. Absorb a host-sleep gap if the wall delta since
        the last sample dwarfs the expected poll interval."""
        now = self._clock()
        delta = now - self._last_sample
        threshold = max(expected_interval * self._factor, self._min_gap)
        if delta > threshold:
            gap = delta - expected_interval
            self._deadline += gap
            self.total_sleep_excluded += gap
        self._last_sample = now
        return self

    def expired(self):
        return self._clock() > self._deadline

    @property
    def remaining(self):
        return max(0.0, self._deadline - self._clock())


@contextlib.contextmanager
def caffeinate_assertion():
    """Hold a ``caffeinate -i`` assertion while a task is EXECUTING (macOS).

    Prevents idle sleep for the duration of the wrapped block so an overnight
    run is not suspended mid-execution. No-op on non-macOS hosts. Best-effort:
    if the ``caffeinate`` binary is unavailable the assertion is skipped (the
    sleep-aware deadline remains the safety net) rather than failing the task.
    """
    proc = None
    if _is_macos():
        try:
            proc = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            logger.warning(
                "caffeinate -i could not be started; proceeding without a "
                "sleep assertion (deadline stays sleep-aware)",
                exc_info=True,
            )
            proc = None
    try:
        yield proc
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _run_subprocess_with_cancellation(task_id, cmd, working_dir, log_file, run_token=None):
    """Run odin exec while supporting stop requests via task metadata.

    The execution deadline is sleep-aware (see ``SleepAwareDeadline``): host
    sleep time is detected and excluded from the budget, and a ``caffeinate``
    assertion is held on macOS to keep the host awake. Before any timeout kill
    the process is re-checked for liveness — a process that just exited is
    reaped with its real exit code instead of being mislabelled "timeout".
    """
    failure_stage = "none"
    env = os.environ.copy()
    if run_token:
        env["ODIN_TASK_RUN_TOKEN"] = run_token

    try:
        with caffeinate_assertion():
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
                # started_at stays wall-clock: it is an observational stamp
                # read cross-process by _recover_stale_executions (which
                # adopts verified-live orphans regardless of this value).
                active["started_at"] = time.time()
                metadata["active_execution"] = active
                task.metadata = metadata
                task.save(update_fields=["metadata"])
                task_runs.heartbeat_run(active.get("run_token"), pid=proc.pid)
                last_heartbeat_at = time.time()

                timeout_seconds = int(
                    getattr(settings, "DAG_EXECUTOR_TASK_TIMEOUT_SECONDS", 1800) or 1800
                )
                deadline = (
                    SleepAwareDeadline(timeout_seconds)
                    if timeout_seconds and timeout_seconds > 0
                    else None
                )

                while True:
                    try:
                        exit_code = proc.wait(timeout=CANCEL_POLL_INTERVAL_SECONDS)
                        if exit_code != 0 and failure_stage == "none":
                            failure_stage = "odin_non_zero_exit"
                        return exit_code, failure_stage
                    except subprocess.TimeoutExpired:
                        # F48 ordering fix: cancellation / run-token / liveness
                        # checks come BEFORE the deadline check. The old
                        # order fired the deadline branch first and SIGTERM'd
                        # fresh attempts whose run_token had already been
                        # superseded (or whose run was user-cancelled) as a
                        # "timeout" instead of as run_token_mismatch /
                        # cancelled. That misclassification cost downstream
                        # consumers the signal they need to retry correctly.
                        if deadline is not None:
                            deadline.sample(CANCEL_POLL_INTERVAL_SECONDS)
                        task.refresh_from_db()
                        active = dict((task.metadata or {}).get("active_execution") or {})

                        # Piggyback the TaskRun heartbeat on this existing
                        # per-second touchpoint (one mechanism, not two).
                        # Throttled so a long-running task doesn't hammer the
                        # DB with a write every second.
                        now = time.time()
                        if now - last_heartbeat_at >= TASK_RUN_HEARTBEAT_INTERVAL_SECONDS:
                            task_runs.heartbeat_run(active.get("run_token"))
                            last_heartbeat_at = now

                        if (
                            run_token
                            and active.get("run_token")
                            and active.get("run_token") != run_token
                        ):
                            # A newer dispatch superseded this attempt; the
                            # metadata's active_execution.run_token already
                            # carries the new attempt's token. Force-kill so
                            # the new subprocess can take its place quickly.
                            _terminate_process(proc, force=True)
                            return -1, "run_token_mismatch"
                        if active.get("cancel_requested"):
                            _terminate_process(proc, force=False)
                            return -1, "cancelled"
                        # Only fall through to a deadline kill when nothing
                        # else applies AND the process is still genuinely
                        # running past its (sleep-aware) budget. Re-verify
                        # liveness first: a process that exited at the
                        # boundary must be reaped, not timed out.
                        if deadline is not None and deadline.expired():
                            if proc.poll() is not None:
                                return proc.returncode, "none"
                            _terminate_process(proc, force=False)
                            return -1, "timeout"
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
    from django.utils import timezone

    from .models import ReflectionReport, ReflectionStatus

    try:
        report = ReflectionReport.objects.select_related("task", "task__board").get(id=report_id)
    except ReflectionReport.DoesNotExist:
        logger.error("ReflectionReport %s not found", report_id)
        return

    if report.status != ReflectionStatus.PENDING:
        logger.info(
            "ReflectionReport %s is %s, expected PENDING — skipping",
            report_id, report.status,
        )
        return

    # Global memory budget gate: a reflection spawns its own ~4 GB reviewer
    # microVM. When the shared budget is full (executions + other reflections
    # holding it), re-delay this task and stay PENDING — wait in line instead
    # of booting into swap. The retry terminates as soon as a live VM releases
    # (executions are timeout-bounded, so the budget always drains).
    if not spawn_fits(default_vm_mem_mib()):
        retry_seconds = int(
            getattr(settings, "SANDBOX_REFLECTION_RETRY_SECONDS", 10) or 10
        )
        logger.info(
            "[reflection:%s] memory budget full — re-delaying %ds to wait in line",
            report_id, retry_seconds,
        )
        execute_reflection.apply_async((report_id,), countdown=retry_seconds)
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
    if report.selection_reason:
        # W3.18 — surface why this reviewer was picked. Stored on the
        # ReflectionReport and visible in the report's metadata so the
        # operator can audit the selection decision (e.g. why a cheap
        # reviewer was used for a small task).
        cmd.extend(["--selection-reason", report.selection_reason])

    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"reflect_{task.id}_{report.id}.log"

    logger.info(
        "Executing reflection: cmd=%s, cwd=%s, report_id=%s",
        cmd, working_dir, report.id,
    )

    timeout_seconds = int(
        getattr(settings, "DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS", 1800) or 1800
    )
    failure_reason = "Reflection process exited without updating report"

    try:
        with open(log_file, "w") as f:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        logger.error("Reflection %s timed out after %ss", report_id, timeout_seconds)
        failure_reason = f"Reflection timed out after {timeout_seconds}s before updating report"
        exit_code = -1
    except Exception as exc:
        logger.exception("Failed to execute reflection %s", report_id)
        failure_reason = f"Failed to execute reflection subprocess: {exc}"
        exit_code = -1

    # Odin normally PATCHes the report itself. If it didn't, set a fallback status.
    report.refresh_from_db()
    if report.status == ReflectionStatus.RUNNING:
        report.status = ReflectionStatus.FAILED if exit_code != 0 else ReflectionStatus.COMPLETED
        report.error_message = failure_reason
        report.completed_at = timezone.now()
        report.save(update_fields=["status", "error_message", "completed_at"])
        logger.warning(
            "Reflection %s fallback status: %s (exit_code=%s)",
            report_id, report.status, exit_code,
        )
    else:
        logger.info("Reflection %s completed with status: %s", report_id, report.status)


_MIGRATION_REPLY_HINT = (
    '\n\nReply "merge them" and I\'ll run `makemigrations --merge` '
    "automatically — it creates a new merge migration and never edits "
    "existing files. To renumber a leaf instead, make the change on the "
    "task branch yourself (operator-only — I can't safely rename "
    "migration files) then reply."
)


def _format_migration_parking_comment(merge_result) -> str:
    """Build the QUESTION comment for a migration leaf collision.

    Reuses ``merge_result.error`` (produced by
    ``odin.worktree._format_migration_conflict_error``, which names every
    leaf and both fixes) and appends a reply-action layer so the human
    knows exactly what the guided resume can execute (``makemigrations
    --merge``) versus what needs an operator (renumbering).  This is the
    plain-spoken answer to the W5.11 scope addition: the bounded
    branch-edit power is named, and the operator-only class is named.
    """
    base = (
        merge_result.error
        or "Django migration leaf collision detected — see merge error."
    )
    return base + _MIGRATION_REPLY_HINT


def _format_gate_violation_guidance(violations, diff_stat):
    """Build the ``resolution_guidance`` string for the post-merge gate
    retry (task 338).

    The merge agent's ``interpret_guidance`` matches on short phrases
    (``keep both``, ``task side``, ``spec side``, ``move``) — when one
    matches on an ambiguous conflict, the agent re-runs resolution
    with that action. For a gate refusal we lead with the exact
    file/line/error triple so the operator AND the merge agent both
    have the strongest possible signal to pick a different strategy
    on the retry attempt.

    The format is human-readable on purpose — the same string is
    surfaced in the STATUS_UPDATE comment that explains the retry,
    so the diff stat + violation list appear identically in both
    places.
    """
    lines = [
        "Post-merge safety gate refused the merge — the merge commit "
        "was rolled back so the spec branch is unchanged. The exact "
        "violations the gate caught:",
        "",
    ]
    for path, error_type, line_no in violations:
        kind = (
            "conflict marker" if error_type == "conflict_marker"
            else "syntax error" if error_type == "syntax_error"
            else error_type
        )
        if line_no:
            lines.append(f"- {path} line {line_no}: {kind}")
        else:
            lines.append(f"- {path}: {kind}")
    lines.append("")
    lines.append(
        "Pick a different resolution this time so the merged file "
        "contains neither conflict markers nor invalid Python. If no "
        "different resolution is available, the merge will park "
        "permanently after this attempt."
    )
    if diff_stat:
        lines.append("")
        lines.append("Diff stat for context:")
        lines.append("```")
        lines.append(diff_stat)
        lines.append("```")
    return "\n".join(lines)


def _format_gate_violation_comment(
    task, branch, spec_branch, violations, *, attempts, parked, diff_stat,
) -> str:
    """Build the QUESTION comment for a post-merge safety gate refusal
    (task 338).

    Names every violation (path + line + error_type) so the operator
    can locate the breakage in seconds. The ``attempts`` counter and
    ``parked`` flag drive the messaging tone:
    - First failure: "this is what broke; fix and reply 're-merge' to
      try once more."
    - Second failure: "the merge is parked permanently after two
      attempts; here is the diff so you can decide what to do."

    The diff block is the actionable artifact — it lets a human read
    the proposed merged content without checking out the worktree,
    which is the exact behavior task 338 asks for ("after two
    failures it parks for a human with the diff").
    """
    lines = [
        "**Post-merge safety gate refused the merge** (task 338).",
        "",
        f"Merging `{branch}` into `{spec_branch}` would have committed "
        f"{len(violations)} broken file(s). The merge was rolled back "
        f"so the spec branch is unchanged.",
        "",
        "Violations:",
    ]
    for path, error_type, line_no in violations:
        kind = (
            "conflict marker" if error_type == "conflict_marker"
            else "syntax error" if error_type == "syntax_error"
            else error_type
        )
        if line_no:
            lines.append(f"- `{path}` line {line_no}: {kind}")
        else:
            lines.append(f"- `{path}`: {kind}")
    lines.append("")
    if parked:
        lines.append(
            f"This merge has been refused **{attempts}** times by the "
            "safety gate — parking permanently. The gate is deterministic "
            "(same input produces the same output), so further retries "
            "without manual intervention will fail the same way. Please "
            "inspect the diff below and either (a) push the corrected "
            "files to the task branch and reply 're-merge', or (b) merge "
            "the branch by hand (`odin merge`) after fixing it locally."
        )
    else:
        lines.append(
            f"This is gate attempt **{attempts} of 2** — the merge was "
            "rolled back; reply 're-merge' once you've inspected the "
            "violations and either fixed the files in the worktree or "
            "decided the merge needs a human. A second gate refusal "
            "parks this merge permanently."
        )
    if diff_stat:
        lines.append("")
        lines.append("```")
        lines.append(diff_stat)
        lines.append("```")
    return "\n".join(lines)


@shared_task(name="tasks.dag_executor.merge_task_on_reflection")
def merge_task_on_reflection(task_id):
    """Merge a task branch into its spec branch, then advance REVIEW → TESTING.

    Runs in the Celery worker where odin is importable.
    Called after reflection passes.  The status transition is deferred to
    *after* the merge so that downstream tasks never fork from a spec
    branch that is missing upstream code.

    Every early-return path emits a log line so a stuck task is never silent
    (regression: task #171 — 9 wave-3 tasks stalled because the dispatch
    guard returned without logging; see ``_merge_task_on_reflection_pass``).

    Merge-failure invariant (W5.11): a merge result parks with
    ``merge_status = "needs_human"`` iff a human comment could resolve it.
    One flag, one meaning — the reply-resume listener
    (``tasks.signals.resume_merge_on_human_reply``) only fires on that
    status, so any human-answerable failure MUST land here:

      Human-answerable (→ needs_human, posts a QUESTION):
        * migration leaf collision — reply "merge them" triggers the
          bounded ``makemigrations --merge`` fix; renumbering is
          operator-only.
        * ambiguous content conflict — reply keep-both / task-side /
          spec-side / move (see ``odin.merge_agent``).

      NOT human-answerable (→ error, owns the auto-requeue path):
        * network timeout, push failure, worktree-not-available,
          subprocess exception during the migration check (the check
          itself degrades to ``has_conflict=False`` so the merge is not
          blocked on a blown-up probe).  Retry is the answer, not a human.
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
        # W3.22 (task #171): log the skip so a stalled task is never silent.
        logger.info(
            "[task:%s] merge_task_on_reflection: no branch on metadata — "
            "advancing REVIEW → TESTING without merge",
            task.id,
        )
        _advance_task_to_testing(task)
        return

    if (task.metadata or {}).get("merge_status") == "merged":
        # Already merged (duplicate dispatch) — ensure status is advanced.
        # W3.22 (task #171): log so a duplicate dispatch is visible.
        logger.info(
            "[task:%s] merge_task_on_reflection: merge_status=merged — "
            "duplicate dispatch, advancing REVIEW → TESTING",
            task.id,
        )
        _advance_task_to_testing(task)
        return

    try:
        started_at = timezone.now()
        merge_result = _merge_task_branch(task)
        finished_at = timezone.now()
        trigger = (
            MergeTrigger.RETRY
            if (task.metadata or {}).get("merge_dispatch_source") == "watchdog"
            else MergeTrigger.REFLECTION_PASS
        )
        record_merge_attempt(
            task, trigger, merge_result, started_at, finished_at,
            dispatched_at=_parse_merge_dispatched_at((task.metadata or {}).get("merge_dispatched_at")),
        )

        # Post-merge safety gate retry/park protocol (task 338).
        #
        # When the safety gate refuses the merge (conflict_marker /
        # syntax_error violations), the task must go back to the merge
        # agent with the exact file + line so it has one chance to pick
        # a different resolution. Re-running inline (instead of waiting
        # for the watchdog's 30-min idle window) keeps the retry path
        # fast — the acceptance criterion is "a clean merge is not slowed
        # by more than a few seconds." If the retry also fails the
        # existing branch below parks permanently with merge_status =
        # needs_human + merge_gate_parked = True + a QUESTION comment
        # carrying the diff, satisfying "after two failures it parks
        # for a human with the diff."
        #
        # The retry only fires on the FIRST gate refusal. We trigger on
        # ``merge_gate_attempts == 0`` (no prior refusal this task) — if
        # the existing branch below has already run for an earlier
        # refusal, ``merge_gate_attempts`` is >= 1 and we skip straight
        # to the parking logic without an extra re-run.
        metadata = dict(task.metadata or {})
        gate_violations_initial = list(
            getattr(merge_result, "gate_violations", None) or []
        )
        prior_gate_attempts = int(metadata.get("merge_gate_attempts") or 0)
        if (
            merge_result.conflict
            and getattr(merge_result, "needs_human", False)
            and gate_violations_initial
            and prior_gate_attempts == 0
        ):
            # First strike: inline retry with the violation details as
            # resolution_guidance. The merge agent sees the exact
            # file + line + error type and has one chance to pick a
            # different resolution that lands a clean merge.
            guidance = _format_gate_violation_guidance(
                gate_violations_initial, merge_result.diff_stat,
            )
            metadata["merge_gate_attempts"] = 1
            metadata["merge_gate_violations"] = gate_violations_initial
            metadata["merge_gate_parked"] = False
            logger.warning(
                "[task:%s] Post-merge gate refused (attempt 1/2) — "
                "re-running merge with violation guidance",
                task.id,
            )
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="merge-agent@odin",
                author_label="merge-agent",
                content=(
                    f"**Post-merge safety gate refused** "
                    f"(attempt 1/2). Re-running the merge "
                    f"with the violation details as guidance so the "
                    f"merge agent can pick a different resolution. "
                    f"A second gate refusal parks this merge "
                    f"permanently.\n\n"
                    f"```\n{guidance}\n```"
                ),
                comment_type=CommentType.STATUS_UPDATE,
            )
            task.metadata = metadata
            task.save(update_fields=["metadata"])

            started_at = timezone.now()
            merge_result = _merge_task_branch(
                task, resolution_guidance=guidance,
            )
            finished_at = timezone.now()
            retry_trigger = (
                MergeTrigger.RETRY
                if trigger == MergeTrigger.REFLECTION_PASS
                else trigger
            )
            record_merge_attempt(
                task, retry_trigger, merge_result,
                started_at, finished_at,
                dispatched_at=_parse_merge_dispatched_at(
                    metadata.get("merge_dispatched_at"),
                ),
            )
            # Reload metadata — the retry may have persisted context
            # the gate cares about (e.g. merge_dispatched_at).
            metadata = dict(task.metadata or {})

        if merge_result.success and merge_result.noop:
            metadata["merge_status"] = "noop"
        elif merge_result.success and getattr(merge_result, "resolved_files", None):
            metadata["merge_status"] = "merged"
            metadata["merge_agent_resolved"] = True
        elif merge_result.success:
            metadata["merge_status"] = "merged"
        elif (
            merge_result.conflict
            and getattr(merge_result, "needs_human", False)
            and getattr(merge_result, "gate_violations", None)
        ):
            # Gate refused (either on the first attempt that skipped
            # the retry, or on the retry just run above). Increment
            # the counter and park when it reaches 2.
            gate_violations = list(merge_result.gate_violations)
            attempts = int(metadata.get("merge_gate_attempts") or 0) + 1
            metadata["merge_gate_attempts"] = attempts
            metadata["merge_gate_violations"] = gate_violations
            if attempts >= 2:
                metadata["merge_status"] = "needs_human"
                metadata["merge_gate_parked"] = True
            else:
                # Defensive: a gate refusal with merge_gate_attempts
                # == 0 means the retry block above was bypassed. Mark
                # needs_human so a human reply can drive the resume;
                # the retry block on the resumed call will fire then.
                metadata["merge_status"] = "needs_human"
        elif merge_result.conflict and getattr(merge_result, "needs_human", False):
            metadata["merge_status"] = "needs_human"
        elif merge_result.conflict:
            metadata["merge_status"] = "conflict"
        else:
            metadata["merge_status"] = "error"
            # Error ledger (task #222): surface genuine merge failures
            # (not conflicts — those are an expected state that gets a
            # different comment) for triage. Idempotent per merge attempt.
            try:
                from .errors import record_merge_failure
                _spec_branch_for_evt = (
                    f"spec/{task.spec.odin_id}" if task.spec else "spec branch"
                )
                record_merge_failure(
                    task=task,
                    symptom=(
                        f"Merge failed for `{branch}` into `{_spec_branch_for_evt}`: "
                        f"{merge_result.error or 'unknown error'}"
                    ),
                    error=merge_result.error or "",
                    conflicting_files=list(
                        getattr(merge_result, "conflicting_files", []) or []
                    ),
                    source_id=f"task-{task.id}:{trigger}",
                )
            except Exception:
                logger.exception(
                    "[task:%s] error ledger: failed to record merge_failure",
                    task.id,
                )
        if merge_result.error:
            metadata["merge_error"] = merge_result.error
        if merge_result.diff_stat:
            metadata["diff_stat"] = merge_result.diff_stat
        resolved_files = getattr(merge_result, "resolved_files", None) or []
        if resolved_files:
            metadata["merge_agent_files"] = list(resolved_files)
        ambiguous_files = getattr(merge_result, "ambiguous_files", None) or []
        if ambiguous_files:
            metadata["merge_ambiguous_files"] = list(ambiguous_files)
        resolution_rationale = getattr(merge_result, "resolution_rationale", None)
        if resolution_rationale:
            metadata["merge_agent_rationale"] = resolution_rationale
        task.metadata = metadata
        task.save(update_fields=["metadata"])
        logger.info("[task:%s] Post-reflection merge: %s", task.id, metadata["merge_status"])

        # Post a comment so the merge result is visible in the task timeline
        spec_branch = f"spec/{task.spec.odin_id}" if task.spec else "spec branch"
        if merge_result.success and resolved_files:
            # Merge agent auto-resolved mechanical conflicts
            try:
                from odin.merge_agent import format_resolution_comment
                comment_text = format_resolution_comment(
                    branch, spec_branch,
                    _make_resolution_snapshot(merge_result),
                    merge_result.diff_stat,
                )
            except ImportError:
                comment_text = (
                    f"Merge agent resolved {len(resolved_files)} conflict(s) "
                    f"merging `{branch}` into `{spec_branch}`."
                )
        elif merge_result.success and not merge_result.noop:
            comment_text = f"Merged `{branch}` into `{spec_branch}`"
            if merge_result.diff_stat:
                comment_text += f"\n```\n{merge_result.diff_stat}\n```"
        elif merge_result.success and merge_result.noop:
            comment_text = (
                f"No changes to merge from `{branch}` "
                f"(branch already up to date with `{spec_branch}`)"
            )
        elif merge_result.conflict and getattr(merge_result, "needs_human", False):
            # A human reply can resolve this — post a blocking QUESTION
            # so the reply-resume listener (tasks.signals) fires when the
            # human answers.  Three sub-classes reach here:
            #   * post-merge gate refusal (task 338) — files contain
            #     conflict markers or Python syntax errors after merge;
            #     a question shows the violations and the next-step
            #     choice (re-merge after manual fix, or two-strikes
            #     park if it's failed twice already).
            #   * migration leaf collision — ``migration_conflict`` set;
            #     the merge-migration fix is auto-runnable on reply.
            #   * ambiguous content conflict — merge-agent captured
            #     per-file context (hunks + side summaries + verdict).
            gate_violations = getattr(merge_result, "gate_violations", None) or []
            if gate_violations:
                comment_text = _format_gate_violation_comment(
                    task, branch, spec_branch, gate_violations,
                    attempts=int(metadata.get("merge_gate_attempts") or 0),
                    parked=bool(metadata.get("merge_gate_parked")),
                    diff_stat=merge_result.diff_stat,
                )
            elif getattr(merge_result, "migration_conflict", False):
                comment_text = _format_migration_parking_comment(merge_result)
            else:
                from odin.merge_agent import format_merge_question
                from odin.worktree import _is_generated_agent_config

                all_conflicting = list(getattr(merge_result, "conflicting_files", []) or [])
                mechanical = [p for p in all_conflicting if _is_generated_agent_config(p)]
                ambiguous = [p for p in all_conflicting if not _is_generated_agent_config(p)]
                conflict_hunks = getattr(merge_result, "conflict_hunks", None)
                file_resolutions = getattr(merge_result, "file_resolutions", None)
                spec_commit_messages = getattr(merge_result, "spec_commit_messages", None)
                task_commit_messages = getattr(merge_result, "task_commit_messages", None)
                comment_text = format_merge_question(
                    mechanical, ambiguous, task.title,
                    conflict_hunks=conflict_hunks,
                    file_resolutions=file_resolutions,
                    spec_commit_messages=spec_commit_messages,
                    task_commit_messages=task_commit_messages,
                )
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="merge-agent@odin",
                author_label="merge-agent",
                content=comment_text,
                comment_type=CommentType.QUESTION,
            )
            # Skip the status_update below — the question is the notification.
            comment_text = None
        elif merge_result.conflict:
            # Use the shared formatter so the board comment includes the
            # blocking file list and the generated-config hint when
            # applicable (regression for task #112: a bare
            # "Merge conflict:" line left the operator unable to debug
            # from the UI).
            try:
                from odin.worktree import format_merge_conflict_comment
                comment_text = format_merge_conflict_comment(
                    branch, spec_branch, list(getattr(merge_result, "conflicting_files", []) or []),
                )
            except ImportError:
                # Odin not importable in this process — degrade to the
                # old minimal message so we still surface a status.
                comment_text = (
                    f"Merge conflict merging `{branch}` into `{spec_branch}`: "
                    f"{merge_result.error or 'see logs'}"
                )
        else:
            comment_text = (
                f"Merge failed for `{branch}` into `{spec_branch}`: "
                f"{merge_result.error or 'unknown error'}"
            )
        if comment_text is not None:
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

            # Enqueue the spec-branch verify gate (W5 — task #208). After
            # a clean merge we still want one full suite sweep against
            # the spec branch, because two individually-green tasks can
            # still break each other when combined (live case: two
            # tasks minted the same Django migration number; each
            # branch was green, the combination broke the suite).
            # The gate runs on a daemon thread in a fresh temp worktree
            # (NEVER the operator's main checkout — that's the W4
            # regression this gate exists to avoid). It coalesces
            # concurrent merges so the next merge is never blocked, and
            # only flags the spec if any suite is RED.
            if getattr(task, "spec", None) is not None:
                try:
                    from tasks import spec_verify

                    head_sha = spec_verify._spec_branch_head_sha(
                        task.spec.odin_id,
                    )
                    if head_sha:
                        spec_verify.enqueue_spec_verify(
                            spec_id=task.spec.odin_id,
                            head_sha=head_sha,
                            merge_branch=branch,
                        )
                    else:
                        logger.info(
                            "[task:%s] spec-branch HEAD unavailable — "
                            "skipping verify gate enqueue (gate stays "
                            "silent on spec-branch-only merges)",
                            task.id,
                        )
                except Exception:
                    logger.warning(
                        "[task:%s] failed to enqueue spec-branch verify",
                        task.id, exc_info=True,
                    )

        # Advance REVIEW → TESTING only after merge succeeds (or noop).
        # On conflict/error the task stays in REVIEW so the user can
        # resolve the issue before downstream tasks fork.
        if merge_result.success:
            _advance_task_to_testing(task)

            # Upload .proof/task-<id>/ files from the worktree as board
            # attachments.  Best-effort: a failure must never regress the
            # merge or the TESTING flip — the status already advanced.
            try:
                from .proof_upload import attach_task_proof_files
                attach_task_proof_files(task)
            except Exception:
                logger.warning(
                    "[task:%s] proof-file upload failed — merge + TESTING unaffected",
                    task.id, exc_info=True,
                )
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


@shared_task(name="tasks.dag_executor.resume_merge_with_guidance")
def resume_merge_with_guidance(task_id, comment_id):
    """Re-attempt a parked ``needs_human`` merge using a human's reply.

    Dispatched by ``tasks.signals`` when a non-agent comment lands on a
    task whose ``merge_status`` is ``needs_human`` (see
    ``resume_merge_on_human_reply``). The comment's content is the
    resolution guidance handed to the merge agent
    (``odin.merge_agent.resolve_ambiguous_with_guidance``) — it never
    guesses on a file the guidance doesn't cover, so this can re-ask
    just as the original conflict did.

    Guarded against races (two comments landing close together, or the
    watchdog reconciling the flag first): re-checks ``merge_status`` is
    still ``needs_human`` before doing anything.
    """
    from .models import CommentType

    try:
        task = Task.objects.select_related("spec", "board").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("resume_merge_with_guidance: Task %s not found", task_id)
        return

    if (task.metadata or {}).get("merge_status") != "needs_human":
        logger.info(
            "[task:%s] resume_merge_with_guidance: merge_status is no "
            "longer needs_human — skipping (already resolved elsewhere)",
            task.id,
        )
        return

    try:
        guidance_comment = TaskComment.objects.get(id=comment_id, task=task)
    except TaskComment.DoesNotExist:
        logger.error(
            "[task:%s] resume_merge_with_guidance: guidance comment %s not found",
            task.id, comment_id,
        )
        return
    guidance = guidance_comment.content

    branch = (task.metadata or {}).get("branch")
    if not branch:
        logger.warning(
            "[task:%s] resume_merge_with_guidance: no branch on metadata — nothing to merge",
            task.id,
        )
        return

    spec_branch = f"spec/{task.spec.odin_id}" if task.spec else "spec branch"

    try:
        started_at = timezone.now()
        merge_result = _merge_task_branch(task, resolution_guidance=guidance)
        finished_at = timezone.now()
        record_merge_attempt(
            task, MergeTrigger.HUMAN_RESUME, merge_result, started_at, finished_at,
            dispatched_at=guidance_comment.created_at,
        )
        metadata = dict(task.metadata or {})

        if merge_result.success:
            metadata["merge_status"] = "merged" if not merge_result.noop else "noop"
            metadata["merge_agent_resolved"] = True
            metadata["merge_resolution_guidance"] = guidance
            metadata.pop("merge_ambiguous_files", None)
            metadata.pop("merge_error", None)
            resolution_rationale = getattr(merge_result, "resolution_rationale", None)
            if resolution_rationale:
                metadata["merge_agent_rationale"] = resolution_rationale
            resolved_files = getattr(merge_result, "resolved_files", None) or []
            if resolved_files:
                metadata["merge_agent_files"] = list(resolved_files)
            task.metadata = metadata
            task.save(update_fields=["metadata"])

            comment_text = (
                f"Applied your guidance and completed the merge of `{branch}` "
                f"into `{spec_branch}`.\n\n"
                f'Guidance: "{guidance.strip()}"\n\n'
                f"{resolution_rationale or ''}"
            ).strip()
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="merge-agent@odin",
                author_label="merge-agent",
                content=comment_text,
                comment_type=CommentType.STATUS_UPDATE,
            )

            try:
                wt = _get_worktree_manager(task)
                if wt:
                    wt.update_spec_worktree(task.spec.odin_id)
            except Exception:
                logger.warning(
                    "Failed to update spec worktree after guided merge for task %s",
                    task.id, exc_info=True,
                )

            _advance_task_to_testing(task)
            logger.info(
                "[task:%s] resume_merge_with_guidance: merged using human guidance",
                task.id,
            )

        elif merge_result.conflict and getattr(merge_result, "needs_human", False):
            # Guidance didn't resolve the conflict — re-ask rather than
            # guess.  For migration collisions the merge-migration fix
            # either wasn't requested (reply lacked "merge") or failed;
            # re-present the options so the human knows what the guided
            # resume can execute vs what needs an operator.
            metadata["merge_status"] = "needs_human"
            if merge_result.error:
                metadata["merge_error"] = merge_result.error
            task.metadata = metadata
            task.save(update_fields=["metadata"])

            gate_violations = getattr(merge_result, "gate_violations", None) or []
            if gate_violations:
                # Post-merge safety gate (task 338) refused again —
                # count this as the second strike and park permanently.
                attempts = int(metadata.get("merge_gate_attempts") or 0) + 1
                metadata["merge_gate_attempts"] = attempts
                metadata["merge_gate_violations"] = gate_violations
                metadata["merge_gate_parked"] = True
                task.metadata = metadata
                task.save(update_fields=["metadata"])
                followup = _format_gate_violation_comment(
                    task, branch, spec_branch, gate_violations,
                    attempts=attempts, parked=True,
                    diff_stat=merge_result.diff_stat,
                )
                comment_text = (
                    f'Your reply ("{guidance.strip()}") didn\'t get past the '
                    f"post-merge safety gate — the same files are still "
                    f"broken. Parking permanently after {attempts} gate "
                    f"refusal(s).\n\n{followup}"
                )
            elif getattr(merge_result, "migration_conflict", False):
                followup = _format_migration_parking_comment(merge_result)
                comment_text = (
                    f'Your reply ("{guidance.strip()}") didn\'t complete '
                    f"the merge — the migration collision is still "
                    f"present.\n\n{followup}"
                )
            else:
                from odin.merge_agent import format_merge_question
                from odin.worktree import _is_generated_agent_config

                all_conflicting = list(getattr(merge_result, "conflicting_files", []) or [])
                mechanical = [p for p in all_conflicting if _is_generated_agent_config(p)]
                ambiguous = [p for p in all_conflicting if not _is_generated_agent_config(p)]
                followup = format_merge_question(
                    mechanical, ambiguous, task.title,
                    conflict_hunks=getattr(merge_result, "conflict_hunks", None),
                    file_resolutions=getattr(merge_result, "file_resolutions", None),
                    spec_commit_messages=getattr(merge_result, "spec_commit_messages", None),
                    task_commit_messages=getattr(merge_result, "task_commit_messages", None),
                )
                comment_text = (
                    f'Your reply ("{guidance.strip()}") didn\'t give a clear '
                    f"resolution for every conflicted file, so nothing was "
                    f"applied yet.\n\n{followup}"
                )
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="merge-agent@odin",
                author_label="merge-agent",
                content=comment_text,
                comment_type=CommentType.QUESTION,
            )
            logger.info(
                "[task:%s] resume_merge_with_guidance: guidance insufficient, re-asked",
                task.id,
            )

        else:
            metadata["merge_status"] = "error" if not merge_result.conflict else "conflict"
            if merge_result.error:
                metadata["merge_error"] = merge_result.error
            task.metadata = metadata
            task.save(update_fields=["metadata"])
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="system@taskit",
                author_label="system",
                content=f"I tried your resolution but the merge still failed: {merge_result.error or 'unknown error'}. Tell me what to try next, or fix the files in the worktree and reply \"re-merge\".",
                comment_type=CommentType.STATUS_UPDATE,
            )
    except Exception as exc:
        logger.exception("resume_merge_with_guidance failed for task %s", task.id)
        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="system@taskit",
            author_label="system",
            content=f"Guided merge retry raised an error: {exc}",
            comment_type=CommentType.STATUS_UPDATE,
        )


def _advance_task_to_testing(task):
    """Transition task REVIEW → TESTING and record history.

    Called by merge_task_on_reflection after a successful merge (or when
    no merge is needed).  Idempotent — skips if task is no longer REVIEW.

    TESTING means "merged, as good as done".  The task lands here and
    STAYS here: no gate runs, no celery task dispatches, no auto-promote.
    DONE is the human's optional housekeeping flip after a manual
    spot-check — the system never sets it.  (W5, task #205: the
    promote-check gate was retired because every W4 hold it produced was
    a phantom gap that parked green work on a human for hours.)
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

    # Memory: stamp estimate vs actual on auto-promotion (REVIEW → TESTING)
    # and append a one-line trail to the dispatch twins comment. Best-effort:
    # a metadata failure must never block the transition.
    try:
        from .estimation import stamp_actual_and_trail
        stamp_actual_and_trail(task, transition="auto_promote_testing")
    except Exception:
        logger.warning(
            "[task:%s] estimate-vs-actual stamp failed on auto-promote",
            task.id, exc_info=True,
        )
    maybe_finalize_schedule_run(task, TaskStatus.TESTING)
    logger.info(
        "Advanced task %s from REVIEW → TESTING (post-merge) — stays in TESTING",
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
    2. Infra-truncation signatures (odin harness truncation language —
       these mark an infrastructure-level failure, not a real work
       failure, and the auto-redispatch hook relies on the surfaced
       reason containing one of these substrings)
    3. Python exception lines (last line starting with an exception class name)
    4. Empty string (caller falls back to generic message)
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

    # 2. Infra-truncation signatures — these are the canonical phrases the
    # odin harness emits when the provider killed the agent mid-flight
    # (truncation, network drop, output-cap hit, silent termination). They
    # are actionable because they tell the operator the agent itself never
    # reported a failure — the system died underneath it. The auto-redispatch
    # hook in _is_infra_failure / _maybe_auto_redispatch_infra_failure
    # matches on these substrings, so the fallback MUST surface them.
    # Also includes "backend unreachable" (task #339): odin exec exits with
    # this message when the TaskIt backend was transiently unreachable during
    # task ID resolution — a transport-class infra failure, not an agent crash.
    infra_signatures = (
        "did not emit an odin-status",
        "did not emit odin-status",
        "truncated mid-generation",
        "response truncated",
        "terminated silently",
        "produced no output",
        "backend unreachable",
        "could not reach task backend",
    )
    for line in reversed(lines):
        low = line.lower()
        if any(sig in low for sig in infra_signatures):
            return line

    # 3. Python exception — last line matching ExceptionClass: message
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
    working_dir = (
        board.working_dir if board and board.working_dir else None
    ) or resolve_working_dir(task)
    if not working_dir:
        return None
    try:
        from odin.worktree import WorktreeManager
        project_root = Path(working_dir)
        return WorktreeManager(project_root)
    except ImportError:
        logger.warning("odin.worktree not available — cannot manage worktrees")
        return None


def _resolve_cleanup_project_root(task) -> Path | None:
    """Best-effort git project root for direct ``git worktree remove`` fallback.

    The WorktreeManager path requires both a board.working_dir and a task.spec.
    When either is missing we still need *some* git root to invoke
    ``git worktree remove --force <path>``.  Prefer the board.working_dir,
    then the metadata working_dir / worktree_path's parent, then any
    parent that contains a ``.git`` directory.
    """
    board = getattr(task, "board", None)
    candidates: list[Path] = []
    if board and board.working_dir:
        candidates.append(Path(board.working_dir))
    metadata = task.metadata or {}
    for key in ("working_dir", "worktree_path"):
        v = metadata.get(key)
        if v:
            candidates.append(Path(v))
    for c in candidates:
        try:
            if (c / ".git").exists() or (c.parent / ".git").exists():
                return c if (c / ".git").exists() else c.parent
        except OSError:
            continue
    return None


def _is_git_lock_error(exc: Exception) -> bool:
    """Transient git lock contention (parallel first-fork of a fresh spec
    branch — task 285 failed on this and succeeded on immediate retry)."""
    msg = str(exc).lower()
    return any(p in msg for p in ("could not lock", "unable to get a lock", "lock file", "index.lock"))


def _create_task_worktree_with_retry(task, max_attempts=3, backoff_ms=150):
    """Retry worktree creation on transient git lock errors with backoff.

    Non-transient errors fail fast. Returns the path or raises the last
    error so the caller's existing failure handling still applies.
    """
    import time as _time
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _create_task_worktree(task)
        except Exception as e:
            last = e
            if not _is_git_lock_error(e) or attempt == max_attempts:
                raise
            logger.warning(
                "[task:%s] worktree creation hit git lock (attempt %d/%d): %s — retrying",
                task.id, attempt, max_attempts, e,
            )
            _time.sleep(backoff_ms / 1000.0 * attempt)
    raise last


def _create_task_worktree(task):
    """Create a worktree for a task. Returns the worktree path or None.

    On retry after a failed run, a stale worktree from a dead run (no
    RUNNING TaskRun) is removed before creating a fresh one.  A worktree
    owned by a RUNNING run is never removed — that guard prevents
    clobbering a live execution's working directory (task 236).
    """
    wt = _get_worktree_manager(task)
    if not wt:
        return None
    spec_id = task.spec.odin_id
    worktree_path = wt.get_worktree_path(spec_id, str(task.id))

    if worktree_path.exists():
        live_run = task_runs.current_running_run(task)
        if live_run is not None:
            logger.warning(
                "[task:%s] Worktree %s exists and run %s is still RUNNING "
                "— refusing to remove. Skipping dispatch.",
                task.id, worktree_path, live_run.run_token[:8],
            )
            return None
        logger.info(
            "[task:%s] Removing stale worktree %s (no live TaskRun)",
            task.id, worktree_path,
        )
        wt.remove_task_worktree(spec_id, str(task.id))

    return wt.create_task_worktree(spec_id, str(task.id))


def _make_resolution_snapshot(merge_result):
    """Build a MergeResolution-like object from a MergeResult for comment formatting."""
    from odin.merge_agent import MergeResolution
    return MergeResolution(
        resolved=True,
        mechanical_files=list(getattr(merge_result, "resolved_files", []) or []),
        resolution_method="checkout --ours (spec side) for generated agent configs",
        rationale=getattr(merge_result, "resolution_rationale", None),
    )


def _merge_task_branch(task, resolution_guidance=""):
    """Merge a task's branch into its spec branch. Returns MergeResult.

    When the merge agent is enabled in project config, mechanical
    conflicts are auto-resolved.  See ``.odin/config.yaml`` →
    ``merge_agent``.

    ``resolution_guidance`` is a human's free-text reply to a prior
    ambiguous-conflict question (see ``resume_merge_with_guidance``).
    When set, resolution is attempted regardless of the config toggle —
    a human explicitly asking to interpret their guidance overrides the
    default auto-resolution setting.
    """
    wt = _get_worktree_manager(task)
    if not wt:
        try:
            from odin.worktree import MergeResult
        except ImportError:
            # Fallback: define a simple result struct when odin is not available
            class MergeResult:
                def __init__(
                    self, success, error=None, conflict=False, noop=False,
                    diff_stat=None, conflicting_files=None,
                ):
                    self.success = success
                    self.error = error
                    self.conflict = conflict
                    self.noop = noop
                    self.diff_stat = diff_stat
                    self.conflicting_files = list(conflicting_files or [])
        return MergeResult(success=False, error="WorktreeManager not available")
    spec_id = task.spec.odin_id
    attempt_resolution = _merge_agent_enabled(task) or bool(resolution_guidance)
    return wt.merge_task_into_spec(
        spec_id, str(task.id), task.title,
        attempt_resolution=attempt_resolution,
        resolution_guidance=resolution_guidance,
    )


def _merge_agent_enabled(task):
    """Check if the merge agent is enabled in project config.

    Looks for ``merge_agent.enabled`` in ``.odin/config.yaml`` relative
    to the board's working directory.  Returns True by default (Default
    First) when the config is missing or odin is not importable.
    """
    try:
        from odin.config import load_config
        board = getattr(task, "board", None)
        working_dir = (
            board.working_dir if board and board.working_dir else None
        ) or resolve_working_dir(task)
        if working_dir:
            cfg = load_config(Path(working_dir) / ".odin" / "config.yaml")
            ma = getattr(cfg, "merge_agent", None)
            if ma is not None:
                return ma.enabled
        return True
    except Exception:
        logger.debug("merge_agent config check failed — defaulting to enabled", exc_info=True)
        return True


def _cleanup_task_worktree(task):
    """Remove a task's worktree and delete its branch.

    Branch deletion is only safe once the branch's commits already live on
    the spec branch — deleting it before then would permanently discard
    unmerged work.  Task #244's reversibility gate
    (``odin.reversibility.classify_worktree_cleanup``) is the single source
    of truth for that call: ``merged``/``noop`` proceeds autonomously
    (reversible — the same content survives on the spec branch); anything
    else parks with ``metadata.hard_action_status = "needs_human"`` and
    posts a QUESTION, reusing the merge flow's park → reply → resume
    mechanism (``resume_hard_action_with_reply`` below,
    ``tasks.signals.resume_hard_action_on_human_reply``).
    """
    from odin.reversibility import HARD, classify_worktree_cleanup

    branch = (task.metadata or {}).get("branch") or (
        f"task/{task.spec.odin_id}/{task.id}" if task.spec_id else ""
    )
    merge_status = (task.metadata or {}).get("merge_status")

    if classify_worktree_cleanup(merge_status) == HARD:
        metadata = dict(task.metadata or {})
        metadata["hard_action_status"] = "needs_human"
        metadata["hard_action"] = {
            "key": "delete_branch_with_unmerged_work",
            "branch": branch,
        }
        task.metadata = metadata
        task.save(update_fields=["metadata"])

        TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email="merge-agent@odin",
            author_label="merge-agent",
            content=(
                "Hard action parked — needs a human decision:\n\n"
                f"What's irreversible: deleting branch `{branch}` now would "
                f"permanently discard its unmerged work (merge_status="
                f"`{merge_status or 'none'}`) — once the branch is gone, "
                "that work can't be recovered.\n\n"
                "Question — reply with your choice: delete the branch "
                'anyway (reply "delete" or "proceed"), or keep it for now '
                '(reply "keep" or "cancel")?'
            ),
            comment_type=CommentType.QUESTION,
        )
        logger.info(
            "[task:%s] _cleanup_task_worktree: parked hard action "
            "(unmerged branch %s) — needs_human",
            task.id, branch,
        )
        return

    wt = _get_worktree_manager(task)
    if wt:
        spec_id = task.spec.odin_id
        wt.cleanup_task_worktree(spec_id, str(task.id))


@shared_task(name="tasks.dag_executor.resume_hard_action_with_reply")
def resume_hard_action_with_reply(task_id, comment_id):
    """Re-attempt (or cancel) a parked hard action using a human's reply.

    Mirrors ``resume_merge_with_guidance``'s park → reply → resume pattern
    for the reversibility gate (task #244): a hard-classed action parks
    with ``metadata.hard_action_status = "needs_human"`` instead of
    proceeding autonomously (see ``_cleanup_task_worktree``). A reply
    containing "delete"/"proceed"/"confirm"/"yes" carries the action out;
    anything else leaves it parked and confirms nothing was touched.

    Guarded against races the same way the merge resume is: re-checks
    ``hard_action_status`` is still ``needs_human`` before doing anything.
    """
    try:
        task = Task.objects.select_related("spec", "board").get(id=task_id)
    except Task.DoesNotExist:
        logger.error("resume_hard_action_with_reply: Task %s not found", task_id)
        return

    if (task.metadata or {}).get("hard_action_status") != "needs_human":
        logger.info(
            "[task:%s] resume_hard_action_with_reply: no longer parked — "
            "skipping (already resolved elsewhere)",
            task.id,
        )
        return

    try:
        guidance_comment = TaskComment.objects.get(id=comment_id, task=task)
    except TaskComment.DoesNotExist:
        logger.error(
            "[task:%s] resume_hard_action_with_reply: guidance comment %s not found",
            task.id, comment_id,
        )
        return

    reply = (guidance_comment.content or "").strip().lower()
    hard_action = (task.metadata or {}).get("hard_action") or {}
    action_key = hard_action.get("key")
    branch = hard_action.get("branch", "")
    proceed = any(word in reply for word in ("delete", "proceed", "confirm", "yes"))

    metadata = dict(task.metadata or {})
    metadata.pop("hard_action_status", None)
    metadata.pop("hard_action", None)
    task.metadata = metadata
    task.save(update_fields=["metadata"])

    if proceed and action_key == "delete_branch_with_unmerged_work":
        wt = _get_worktree_manager(task)
        if wt and task.spec_id is not None:
            wt.cleanup_task_worktree(task.spec.odin_id, str(task.id))
        comment_text = (
            f'Applied your guidance ("{guidance_comment.content.strip()}") — '
            f"deleted branch `{branch}` and its worktree."
        )
    else:
        comment_text = (
            f'Your reply ("{guidance_comment.content.strip()}") kept branch '
            f"`{branch}` in place — nothing was deleted."
        )

    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="merge-agent@odin",
        author_label="merge-agent",
        content=comment_text,
        comment_type=CommentType.STATUS_UPDATE,
    )
    logger.info(
        "[task:%s] resume_hard_action_with_reply: %s (reply=%r)",
        task.id, "proceeded" if proceed else "kept parked", reply,
    )


def _remove_task_worktree(task):
    """Remove a task's worktree but preserve its branch for recovery.

    Two paths:
      - normal: ask the WorktreeManager (which knows the spec/task-id
        layout) to remove the worktree.  The branch survives.
      - fallback: when the WorktreeManager isn't available (no board
        working_dir) OR the task has no spec (so spec_id is unknown),
        remove the worktree at the path recorded in metadata directly
        via git.  Still preserves the branch — we only drop the
        worktree directory, never delete the branch itself.
    """
    wt = _get_worktree_manager(task)
    metadata = task.metadata or {}
    recorded_path = metadata.get("worktree_path")
    if wt and task.spec_id is not None:
        spec_id = task.spec.odin_id
        wt.remove_task_worktree(spec_id, str(task.id))
        return
    if not recorded_path:
        return
    p = Path(recorded_path)
    if not p.exists():
        return
    project_root = _resolve_cleanup_project_root(task)
    if project_root is None:
        # Without a known git root we can't safely run git worktree
        # remove.  Leave the directory in place; the operator can
        # clean it up.  The comment posted by the caller names the
        # leftover path so it's discoverable.
        raise RuntimeError(
            f"No git project root available to remove worktree at {recorded_path}"
        )
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(p)],
        cwd=str(project_root),
        check=True,
        capture_output=True,
        text=True,
    )


def _cleanup_done_task_worktree(task):
    """Reclaim a DONE task's worktree disk and clear its path metadata.

    Called when a task transitions to DONE (spec finalization). The task
    branch is preserved — `remove_task_worktree` only drops the worktree, so
    committed work survives on the branch. REVIEW/TESTING/FAILED worktrees
    are intentionally not touched (rework/inspection needs them).

    Removal failures must never block the transition: on error we log a
    warning and post a comment naming the leftover path, leaving
    `worktree_path` in metadata so the operator can find and clean it up.
    """
    metadata = dict(task.metadata or {})
    worktree_path = metadata.get("worktree_path")
    if not worktree_path:
        return
    try:
        _remove_task_worktree(task)
    except Exception:
        logger.warning(
            "Task %s: worktree removal failed on DONE (%s)",
            task.id, worktree_path, exc_info=True,
        )
        try:
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="system@taskit",
                author_label="system",
                content=(
                    f"Could not remove worktree on finalization: {worktree_path}. "
                    "Branch work is preserved; clean up the worktree manually if needed."
                ),
                comment_type=CommentType.STATUS_UPDATE,
            )
        except Exception:
            logger.warning(
                "Task %s: failed to post worktree-cleanup comment", task.id, exc_info=True,
            )
        return

    metadata.pop("worktree_path", None)
    metadata.pop("working_dir", None)
    Task.objects.filter(id=task.id).update(metadata=metadata)
    task.metadata = metadata
    logger.info("Task %s: removed worktree on DONE (%s)", task.id, worktree_path)


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
        # Memory: stamp estimate vs actual on spec-finalize DONE.
        try:
            from .estimation import stamp_actual_and_trail
            stamp_actual_and_trail(task, transition="spec_finalize_done")
        except Exception:
            logger.warning(
                "[task:%s] estimate-vs-actual stamp failed on spec finalize",
                task.id, exc_info=True,
            )
        maybe_finalize_schedule_run(task, TaskStatus.DONE)
        logger.info("Task %s: TESTING → DONE (finalize, pr=%s)", task.id, pr_url)
        # Reclaim the worktree now that the task is finalized. The branch is
        # preserved by remove_task_worktree; failures never block the DONE
        # transition (handled inside the helper).
        _cleanup_done_task_worktree(task)


def _classify_failure(exit_code: int, failure_stage: str, excerpt: str) -> tuple[str, str]:
    """Classify fallback failures and choose the best user-facing reason."""
    if failure_stage in ("cancelled", "run_token_mismatch"):
        return ("cancelled", "Execution stopped by user request")
    if failure_stage == "timeout":
        return ("timeout", "Task execution timed out")
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


# ── Post-reflection merge watchdog (W3.22 / task #171) ─────────────
# The post-PASS dispatch in ``views._merge_task_on_reflection_pass`` has
# three silent-skip holes (status moved past REVIEW, merge_status already
# merged, Celery broker down). When any of those fires the merge never
# happens and the operator has no signal — 9 wave-3 tasks stalled this way.
# The watchdog scans for tasks whose latest reflection PASSed but whose
# merge dispatch hasn't completed, retries the dispatch once, and on the
# second observation marks ``merge_status = "needs_human"`` so the operator
# sees the stall on the board.
#
# Decision order (W4 — task #196, false-alarm fix):
#   1. terminal / no-PASS → skip
#   2. within the idle window → skip (merge may still be in-flight)
#   3. git reality: branch already merged → reconcile to merged, skip
#   4. window expired + not merged + attempts exhausted → escalate
#   5. window expired + not merged + attempts remain → retry dispatch
# The window must gate *every* non-terminal path, not just the retry path —
# the original code escalated the moment ``attempts >= max_attempts`` without
# consulting the window, so a merge dispatched one second earlier was
# escalated on the next beat tick (three false alarms in one day).
#
# Tunables (settings):
#   MERGE_WATCHDOG_MINUTES_IDLE   — minutes since last dispatch before the
#                                   watchdog acts (default 30). Must exceed
#                                   the observed ~20 min merge latency under
#                                   worker-pool starvation or merges still
#                                   in flight get flagged.
#   MERGE_WATCHDOG_MAX_ATTEMPTS   — auto-retry count before escalation
#                                   (default 1; the second observation
#                                   triggers needs_human).
#   MERGE_QUEUE_NAME              — optional dedicated celery queue for
#                                   ``merge_task_on_reflection`` so long
#                                   executions cannot starve merges. Empty
#                                   (default) = default queue, unchanged.
MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT = 30
MERGE_WATCHDOG_MAX_ATTEMPTS_DEFAULT = 1
MERGE_TERMINAL_STATUSES = {"merged", "noop", "needs_human", "conflict", "error"}


def _latest_pass_reflection(task):
    """Return the most recent COMPLETED reflection report with verdict=PASS.

    Used by the watchdog to confirm the merge is *expected* to fire — a
    NEEDS_WORK/FAIL verdict should not trigger retry.
    """
    return (
        ReflectionReport.objects.filter(
            task=task,
            status=ReflectionStatus.COMPLETED,
            verdict__iexact="PASS",
        )
        .order_by("-completed_at", "-id")
        .first()
    )


def _parse_merge_dispatched_at(stamp_iso):
    """Parse a ``merge_dispatched_at`` ISO stamp into an aware datetime.

    Returns None for a missing/unparseable stamp — used for both the
    watchdog's idle-window check and MergeAttempt's dispatch-lag capture
    (task #209), so a bad stamp degrades to 'no lag recorded' rather than
    raising. Naive stamps are assumed UTC so they stay comparable to
    aware datetimes.
    """
    if not stamp_iso:
        return None
    try:
        stamp = datetime.fromisoformat(stamp_iso)
    except (TypeError, ValueError):
        return None
    if getattr(stamp, "tzinfo", None) is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _merge_dispatch_within_window(stamp_iso, cutoff):
    """True if the last-dispatch timestamp is still inside the idle window.

    A bad/missing stamp is treated as 'outside the window' so the watchdog
    falls through to act (retry or escalate) rather than silently waiting
    forever on an unparseable value.
    """
    stamp = _parse_merge_dispatched_at(stamp_iso)
    return stamp is not None and stamp >= cutoff


def _is_task_branch_merged(task):
    """Return True if the task branch is already an ancestor of the spec branch.

    Git-reality check used by the watchdog before escalating: a merge whose
    commit landed but whose ``merge_status`` stamp never did (e.g. the celery
    worker died after the merge commit) must be reconciled, not flagged.

    Fails open: returns False (not merged) when no WorktreeManager is
    available, the branches are missing, or git errors — a false 'merged'
    would silently drop a real merge, while a false 'not merged' just keeps
    waiting, which is the safe direction.
    """
    if not task.spec_id:
        return False
    wt = _get_worktree_manager(task)
    if wt is None:
        return False
    try:
        return wt.is_task_merged(task.spec.odin_id, str(task.id))
    except Exception:
        logger.warning(
            "[task:%s] merge watchdog: git-reality check failed — treating "
            "merge as not-yet-merged",
            task.id, exc_info=True,
        )
        return False


def _dispatch_merge_task(task_id):
    """Dispatch the post-reflection merge, honoring an optional dedicated queue.

    When ``settings.MERGE_QUEUE_NAME`` is set, route via ``apply_async`` to
    that queue so a dedicated worker can drain merges without waiting behind
    long-running executions. Empty/unset (default) uses plain ``.delay()`` on
    the default queue — identical to the pre-existing behavior.
    """
    queue = getattr(settings, "MERGE_QUEUE_NAME", None)
    if queue:
        merge_task_on_reflection.apply_async(args=(task_id,), queue=queue)
    else:
        merge_task_on_reflection.delay(task_id)


def scan_pending_merge_dispatches(minutes_idle=None, max_attempts=None):
    """Find REVIEW tasks with stalled merge dispatches and retry/escalate.

    Returns a dict ``{"dispatched": [...], "escalated": [...], "skipped": [...],
    "merged": [...]}`` with the affected task IDs. Idempotent and safe to call
    from a Celery Beat schedule (every 60s, for example).

    Decision order (see W4 task #196 — false-alarm fix):
      1. terminal merge_status / no PASS reflection → skip
      2. last dispatch within the idle window → skip (merge may be in-flight)
      3. git reality: task branch already an ancestor of spec branch →
         reconcile ``merge_status = merged`` and skip (the commit landed but
         the metadata stamp never did)
      4. window expired + not merged + attempts exhausted → escalate to
         ``needs_human`` with a blocking QUESTION comment
      5. window expired + not merged + attempts remain → re-dispatch and
         bump the counter

    The idle window gates *every* non-terminal path. The original code
    escalated as soon as ``attempts >= max_attempts`` regardless of how
    recently the merge was dispatched, so a merge that left the queue one
    second earlier was escalated on the next beat tick.

    The function deliberately never raises — every failure path logs a
    warning and moves on so a single bad task can't crash the scan.
    """
    minutes_idle = minutes_idle if minutes_idle is not None else MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT
    max_attempts = max_attempts if max_attempts is not None else MERGE_WATCHDOG_MAX_ATTEMPTS_DEFAULT
    now = timezone.now()
    cutoff = now - timedelta(minutes=minutes_idle)

    result = {"dispatched": [], "escalated": [], "skipped": [], "merged": []}
    candidates = Task.objects.filter(status=TaskStatus.REVIEW).exclude(metadata__merge_status__in=MERGE_TERMINAL_STATUSES)
    for task in candidates:
        try:
            meta = dict(task.metadata or {})
            merge_status = meta.get("merge_status")
            if merge_status in MERGE_TERMINAL_STATUSES:
                result["skipped"].append(task.id)
                continue

            if _latest_pass_reflection(task) is None:
                # No PASS verdict → no merge expected; nothing to do.
                result["skipped"].append(task.id)
                continue

            attempts = int(meta.get("merge_dispatch_attempts") or 0)
            last_dispatched_at = meta.get("merge_dispatched_at")

            # 1. Idle window gates EVERY non-terminal path. A merge dispatched
            #    moments ago (or one still waiting on a starved worker) must
            #    not be retried or escalated just because attempts rolled over.
            if _merge_dispatch_within_window(last_dispatched_at, cutoff):
                result["skipped"].append(task.id)
                continue

            # 2. Git reality: maybe the merge already landed and only the
            #    metadata stamp is missing (worker died after the commit).
            #    Reconcile instead of crying wolf.
            if _is_task_branch_merged(task):
                meta["merge_status"] = "merged"
                meta["merge_reconciled_by"] = "watchdog"
                meta["merge_reconciled_at"] = now.isoformat()
                Task.objects.filter(id=task.id).update(metadata=meta)
                task.metadata = meta
                logger.info(
                    "[task:%s] merge watchdog: task branch already merged into "
                    "spec branch — reconciling merge_status=merged (no escalation)",
                    task.id,
                )
                TaskComment.objects.create(
                    task=task,
                    schedule_run=task.current_schedule_run,
                    author_email="system@taskit",
                    author_label="system",
                    content=(
                        "Watchdog reconciliation: the task branch is already "
                        "merged into the spec branch, so `merge_status` was "
                        "stale. Marked merged — no action needed."
                    ),
                    comment_type=CommentType.STATUS_UPDATE,
                )
                result["merged"].append(task.id)
                continue

            # 3. Window expired, not merged, attempts exhausted → escalate.
            if last_dispatched_at and attempts >= max_attempts:
                meta["merge_status"] = "needs_human"
                meta["merge_watchdog_escalated_at"] = now.isoformat()
                meta["merge_watchdog_attempts"] = attempts
                Task.objects.filter(id=task.id).update(metadata=meta)
                task.metadata = meta
                logger.warning(
                    "[task:%s] merge watchdog: escalation after %d attempt(s); "
                    "merge_status=needs_human",
                    task.id, attempts,
                )
                TaskComment.objects.create(
                    task=task,
                    schedule_run=task.current_schedule_run,
                    author_email="system@taskit",
                    author_label="system",
                    content=(
                        f"**Merge dispatch stalled — needs human attention**\n\n"
                        f"Reflection verdict was PASS but the post-PASS merge "
                        f"has not completed after {attempts} dispatch attempt(s) "
                        f"and {minutes_idle} minutes. Please inspect the task "
                        f"branch and either merge it manually "
                        f"(`odin merge {task.spec.odin_id}`) or investigate why "
                        f"`merge_task_on_reflection` is not running.\n\n"
                        f"Task branch: `{meta.get('branch', '?')}`\n"
                        f"Spec branch: `spec/{task.spec.odin_id}`"
                    ),
                    comment_type=CommentType.QUESTION,
                )
                result["escalated"].append(task.id)
                continue

            # 4. First-observation OR post-idle-window retry path.
            meta["merge_dispatch_attempts"] = attempts + 1
            meta["merge_dispatched_at"] = now.isoformat()
            meta["merge_dispatch_source"] = "watchdog"
            Task.objects.filter(id=task.id).update(metadata=meta)
            task.metadata = meta
            logger.warning(
                "[task:%s] merge watchdog: retrying merge dispatch "
                "(attempt %d, last_dispatched_at=%s)",
                task.id, attempts + 1, last_dispatched_at,
            )
            try:
                _dispatch_merge_task(task.id)
                result["dispatched"].append(task.id)
            except Exception:
                logger.exception(
                    "[task:%s] merge watchdog: dispatch failed at broker",
                    task.id,
                )
                result["skipped"].append(task.id)
        except Exception:
            logger.exception(
                "[task:%s] merge watchdog: scan iteration failed",
                task.id,
            )
            result["skipped"].append(task.id)

    if result["dispatched"] or result["escalated"] or result["merged"]:
        logger.info(
            "merge watchdog scan: dispatched=%d escalated=%d merged=%d skipped=%d",
            len(result["dispatched"]), len(result["escalated"]),
            len(result["merged"]), len(result["skipped"]),
        )
    return result


# ── needs_human reconciliation (task #193) ──────────────────────────
# The merge agent writes ``merge_status = "needs_human"`` once (ambiguous
# conflict, watchdog escalation, broker failure) and nothing reconciles
# that flag against git reality afterwards. When the operator resolves
# the conflict by hand the merge commit lands on the spec branch but the
# flag stays — the board shows phantom pending merges forever.
#
# This pass re-checks each flagged task: if its task branch is now an
# ancestor of the spec branch, the merge happened (by whatever means) and
# the flag is stale. It rides the existing periodic seam
# (``scan_pending_merges``) rather than introducing a new cron.


def reconcile_needs_human_merges():
    """Clear stale ``merge_status='needs_human'`` flags against git reality.

    For each task flagged needs_human, if its task branch
    (``task/<spec>/<id>``) is now an ancestor of the spec branch
    (``spec/<spec_id>``) — checked via ``git merge-base --is-ancestor`` —
    the merge landed (by manual resolution or otherwise) and the flag is
    cleared: ``merge_status`` → ``merged``,
    ``merge_resolution = "reconciled: branch merged on spec branch"``,
    stale ``merge_error`` / ``merge_ambiguous_files`` are dropped, and
    exactly one STATUS_UPDATE comment is posted.

    Idempotent: once reconciled the task is no longer needs_human, so
    subsequent scans skip it (no duplicate comments). When the
    WorktreeManager can't be resolved (odin unavailable / no board
    working dir) or the branch is genuinely unmerged, the task is left
    flagged and skipped.

    Returns ``{"reconciled": [...], "skipped": [...]}`` with task IDs.
    Never raises — a single bad task logs and moves on.
    """
    result = {"reconciled": [], "skipped": []}
    candidates = Task.objects.filter(metadata__merge_status="needs_human")
    for task in candidates:
        try:
            meta = dict(task.metadata or {})
            branch = meta.get("branch")
            if not branch:
                result["skipped"].append(task.id)
                continue

            spec_odin_id = getattr(task.spec, "odin_id", None) if task.spec else None
            if not spec_odin_id:
                result["skipped"].append(task.id)
                continue
            spec_branch = f"spec/{spec_odin_id}"

            wt = _get_worktree_manager(task)
            if wt is None:
                # No git context resolvable (odin unavailable / no board
                # working dir) — can't verify against git reality. Leave
                # the flag so a human still sees the task.
                result["skipped"].append(task.id)
                continue

            if not wt.is_ancestor(branch, spec_branch):
                # Branch genuinely not merged yet — flag still accurate.
                result["skipped"].append(task.id)
                continue

            # Git reality says the merge landed — clear the flag.
            meta["merge_status"] = "merged"
            meta["merge_resolution"] = "reconciled: branch merged on spec branch"
            meta.pop("merge_error", None)
            meta.pop("merge_ambiguous_files", None)
            Task.objects.filter(id=task.id).update(metadata=meta)
            task.metadata = meta
            TaskComment.objects.create(
                task=task,
                schedule_run=task.current_schedule_run,
                author_email="system@taskit",
                author_label="system",
                content=(
                    f"**Merge reconciled** — the task branch `{branch}` is "
                    f"now an ancestor of `{spec_branch}`, so the merge has "
                    f"landed. The earlier needs_human flag has been cleared "
                    f"(merge_status → merged)."
                ),
                comment_type=CommentType.STATUS_UPDATE,
            )
            logger.info(
                "[task:%s] merge reconciliation: branch %s is an ancestor of "
                "%s — cleared needs_human → merged",
                task.id, branch, spec_branch,
            )
            result["reconciled"].append(task.id)
        except Exception:
            logger.exception(
                "[task:%s] merge reconciliation: iteration failed",
                task.id,
            )
            result["skipped"].append(task.id)

    if result["reconciled"]:
        logger.info(
            "merge reconciliation scan: reconciled=%d skipped=%d",
            len(result["reconciled"]), len(result["skipped"]),
        )
    return result


@shared_task(name="tasks.dag_executor.scan_pending_merges")
def scan_pending_merges():
    """Celery Beat entry point — runs reconciliation + the watchdog.

    Intended cadence: every 60 seconds. Two complementary passes share
    this one schedule entry (no separate cron):
      1. ``reconcile_needs_human_merges`` — clears needs_human flags whose
         task branch has since been merged into the spec branch by hand.
      2. ``scan_pending_merge_dispatches`` — retries/escalates stalled
         pending merge dispatches.

    Tunables for the watchdog come from settings so an operator can crank
    up the idle window on a noisy system without code changes.
    """
    reconcile_needs_human_merges()
    minutes_idle = getattr(settings, "MERGE_WATCHDOG_MINUTES_IDLE", MERGE_WATCHDOG_MINUTES_IDLE_DEFAULT)
    max_attempts = getattr(settings, "MERGE_WATCHDOG_MAX_ATTEMPTS", MERGE_WATCHDOG_MAX_ATTEMPTS_DEFAULT)
    return scan_pending_merge_dispatches(
        minutes_idle=minutes_idle, max_attempts=max_attempts,
    )


# ── Reflection watchdog (task #221) ─────────────────────────────────
# Reflections are fire-and-forget: a task entering REVIEW dispatches one
# reflection, and if that moment is lost (host slept mid-transition, worker
# died, broker dropped the message) the task sits in REVIEW forever with no
# reviewer. Two tasks waited 6 hours in production. Merges had the same
# disease and got ``scan_pending_merges`` (task #196); this is the mirror
# for reflections — same module, same style, one pattern.
#
# Unlike merges, reflections have no TaskRun lease/heartbeat: a dead worker
# leaves the report PENDING/RUNNING forever. So the watchdog reaps stale
# active reports before re-dispatching (the merge watchdog never needs this
# because a merge either lands or fails fast).
#
# Decision order:
#   1. reflection disabled → skip
#   2. active (PENDING/RUNNING) report within the idle window → skip
#   3. active report stale (past window) → reap it (FAILED), fall through
#   4. idle-window gate on the last watchdog dispatch → skip (backoff)
#   5. attempts exhausted → escalate (QUESTION comment + reflect log tail)
#   6. attempts remain → dispatch a fresh reflection (size-scaled selection)
#
# The idle window is the backoff: each retry waits one window before the
# next attempt. It must exceed ``DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS``
# (default 1800s = 30min) so a legitimately-running reflection is never
# reaped as stale.
#
# Tunables (settings):
#   REFLECTION_WATCHDOG_MINUTES_IDLE   — minutes before a missing/stalled
#                                        reflection is acted on (default 35;
#                                        must exceed the 30min reflection
#                                        subprocess timeout).
#   REFLECTION_WATCHDOG_MAX_ATTEMPTS   — watchdog dispatches before
#                                        escalation (default 2).
#   REFLECTION_QUEUE_NAME              — optional dedicated celery queue so
#                                        reviews never wait behind the
#                                        execution pool. Empty = default queue.
REFLECTION_WATCHDOG_MINUTES_IDLE_DEFAULT = 35
REFLECTION_WATCHDOG_MAX_ATTEMPTS_DEFAULT = 2
# After this many consecutive scans where ``select_reviewer_by_context_size``
# returns (None, None), the watchdog posts a QUESTION comment so an operator
# notices a stuck review that no agent can carry (task #246). Default 3
# scans ≈ 3 minutes at the 60s beat cadence — short enough to surface a
# configuration mistake fast, long enough to ride out a transient blip.
REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_DEFAULT = 3


def _reflection_dispatch_within_window(stamp_iso, cutoff):
    """True if the last reflection-dispatch timestamp is inside the idle window.

    Reuses the merge watchdog's ISO-stamp parser — same semantics. A
    missing/unparseable stamp is treated as 'outside the window' so the
    watchdog acts rather than waiting forever on a bad value.
    """
    stamp = _parse_merge_dispatched_at(stamp_iso)
    return stamp is not None and stamp >= cutoff


def _read_reflect_log_tail(task_id, report_id=None):
    """Read the tail of the most recent reflect log for an escalation comment.

    Used only on the escalation path so the operator sees *what* failed. The
    reflect log lives at ``logs/reflect_{task_id}_{report_id}.log``. Falls
    back to any reflect log for the task, then to an empty string.
    """
    log_dir = Path(settings.BASE_DIR) / "logs"
    candidates = []
    if report_id is not None:
        candidates.append(log_dir / f"reflect_{task_id}_{report_id}.log")
    try:
        candidates.extend(
            sorted(log_dir.glob(f"reflect_{task_id}_*.log"), reverse=True)
        )
    except Exception:
        pass
    for log_file in candidates:
        tail = _read_log_tail(log_file)
        if tail:
            return tail
    return ""


def _dispatch_reflection_task(report_id):
    """Dispatch ``execute_reflection``, honoring an optional dedicated queue.

    Mirrors ``_dispatch_merge_task`` exactly: when
    ``settings.REFLECTION_QUEUE_NAME`` is set, route via ``apply_async`` to
    that queue so a dedicated worker can drain reflections without waiting
    behind long-running executions. Empty/unset (default) uses plain
    ``.delay()``.
    """
    queue = getattr(settings, "REFLECTION_QUEUE_NAME", None)
    if queue:
        execute_reflection.apply_async(args=(report_id,), queue=queue)
    else:
        execute_reflection.delay(report_id)


@shared_task(name="tasks.dag_executor.scan_pending_reflections")
def scan_pending_reflections(minutes_idle=None, max_attempts=None):
    """Find REVIEW tasks whose reflection is missing/stalled; retry/escalate.

    Returns ``{"dispatched": [...], "escalated": [...], "reaped": [...],
    "skipped": [...]}`` with the affected task IDs. Idempotent and safe to
    call from Celery Beat (every 60s) — never raises per-task.

    Callable directly with explicit args for tests; Beat calls it with no
    args (settings supply the defaults). Mirrors
    ``scan_pending_merge_dispatches`` in shape and tunable surface.
    """
    minutes_idle = (
        minutes_idle
        if minutes_idle is not None
        else getattr(
            settings,
            "REFLECTION_WATCHDOG_MINUTES_IDLE",
            REFLECTION_WATCHDOG_MINUTES_IDLE_DEFAULT,
        )
    )
    max_attempts = (
        max_attempts
        if max_attempts is not None
        else getattr(
            settings,
            "REFLECTION_WATCHDOG_MAX_ATTEMPTS",
            REFLECTION_WATCHDOG_MAX_ATTEMPTS_DEFAULT,
        )
    )
    now = timezone.now()
    cutoff = now - timedelta(minutes=minutes_idle)

    result = {"dispatched": [], "escalated": [], "reaped": [], "skipped": []}
    candidates = Task.objects.filter(status=TaskStatus.REVIEW).select_related(
        "board", "spec",
    )
    for task in candidates:
        try:
            # 1. Reflection disabled → nothing to do.
            if task.skip_reflection or getattr(task.board, "skip_reflection", False):
                result["skipped"].append(task.id)
                continue

            meta = dict(task.metadata or {})

            # 2/3. Active reflection: in-flight if fresh, reaped if stale.
            active = (
                ReflectionReport.objects.filter(
                    task=task,
                    status__in=(ReflectionStatus.PENDING, ReflectionStatus.RUNNING),
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if active is not None and active.created_at >= cutoff:
                # Still in-flight — leave it alone (no double-dispatch).
                result["skipped"].append(task.id)
                continue

            reaped_report_id = None
            if active is not None:
                # Stale active report: the worker supervising it is gone
                # (reflections have no TaskRun lease/heartbeat). Reap it so
                # the retry creates a clean report instead of stacking.
                reaped_report_id = active.id
                orig_status = active.status
                active.status = ReflectionStatus.FAILED
                active.error_message = (
                    f"Reflection watchdog: report was {orig_status} since "
                    f"{active.created_at.isoformat()} with no completion past "
                    f"the {minutes_idle}min idle window — reaped. The worker "
                    f"supervising this reflection is gone."
                )
                active.completed_at = now
                active.save(update_fields=[
                    "status", "error_message", "completed_at",
                ])
                result["reaped"].append(task.id)
                logger.warning(
                    "[task:%s] reflection watchdog: reaped stale %s report %s "
                    "(past %dmin idle window)",
                    task.id, orig_status, active.id, minutes_idle,
                )

            # 3b. A COMPLETED reflection with a usable verdict from THIS
            #     review cycle means the post-reflection pipeline owns the
            #     task — the merge may be legitimately parked on a human
            #     conflict answer, which keeps the task in REVIEW for as
            #     long as the human takes. Re-reflecting here spawns a
            #     duplicate merge agent on the same conflict (task 266).
            #     Unusable verdicts ("", "ERROR") still fall through to
            #     retry; a PASS from a PREVIOUS cycle (task re-entered
            #     REVIEW after the report) doesn't count.
            latest_completed = (
                ReflectionReport.objects.filter(
                    task=task, status=ReflectionStatus.COMPLETED,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if latest_completed is not None:
                verdict = (latest_completed.verdict or "").strip().upper()
                if verdict and verdict != "ERROR":
                    entered_review_at = (
                        TaskHistory.objects.filter(
                            task=task,
                            field_name="status",
                            new_value=TaskStatus.REVIEW,
                        )
                        .order_by("-changed_at", "-id")
                        .values_list("changed_at", flat=True)
                        .first()
                    )
                    if (
                        entered_review_at is None
                        or latest_completed.created_at >= entered_review_at
                    ):
                        result["skipped"].append(task.id)
                        continue

            # 4. Idle-window gate on the last WATCHDOG dispatch. This is the
            #    backoff: after a reap + re-dispatch the stamp is fresh, so
            #    the next beat waits a full window before acting again. On
            #    the very first observation (no stamp) it falls through.
            last_dispatched_at = meta.get("reflection_dispatched_at")
            if _reflection_dispatch_within_window(last_dispatched_at, cutoff):
                result["skipped"].append(task.id)
                continue

            attempts = int(meta.get("reflection_dispatch_attempts") or 0)

            # 5. Escalation: attempts exhausted.
            if last_dispatched_at and attempts >= max_attempts:
                # Idempotency (task 300 got 40 identical comments): the
                # marker was SET here but never CHECKED, so every beat
                # past the cap posted again. Guard on the marker, and on
                # a durable comment lookup in case metadata was rewritten
                # by a rework cycle.
                if meta.get("reflection_watchdog_escalated_at"):
                    result["skipped"].append(task.id)
                    continue
                if TaskComment.objects.filter(
                    task=task,
                    content__startswith="**Reflection stalled",
                ).exists():
                    meta["reflection_watchdog_escalated_at"] = now.isoformat()
                    Task.objects.filter(id=task.id).update(metadata=meta)
                    result["skipped"].append(task.id)
                    continue
                log_tail = _read_reflect_log_tail(task.id, reaped_report_id)
                meta["reflection_watchdog_escalated_at"] = now.isoformat()
                meta["reflection_watchdog_attempts"] = attempts
                Task.objects.filter(id=task.id).update(metadata=meta)
                task.metadata = meta
                body = [
                    "**Reflection stalled — needs human attention**",
                    "",
                    f"This task has been in REVIEW with no progressing "
                    f"reflection after {attempts} watchdog dispatch attempt(s) "
                    f"and {minutes_idle} minutes idle. Either every reviewer "
                    f"emitted no usable verdict (prose, no fenced JSON) or the "
                    f"reflection worker kept dying mid-run.",
                    "",
                ]
                if log_tail:
                    body.append("Last reflect log tail:")
                    body.append("```")
                    body.append(log_tail)
                    body.append("```")
                TaskComment.objects.create(
                    task=task,
                    schedule_run=task.current_schedule_run,
                    author_email="system@taskit",
                    author_label="system",
                    content="\n".join(body),
                    comment_type=CommentType.QUESTION,
                )
                logger.warning(
                    "[task:%s] reflection watchdog: escalation after %d "
                    "attempt(s); posted QUESTION comment",
                    task.id, attempts,
                )
                result["escalated"].append(task.id)
                continue

            # 6. Dispatch a fresh reflection (first observation OR retry).
            #    Retry uses the size-scaled reviewer selection — the same
            #    function the auto-trigger uses — so a no-verdict reviewer is
            #    retried with a fresh, size-appropriate model.
            from .views import select_reviewer_by_context_size

            reviewer_agent, reviewer_model, selection_reason = (
                select_reviewer_by_context_size(
                    task,
                    board=task.board,
                    # Retry diversity: never re-pick a reviewer whose
                    # completed verdict on this task was unusable — the
                    # deterministic walk otherwise retries the same broken
                    # reviewer forever (task 297).
                    exclude_reviewers={
                        (r.reviewer_agent, r.reviewer_model)
                        for r in ReflectionReport.objects.filter(
                            task=task, status=ReflectionStatus.COMPLETED,
                        ).only("reviewer_agent", "reviewer_model", "verdict")
                        if (r.verdict or "").strip().upper() in ("", "ERROR")
                    },
                )
            )
            if not reviewer_agent or not reviewer_model:
                # 7. No reviewer resolvable. After N consecutive skips we
                #    escalate so an operator notices a stuck review that
                #    no active agent can carry (task #246) — without this
                #    guard the watchdog would skip forever, which is
                #    exactly the pathology the reflection watchdog was
                #    built to prevent. The marker keeps it idempotent.
                no_reviewer_skips_before_escalation = getattr(
                    settings,
                    "REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION",
                    REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_DEFAULT,
                )
                if not meta.get("reflection_watchdog_no_reviewer_escalated_at"):
                    no_reviewer_skips = (
                        int(meta.get("reflection_watchdog_no_reviewer_skips") or 0) + 1
                    )
                    meta["reflection_watchdog_no_reviewer_skips"] = no_reviewer_skips
                    if no_reviewer_skips >= no_reviewer_skips_before_escalation:
                        meta["reflection_watchdog_no_reviewer_escalated_at"] = (
                            now.isoformat()
                        )
                        Task.objects.filter(id=task.id).update(metadata=meta)
                        task.metadata = meta
                        body = [
                            "**Reflection watchdog: no reviewer available — needs human attention**",
                            "",
                            f"This task has been in REVIEW for "
                            f"{no_reviewer_skips} consecutive watchdog scan(s) with no "
                            f"reviewer resolvable. The reviewer-selection logic returned "
                            f"(None, None) every time, which usually means the board's "
                            f"``reflection_model`` (or a strategy bucket) names a model "
                            f"no active agent advertises, or the selection hint table "
                            f"doesn't know the requested model family. Check the board's "
                            f"reflection model override and the agent_models.json seed.",
                            "",
                        ]
                        TaskComment.objects.create(
                            task=task,
                            schedule_run=task.current_schedule_run,
                            author_email="system@taskit",
                            author_label="system",
                            content="\n".join(body),
                            comment_type=CommentType.QUESTION,
                        )
                        try:
                            from .errors import record_reflection_no_reviewer
                            record_reflection_no_reviewer(
                                task=task,
                                symptom=(
                                    "reflection watchdog: no reviewer resolvable "
                                    f"after {no_reviewer_skips} consecutive scan(s)"
                                ),
                                no_reviewer_skips=no_reviewer_skips,
                            )
                        except Exception:
                            logger.exception(
                                "[task:%s] reflection watchdog: failed to record "
                                "no-reviewer ErrorEvent",
                                task.id,
                            )
                        logger.warning(
                            "[task:%s] reflection watchdog: no-reviewer escalation "
                            "after %d skip(s); posted QUESTION comment",
                            task.id, no_reviewer_skips,
                        )
                        result["escalated"].append(task.id)
                        continue
                    Task.objects.filter(id=task.id).update(metadata=meta)
                    task.metadata = meta
                logger.info(
                    "[task:%s] reflection watchdog: no reviewer available — skipping",
                    task.id,
                )
                result["skipped"].append(task.id)
                continue

            report = ReflectionReport.objects.create(
                task=task,
                reviewer_agent=reviewer_agent,
                reviewer_model=reviewer_model,
                requested_by="system@taskit",
                context_selections=[
                    "description", "comments", "execution_result",
                    "dependencies", "metadata",
                ],
                status=ReflectionStatus.PENDING,
                selection_reason=selection_reason,
            )
            meta["reflection_dispatch_attempts"] = attempts + 1
            meta["reflection_dispatched_at"] = now.isoformat()
            meta["reflection_dispatch_source"] = "watchdog"
            Task.objects.filter(id=task.id).update(metadata=meta)
            task.metadata = meta
            logger.warning(
                "[task:%s] reflection watchdog: dispatching reflection "
                "(attempt %d, last_dispatched_at=%s, report=%s)",
                task.id, attempts + 1, last_dispatched_at, report.id,
            )
            try:
                _dispatch_reflection_task(report.id)
                result["dispatched"].append(task.id)
            except Exception:
                logger.exception(
                    "[task:%s] reflection watchdog: dispatch failed at broker",
                    task.id,
                )
                result["skipped"].append(task.id)
        except Exception:
            logger.exception(
                "[task:%s] reflection watchdog: scan iteration failed",
                task.id,
            )
            result["skipped"].append(task.id)

    if any(result[k] for k in ("dispatched", "escalated", "reaped")):
        logger.info(
            "reflection watchdog scan: dispatched=%d escalated=%d reaped=%d skipped=%d",
            len(result["dispatched"]), len(result["escalated"]),
            len(result["reaped"]), len(result["skipped"]),
        )
    return result
