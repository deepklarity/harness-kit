"""Taskit-side dependency helpers — thin wrappers over odin.dag.

Adapts the shared DAG algorithms to Django's Task model. The canonical
logic lives in odin.dag; this module only supplies Django-specific adapters.

Coupling rule: taskit-backend MAY import from odin; odin MUST NOT import
from taskit-backend.

Status semantics:
    COMPLETED_STATUSES — statuses that count as "agent finished AND its
    work has merged to the spec branch". Only DONE and TESTING qualify:
    TESTING is the post-merge gate (the dep branch has been folded into
    the spec branch), DONE is the fully landed terminal state.

    REVIEW is intentionally NOT in this set: REVIEW means the agent has
    produced mergeable work, but the branch has not been folded into the
    spec branch yet. A dependent that started while the dep was in
    REVIEW would fork its worktree from the spec branch and build
    against missing upstream code — silently broken, or worse, passing
    against stale state. Dependents stay WAITING until the dep reaches
    TESTING.

    CANCELED is also not in this set: it is terminal-neutral (operator
    parked the work) and treated as "still unmet, not failed" — see
    odin.dag.check_dep_status.
"""

import logging
from typing import List, Optional

from odin.dag import DepStatus, check_dep_status, filter_ready

from .models import Task, TaskStatus

__all__ = [
    "DepStatus",
    "COMPLETED_STATUSES",
    "check_deps",
    "get_blocked_by",
    "get_failed_deps",
    "get_unmet_deps",
    "get_ready_tasks",
]

logger = logging.getLogger(__name__)

# Statuses that count as "agent finished AND merged to spec branch".
# Must stay in sync with odin/src/odin/dependencies.py COMPLETED_STATUSES.
# Both modules use odin.dag for the algorithm; this constant names the
# Django-side TaskStatus values that satisfy "is_complete".
# TESTING = dep branch folded into spec branch. DONE = final landed state.
# REVIEW is excluded on purpose (see module docstring): agent may have
# produced mergeable work but the branch is still pre-merge, so dependents
# cannot yet build against it.
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}


def _get_task_by_id(task_id) -> Optional[Task]:
    """Django ORM resolver: look up a Task by primary key or None."""
    try:
        return Task.objects.get(id=task_id)
    except Task.DoesNotExist:
        return None


def check_deps(task: Task) -> DepStatus:
    """Return the current dependency status for a task.

    Always queries the database for current dep statuses — never cached.
    Recovery is automatic: if a human fixes a failed dep, the dependent
    unblocks on the next check.
    """
    dep_ids = list(task.depends_on or [])
    return check_dep_status(
        dep_ids,
        _get_task_by_id,
        is_complete=lambda t: t.status in COMPLETED_STATUSES,
        is_failed=lambda t: t.status == TaskStatus.FAILED,
    )


def get_blocked_by(task: Task) -> List[Task]:
    """Return tasks on the SAME board whose depends_on list contains task.id.

    These are the tasks that cannot proceed until `task` is resolved.
    Excludes self. Returns empty when nothing depends on this task.
    """
    task_id = str(task.id)
    candidates = Task.objects.filter(board=task.board).exclude(id=task.id)
    return [candidate for candidate in candidates if task_id in (candidate.depends_on or [])]


def get_failed_deps(task: Task) -> List[Task]:
    """Return dependency tasks that are in FAILED status."""
    if not task.depends_on:
        return []
    return list(Task.objects.filter(id__in=task.depends_on, status=TaskStatus.FAILED))


def get_unmet_deps(task: Task) -> List[Task]:
    """Return dependency tasks not yet in a completed status."""
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
        dep_ids = list(task.depends_on or [])
        status = check_dep_status(
            dep_ids,
            _get_task_by_id,
            is_complete=lambda t: t.status in COMPLETED_STATUSES,
            is_failed=lambda t: t.status == TaskStatus.FAILED,
        )
        if status == DepStatus.READY:
            ready.append(task)

    return ready
