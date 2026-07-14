"""Odin-side dependency helpers — thin wrappers over odin.dag.

Adapts the shared DAG algorithms to odin's Pydantic Task model.
The canonical logic lives in odin.dag; this module only supplies the
odin-specific predicates and the task_resolver calling convention.

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
from typing import Callable, List, Optional

from odin.dag import DepStatus, check_dep_status, detect_cycle, filter_ready
from odin.taskit.models import Task, TaskStatus

__all__ = [
    "DepStatus",
    "COMPLETED_STATUSES",
    "check_deps",
    "get_failed_deps",
    "get_unmet_deps",
    "get_ready_tasks",
]

logger = logging.getLogger(__name__)

# Statuses that count as "agent finished AND merged to spec branch".
# Must stay in sync with taskit-backend/tasks/dependencies.py (single source
# of truth for the *semantic*; both use odin.dag for the *algorithm*).
# TESTING = dep branch folded into spec branch. DONE = final landed state.
# REVIEW is excluded on purpose (see module docstring): agent may have
# produced mergeable work but the branch is still pre-merge, so dependents
# cannot yet build against it.
COMPLETED_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}


def check_deps(
    task: Task,
    task_resolver: Callable[[str], Optional[Task]],
) -> DepStatus:
    """Return the current dependency status for a task.

    Uses task_resolver (typically task_manager.get_task) to look up
    dependency tasks by ID. Always queries current state — never cached.
    """
    return check_dep_status(
        task.depends_on,
        task_resolver,
        is_complete=lambda t: t.status in COMPLETED_STATUSES,
        is_failed=lambda t: t.status == TaskStatus.FAILED,
    )


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
    """Return IDs of dependencies not yet in a completed status."""
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
    return filter_ready(
        tasks,
        task_resolver,
        is_complete=lambda t: t.status in COMPLETED_STATUSES,
        is_failed=lambda t: t.status == TaskStatus.FAILED,
        is_candidate=lambda t: t.status == TaskStatus.TODO,
    )
