"""Global memory budget for microVM sandbox spawns (execution + reflection).

WHY: every ``odin exec`` / ``odin reflect`` dispatch boots a libkrun microVM
that commits its full provisioned cap (~4 GB) up front — libkrun reserves the
cap, not just the RSS the guest happens to touch. Without coordination the
host over-commits RAM, swaps, and the resulting slowdown causes false watchdog
alarms (the binding constraint this module exists to fix). One shared budget
— drawn from by every VM-spawning dispatch kind — ends that: a spawn that
would exceed the budget is held by the dispatcher until a live VM releases,
instead of booting into swap.

Model: account in *provisioned* MiB. Each spawn reserves its cap
(``SANDBOX_DEFAULT_VM_MEM_MIB``, default 4096 — matching the harness's
``microsandbox_mem_size_mib``); the sum of live reservations must stay under
the budget.

Crash-safe accounting: the live reservation is DERIVED from DB state —
EXECUTING tasks' ``metadata.active_execution.mem_mib`` plus RUNNING reflection
reports — exactly how ``poll_and_execute`` already derives the execution count
from EXECUTING status. A worker that dies mid-spawn leaves the task EXECUTING
/ report RUNNING until ``_recover_stale_executions`` reconciles it; there is
no in-memory counter to leak. ``compute_reserved_mib`` is therefore a pure
query and safe to call from any process.

Merge is intentionally OUTSIDE this budget: the merge agent runs pure git in
the Celery worker (``resolve_conflicts_in_worktree`` → ``checkout --ours``),
no microVM, so reserving memory for it would be wrong. Its worker-pool
starvation is addressed by the optional dedicated merge queue
(``MERGE_QUEUE_NAME``), not memory.

Default budget: ``SANDBOX_MEMORY_BUDGET_MIB`` env var. When unset/0 the budget
is derived from the host (total RAM minus a 6 GB reserve for OS + backend +
celery + UI); when the host RAM can't be detected the budget is treated as
unbounded so dev/test boxes without ``/proc`` aren't choked (opt in via env).
"""

from __future__ import annotations

from django.conf import settings

# One microVM's provisioned cap. Mirrors odin's AgentConfig.microsandbox_mem_size_mib
# (opencode/bun OOMs below ~4G). The dispatcher doesn't know each task's
# per-agent size, so it accounts the standard cap — the honest upper bound on
# what a spawn reserves.
DEFAULT_VM_MEM_MIB = 4096

# RAM to leave for OS + backend + celery + UI when deriving the budget from
# the host. The scope's rule: "leave >= 6 GB for OS+services".
OS_RESERVE_MIB = 6144


def default_vm_mem_mib() -> int:
    """The MiB a single spawn reserves. Override via settings for non-default
    VM sizes; defaults to :data:`DEFAULT_VM_MEM_MIB`."""
    return int(getattr(settings, "SANDBOX_DEFAULT_VM_MEM_MIB", DEFAULT_VM_MEM_MIB) or DEFAULT_VM_MEM_MIB)


def _host_ram_mib():
    """Best-effort total host RAM in MiB, or None when it can't be detected.

    Reads ``/proc/meminfo`` on Linux (the production host). Returns None
    anywhere else (macOS dev, containers without ``/proc``) so the caller
    falls back to an unbounded budget rather than guessing — operators on
    those hosts opt in via ``SANDBOX_MEMORY_BUDGET_MIB``.
    """
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    # "MemTotal:       16384000 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def get_budget_mib():
    """The global memory budget in MiB, or None when unbounded.

    Resolution order:
      1. ``SANDBOX_MEMORY_BUDGET_MIB`` > 0 (explicit operator override).
      2. host RAM − ``OS_RESERVE_MIB`` (default, sized to this host).
      3. None (unbounded) when the host RAM can't be detected — opt in via env.

    Returning None (rather than a guess) means the dispatch gates treat the
    budget as disabled, matching the pre-feature behavior on hosts where the
    RAM is unknown (e.g. a container without ``/proc``).
    """
    configured = int(getattr(settings, "SANDBOX_MEMORY_BUDGET_MIB", 0) or 0)
    if configured > 0:
        return configured
    host = _host_ram_mib()
    if not host:
        return None
    reserve = int(getattr(settings, "SANDBOX_OS_RESERVE_MIB", OS_RESERVE_MIB) or OS_RESERVE_MIB)
    derived = host - reserve
    if derived < default_vm_mem_mib():
        # Host can't fit even one VM after the OS reserve. Gating would
        # deadlock (no spawn ever fits), so defer to the concurrency cap and
        # treat the budget as unbounded — operators on such hosts opt in via
        # SANDBOX_MEMORY_BUDGET_MIB explicitly.
        return None
    return derived


def compute_reserved_mib() -> int:
    """MiB currently reserved by live VM spawns, derived from DB state.

    Sums:
      - every EXECUTING task's ``active_execution.mem_mib`` (or the default
        when the stamp is absent — a live VM consumes memory whether or not
        the stamp landed), and
      - every RUNNING reflection report at the default VM size.

    Pure query: safe to call from the poller, the reflection task, or a
    diagnostic. Never raises — a malformed metadata blob contributes 0.
    """
    from .models import ReflectionReport, ReflectionStatus, Task, TaskStatus

    vm = default_vm_mem_mib()
    reserved = 0
    # EXECUTING tasks — each holds one microVM. Use the stamp when present,
    # otherwise the default: a real EXECUTING VM is consuming the cap whether
    # or not the metadata recorded it (the only unstamped ones are transient
    # stragglers from a pre-feature deploy, swept by _recover_stale_executions).
    for task in Task.objects.filter(status=TaskStatus.EXECUTING).only("id", "metadata"):
        active = (task.metadata or {}).get("active_execution") or {}
        try:
            reserved += int(active.get("mem_mib") or vm)
        except (TypeError, ValueError):
            reserved += vm

    # RUNNING reflections — each reviewer runs inside its own microVM at the
    # default size. PENDING reflections hold no VM yet.
    running_reflections = ReflectionReport.objects.filter(status=ReflectionStatus.RUNNING).count()
    reserved += running_reflections * vm

    return reserved


def memory_share_holders(board=None):
    """Enumerate the live VM-spawning tasks currently holding a memory share.

    Returns a list of dicts — one per holder — read from the same DB state the
    dispatcher's ``compute_reserved_mib`` reads from. Same source, same moment:
    the dispatch-blocked banner names exactly these tasks, and the factory
    / board header surfaces exactly these tasks.

    Entry shape::

        {
            "task_id": <int>,        # task whose execution holds the share
            "task_title": <str>,
            "kind": "execution" | "reflection",
            "mem_mib": <int>,        # bytes reserved against the budget
            "report_id": <int|None>, # only present for reflection entries
        }

    Without a board argument the helper returns every holder across all
    boards (the global accounting the dispatcher itself uses).
    """
    from .models import (
        ReflectionReport, ReflectionStatus, Task, TaskStatus,
    )

    vm = default_vm_mem_mib()
    holders = []
    tasks_qs = Task.objects.filter(status=TaskStatus.EXECUTING)
    if board is not None:
        tasks_qs = tasks_qs.filter(board=board)
    for task in tasks_qs.select_related("board").only(
        "id", "title", "metadata", "board_id",
    ):
        active = (task.metadata or {}).get("active_execution") or {}
        try:
            mem_mib = int(active.get("mem_mib") or vm)
        except (TypeError, ValueError):
            mem_mib = vm
        holders.append({
            "task_id": task.id,
            "task_title": task.title,
            "kind": "execution",
            "mem_mib": mem_mib,
        })

    refl_qs = ReflectionReport.objects.filter(status=ReflectionStatus.RUNNING)
    if board is not None:
        refl_qs = refl_qs.filter(task__board=board)
    for report in refl_qs.select_related("task").only(
        "id", "task_id", "task__title",
    ):
        holders.append({
            "task_id": report.task_id,
            "task_title": report.task.title if report.task else "",
            "kind": "reflection",
            "mem_mib": vm,
            "report_id": report.id,
        })
    return holders


def memory_share_summary(board=None) -> dict:
    """Aggregate ``memory_share_holders`` plus the budget into one operator
    surface. Same primitives as ``compute_reserved_mib`` + ``get_budget_mib``
    so the board header / factory never disagrees with the dispatcher.

    Shape::

        {
            "budget_mib": <int|None>,        # None = unbounded
            "reserved_mib": <int>,           # = compute_reserved_mib() for this board/global
            "default_vm_mem_mib": <int>,
            "max_shares": <int>,             # = budget_mib // default_vm_mem_mib
            "executing_count": <int>,
            "reflecting_count": <int>,
            "shares_in_use": <int>,          # = executing + reflecting
            "holders": [<holder>, ...],      # see memory_share_holders()
        }
    """
    from .models import Task, TaskStatus

    vm = default_vm_mem_mib()
    budget = get_budget_mib()
    holders = memory_share_holders(board=board)

    if board is not None:
        # Per-board reservation mirrors the same DB-state derivation as the
        # global ``compute_reserved_mib`` — the surface and the gate read
        # the same rows.
        reserved = 0
        for task in Task.objects.filter(
            board=board, status=TaskStatus.EXECUTING,
        ).only("id", "metadata"):
            active = (task.metadata or {}).get("active_execution") or {}
            try:
                reserved += int(active.get("mem_mib") or vm)
            except (TypeError, ValueError):
                reserved += vm
        from .models import ReflectionReport, ReflectionStatus
        reserved += ReflectionReport.objects.filter(
            status=ReflectionStatus.RUNNING, task__board=board,
        ).count() * vm
    else:
        reserved = compute_reserved_mib()

    executing = sum(1 for h in holders if h["kind"] == "execution")
    reflecting = sum(1 for h in holders if h["kind"] == "reflection")
    return {
        "budget_mib": budget,
        "reserved_mib": reserved,
        "default_vm_mem_mib": vm,
        "max_shares": (budget // vm) if budget else 0,
        "executing_count": executing,
        "reflecting_count": reflecting,
        "shares_in_use": executing + reflecting,
        "holders": holders,
    }


_UNSET = object()


def spawn_fits(mem_mib: int, reserved: int = None, budget_mib=_UNSET) -> bool:
    """True when a spawn of ``mem_mib`` fits under the budget right now.

    ``reserved`` defaults to :func:`compute_reserved_mib` (a live DB read);
    pass it explicitly to avoid re-querying when the caller already has it.
    ``budget_mib`` defaults to :func:`get_budget_mib`; passing ``None``
    explicitly means "unbounded" (no budget configured) → always fits, so the
    feature is strictly opt-in by environment.
    """
    if budget_mib is _UNSET:
        budget_mib = get_budget_mib()
    if budget_mib is None:
        return True
    if reserved is None:
        reserved = compute_reserved_mib()
    return (reserved + mem_mib) <= budget_mib
