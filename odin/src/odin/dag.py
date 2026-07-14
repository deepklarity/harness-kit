"""Shared DAG algorithms for dependency ordering.

Pure-algorithm module — no coupling to odin Pydantic models or Django models.
Both odin and taskit-backend import from here; the reverse coupling is forbidden.

The three invariants enforced:
  1. A task is ready only when all its deps are in a completed state.
  2. A failed dep blocks all dependents (fail-fast semantics).
  3. Cycles are detected with the full cycle path before any execution begins.

Usage pattern — callers supply two predicates and a resolver:

    from odin.dag import DepStatus, check_dep_status, detect_cycle, filter_ready

    def get_task(tid): ...            # returns task object or None
    def is_complete(t): ...           # True when task counts as "done"
    def is_failed(t): ...             # True when task is in FAILED state

    status = check_dep_status(task.depends_on, get_task, is_complete, is_failed)
    cycle  = detect_cycle(task_ids, get_task)
"""

from enum import Enum
from typing import Any, Callable, Iterable, List, Optional


class DepStatus(str, Enum):
    READY = "ready"      # All deps satisfied (or no deps)
    WAITING = "waiting"  # Deps exist but not all completed yet
    BLOCKED = "blocked"  # At least one dep is FAILED


def check_dep_status(
    dep_ids: List[str],
    get_task_fn: Callable[[str], Optional[Any]],
    is_complete: Callable[[Any], bool],
    is_failed: Callable[[Any], bool],
) -> DepStatus:
    """Return the dependency status for a task given its dep IDs.

    Always queries current state via get_task_fn — never cached. This ensures
    recovery works: if a human fixes a failed dep, the dependent automatically
    unblocks on the next check.

    Args:
        dep_ids: list of dependency task IDs.
        get_task_fn: callable(id) → task object or None.
        is_complete: predicate returning True when a dep counts as finished.
        is_failed: predicate returning True when a dep is in FAILED state.

    Returns:
        READY   – all deps satisfy is_complete (or dep_ids is empty).
        WAITING – at least one dep is not complete and none are failed.
        BLOCKED – at least one dep satisfies is_failed.
    """
    if not dep_ids:
        return DepStatus.READY

    any_failed = False
    all_completed = True

    for dep_id in dep_ids:
        dep = get_task_fn(dep_id)
        if dep is None:
            # Unknown dep — treat as unmet (waiting)
            all_completed = False
            continue
        if is_failed(dep):
            any_failed = True
        if not is_complete(dep):
            all_completed = False

    if any_failed:
        return DepStatus.BLOCKED
    if all_completed:
        return DepStatus.READY
    return DepStatus.WAITING


def detect_cycle(
    task_ids: Iterable[str],
    get_task_fn: Callable[[str], Optional[Any]],
) -> Optional[List[str]]:
    """Detect a cycle in the dependency graph.

    Uses iterative DFS with three-color marking (WHITE/GRAY/BLACK) to find
    back-edges. Returns the cycle as a list of IDs (the repeated node appears
    at both ends), or None if the graph is acyclic.

    Only considers tasks reachable from task_ids via get_task_fn. Edges to
    unknown tasks (get_task_fn returns None) are silently skipped.

    Args:
        task_ids: iterable of task IDs to check.
        get_task_fn: callable(id) → task object (needs .depends_on attribute)
                     or None.

    Returns:
        None if acyclic, or a list of IDs forming the cycle path
        (e.g. ["a", "b", "c", "a"]).
    """
    task_ids = list(task_ids)

    # Build ID → task map for tasks we can resolve
    tasks_by_id = {}
    for tid in task_ids:
        task = get_task_fn(tid)
        if task is not None:
            tasks_by_id[tid] = task

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in tasks_by_id}

    def dfs(tid: str, path: List[str]) -> Optional[List[str]]:
        color[tid] = GRAY
        task = tasks_by_id.get(tid)
        if task is None:
            color[tid] = BLACK
            return None
        for dep_id in (getattr(task, "depends_on", None) or []):
            if dep_id not in color:
                # Edge to a task outside our known set — skip
                continue
            if color[dep_id] == GRAY:
                # Back-edge found — extract cycle
                cycle_start = path.index(dep_id)
                return path[cycle_start:] + [dep_id]
            if color[dep_id] == WHITE:
                result = dfs(dep_id, path + [dep_id])
                if result is not None:
                    return result
        color[tid] = BLACK
        return None

    for tid in tasks_by_id:
        if color[tid] == WHITE:
            result = dfs(tid, [tid])
            if result is not None:
                return result

    return None


def filter_ready(
    tasks: Iterable[Any],
    get_task_fn: Callable[[str], Optional[Any]],
    is_complete: Callable[[Any], bool],
    is_failed: Callable[[Any], bool],
    is_candidate: Optional[Callable[[Any], bool]] = None,
) -> List[Any]:
    """From an iterable of tasks, return those whose deps are all satisfied.

    Args:
        tasks: iterable of task objects. Each must have a .depends_on attribute.
        get_task_fn: callable(id) → task object or None, for looking up deps.
        is_complete: predicate for "dep is done".
        is_failed: predicate for "dep is FAILED".
        is_candidate: optional predicate to pre-filter tasks before dep-checking
                      (e.g. only tasks in TODO status). If None, all tasks are
                      checked.

    Returns:
        List of tasks (preserving input order) whose dep status is READY.
    """
    ready = []
    for task in tasks:
        if is_candidate is not None and not is_candidate(task):
            continue
        dep_ids = getattr(task, "depends_on", None) or []
        status = check_dep_status(dep_ids, get_task_fn, is_complete, is_failed)
        if status == DepStatus.READY:
            ready.append(task)
    return ready
