"""Centralized dependency checking for odin orchestrator.

Same rules as taskit-backend/tasks/dependencies.py but operates on
odin's Pydantic Task model, using a task_resolver callable for lookups.

Dependencies are always evaluated at runtime -- never cached. This ensures
recovery works: if a human fixes a failed upstream task, the dependent
automatically unblocks on the next check.
"""

import logging
from enum import Enum
from typing import Callable, List, Optional

from odin.taskit.models import Task, TaskStatus

logger = logging.getLogger(__name__)

# Statuses that count as "agent finished its work" -- downstream can proceed.
# Must match taskit-backend/tasks/dependencies.py COMPLETED_STATUSES.
# REVIEW is excluded: task is still under reflection and may loop back to
# IN_PROGRESS via NEEDS_WORK. Only TESTING (reflection passed) and DONE
# unblock dependents.
# TODO: DRY this — single source of truth shared between odin and taskit
#       so definitions can't silently diverge again.
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}


class DepStatus(str, Enum):
    READY = "ready"       # All deps satisfied (or no deps)
    WAITING = "waiting"   # Deps exist but not all completed yet
    BLOCKED = "blocked"   # At least one dep is FAILED


def check_deps(
    task: Task,
    task_resolver: Callable[[str], Optional[Task]],
) -> DepStatus:
    """Single entry point -- returns the current dependency status.

    Uses task_resolver (typically task_manager.get_task) to look up
    dependency tasks by ID. Always queries current state.
    """
    if not task.depends_on:
        return DepStatus.READY

    any_failed = False
    all_completed = True

    for dep_id in task.depends_on:
        dep = task_resolver(dep_id)
        if not dep:
            # Unknown dep -- treat as unmet (waiting)
            all_completed = False
            continue
        if dep.status == TaskStatus.FAILED:
            any_failed = True
        if dep.status not in COMPLETED_STATUSES:
            all_completed = False

    if any_failed:
        return DepStatus.BLOCKED
    if all_completed:
        return DepStatus.READY
    return DepStatus.WAITING


def get_failed_deps(
    task: Task,
    task_resolver: Callable[[str], Optional[Task]],
) -> List[str]:
    """Return IDs of dependencies that are in FAILED status."""
    failed = []
    for dep_id in task.depends_on:
        dep = task_resolver(dep_id)
        if dep and dep.status == TaskStatus.FAILED:
            failed.append(dep_id)
    return failed


def get_unmet_deps(
    task: Task,
    task_resolver: Callable[[str], Optional[Task]],
) -> List[str]:
    """Return IDs of dependencies not yet in a completed status (DONE/TESTING)."""
    unmet = []
    for dep_id in task.depends_on:
        dep = task_resolver(dep_id)
        if not dep or dep.status not in COMPLETED_STATUSES:
            unmet.append(dep_id)
    return unmet


def get_ready_tasks(
    tasks: List[Task],
    task_resolver: Callable[[str], Optional[Task]],
) -> List[Task]:
    """From a list of tasks, return those whose deps are all satisfied.

    Only considers tasks in TODO status (assigned, waiting to execute).
    Skips tasks with failed deps. Preserves input order.
    """
    ready = []
    for task in tasks:
        if task.status != TaskStatus.TODO:
            continue
        status = check_deps(task, task_resolver)
        if status == DepStatus.READY:
            ready.append(task)
    return ready
