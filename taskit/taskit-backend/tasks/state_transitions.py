"""Task status transition matrix and helpers.

Centralizes the rules for which ``TaskStatus`` transitions are allowed. The
rest of the codebase only constrains transitions by *which code path* writes
the status — this module formalizes that contract into a function the
serializer / view consults on every PATCH.

Why a separate module: the existing rules in ``docs/breadcrumb_analysis/
task-state-machine-celery-automation/FLOW.md`` are prose. A growing status
set (fable task 192 added ``CANCELED``) needs a machine-checkable shape so
operators don't accidentally violate the matrix via the API, and so the
test suite can pin every edge in one place.

Transition rules for ``CANCELED`` (the only state added by this module):

  * Allowed from ``BACKLOG``, ``TODO``, ``IN_PROGRESS``, ``FAILED``,
    ``REVIEW``, ``TESTING`` — an operator deciding "this work item is
    no longer needed" can land in CANCELED from any non-terminal-execution
    status.
  * From ``EXECUTING`` — only via the ``stop_execution`` endpoint's
    ``target_status`` field. A direct PATCH that sets ``status=CANCELED``
    on an EXECUTING task is rejected; the operator must go through the
    stop flow so the running process is actually terminated and the
    transition is auditable.
  * From ``DONE`` — never. ``DONE`` is fully terminal (a shipped change,
    not an abandoned one).
"""

from .models import TaskStatus


# Set of statuses that count as "agent finished its work" for dep
# satisfaction. CANCELED is intentionally NOT in here — a CANCELED task
# has not done the work, so downstream tasks stay WAITING (not ready, but
# also not blocked-failed) until a human decides.
TERMINAL_DONE_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.TESTING, TaskStatus.REVIEW})

# Fully terminal states. A task in any of these is closed for further
# system-driven mutation. DONE is the strongest (re-dispatch is impossible),
# CANCELED is the softest (operator can re-open by hand; the system never
# auto-touches it).
FULLY_TERMINAL_STATUSES = frozenset({TaskStatus.DONE})

# Statuses the polling executor and stale-watchdog consider "could be
# running." A CANCELED task is excluded so the watchdog never tries to
# mark it FAILED or recompute its assignment.
EXECUTOR_ACTIVE_STATUSES = frozenset({TaskStatus.EXECUTING})

# A CANCELED task is never an active-work item — used to gate scheduling,
# dependency gating, and active-work metrics. (A dep on a CANCELED task
# stays WAITING, not BLOCKED, because the failure isn't a real failure —
# the operator should re-spec the dep manually.)
TERMINAL_NEUTRAL_STATUSES = frozenset({TaskStatus.CANCELED})


# Transitions to CANCELED that the API permits in a direct PATCH. EXECUTING
# is excluded so the executor's stop_execution is the only path that can
# land there (the stop flow writes the metadata guards first, then applies
# the user's chosen target_status, preserving the audit trail).
CANCEL_ALLOWED_VIA_API_FROM = frozenset({
    TaskStatus.BACKLOG,
    TaskStatus.TODO,
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.TESTING,
    TaskStatus.FAILED,
})

# Transitions to CANCELED permitted through the stop_execution endpoint.
# EXECUTING joins the set here so the operator can cancel a running task
# without first having to stop-then-cancel.
CANCEL_ALLOWED_VIA_STOP_FROM = CANCEL_ALLOWED_VIA_API_FROM | {TaskStatus.EXECUTING}


def is_cancel_transition_allowed(from_status, to_status, *, via_stop_execution=False):
    """Return True if a transition INTO ``to_status == CANCELED`` is allowed.

    Args:
        from_status: current task status (string or TaskStatus member).
        to_status: requested target status.
        via_stop_execution: True when the call comes through the
            ``stop_execution`` endpoint, which expands the allowed origins
            to include EXECUTING.

    Other transitions (e.g. TODO → IN_PROGRESS) are not governed by this
    helper — they are owned by their respective code paths (the view's
    IN_PROGRESS trigger, the dag_executor's IN_PROGRESS → EXECUTING
    transition, etc.). This function is *only* the CANCELED gate.
    """
    if str(to_status) != TaskStatus.CANCELED:
        return True
    allowed = CANCEL_ALLOWED_VIA_STOP_FROM if via_stop_execution else CANCEL_ALLOWED_VIA_API_FROM
    return str(from_status) in allowed


__all__ = [
    "TERMINAL_DONE_STATUSES",
    "FULLY_TERMINAL_STATUSES",
    "EXECUTOR_ACTIVE_STATUSES",
    "TERMINAL_NEUTRAL_STATUSES",
    "CANCEL_ALLOWED_VIA_API_FROM",
    "CANCEL_ALLOWED_VIA_STOP_FROM",
    "is_cancel_transition_allowed",
]
