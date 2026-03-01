"""Centralized dependency checking for task execution.

All dependency logic lives here so DAG executor, views, and any future
consumers use the same rules. Dependencies are always evaluated at runtime
against current task status -- never cached.
"""

import logging
from enum import Enum
from typing import List, Optional

from .models import Task, TaskStatus

logger = logging.getLogger(__name__)

# Statuses that count as "agent finished its work" -- downstream can proceed.
# REVIEW is excluded: the task is still under reflection and may loop back
# to IN_PROGRESS via NEEDS_WORK. Only TESTING (reflection passed) and DONE
# unblock dependents.
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}


class DepStatus(str, Enum):
    READY = "ready"       # All deps satisfied (or no deps)
    WAITING = "waiting"   # Deps exist but not all completed yet
    BLOCKED = "blocked"   # At least one dep is FAILED


def check_deps(task: Task) -> DepStatus:
    """Single entry point -- returns the current dependency status.

    Always queries the database for current dep statuses (runtime query,
    never cached). This ensures recovery works: if a human fixes a failed
    upstream task, the dependent automatically unblocks on the next check.
    """
    if not task.depends_on:
        return DepStatus.READY

    dep_ids = task.depends_on
    deps = Task.objects.filter(id__in=dep_ids)

    any_failed = False
    all_completed = True

    for dep in deps:
        if dep.status == TaskStatus.FAILED:
            any_failed = True
        if dep.status not in COMPLETED_STATUSES:
            all_completed = False

    if any_failed:
        return DepStatus.BLOCKED
    if all_completed:
        return DepStatus.READY
    return DepStatus.WAITING


def get_failed_deps(task: Task) -> List[Task]:
    """Return dependency tasks that are in FAILED status."""
    if not task.depends_on:
        return []
    return list(Task.objects.filter(id__in=task.depends_on, status=TaskStatus.FAILED))


def get_unmet_deps(task: Task) -> List[Task]:
    """Return dependency tasks not yet in a completed status (DONE/REVIEW)."""
    if not task.depends_on:
        return []
    deps = Task.objects.filter(id__in=task.depends_on)
    return [d for d in deps if d.status not in COMPLETED_STATUSES]


def get_ready_tasks(queryset, max_count: Optional[int] = None) -> List[Task]:
    """From a queryset of tasks, return those whose deps are all satisfied.

    Skips tasks with failed deps. Respects ordering by created_at.
    If max_count is provided, stops after collecting that many ready tasks.
    """
    candidates = queryset.order_by("created_at")
    ready = []

    for task in candidates:
        if max_count is not None and len(ready) >= max_count:
            break
        status = check_deps(task)
        if status == DepStatus.READY:
            ready.append(task)

    return ready
