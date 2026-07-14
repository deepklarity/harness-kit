"""TaskRun lifecycle helpers (task #210).

A TaskRun row is the first-class record of one sandbox-execution attempt.
It is created at spawn by the executor (dag_executor.poll_and_execute /
execution.local.LocalOdinStrategy.trigger) and heartbeated from the
subprocess-monitoring loop that already polls once a second — one
mechanism, not two.

TaskRun.run_token is the fencing anchor consulted at the API boundary
(views.execution_result): a write whose run_token doesn't match the
task's current RUNNING row is rejected. Legacy metadata.active_execution
blobs stay for compatibility this wave; TaskRun is the source of truth
going forward.
"""

from django.utils import timezone

from .db import retry_on_locked
from .models import TaskRun, TaskRunState

# TaskRun rows that never got an explicit finish/expire/kill are still
# considered "open" — a crash between spawn and the first status write
# leaves the row RUNNING, and start_run() must still supersede it.
OPEN_STATES = (TaskRunState.RUNNING,)


def start_run(task, run_token, *, pid=None, sandbox_name=""):
    """Create a new RUNNING TaskRun for `task`, expiring any prior open run.

    A prior RUNNING row for the same task means a redispatch superseded it
    without an explicit finish (e.g. the stale-recovery sweep hasn't run
    yet) — treat it as expired-by-supersession so exactly one RUNNING row
    exists per task at a time, and any zombie still holding the old
    run_token fails the fencing check in views.execution_result.
    """
    TaskRun.objects.filter(task=task, state__in=OPEN_STATES).exclude(
        run_token=run_token,
    ).update(state=TaskRunState.EXPIRED, finished_at=timezone.now())

    return TaskRun.objects.create(
        task=task,
        spec=task.spec,
        run_token=run_token,
        pid=pid,
        sandbox_name=sandbox_name or "",
        state=TaskRunState.RUNNING,
    )


@retry_on_locked(max_retries=3, base_delay=0.05)
def heartbeat_run(run_token, *, pid=None):
    """Touch last_heartbeat (and optionally pid) for the RUNNING row, if any.

    No-op when the token is blank or no matching RUNNING row exists (e.g.
    it already finished/expired) — heartbeats never resurrect a closed run.

    Retries a transient ``database is locked`` (task #253): this write is
    on the cancellation-poll hot path (touched every few seconds by
    ``_run_subprocess_with_cancellation``), so an un-retried lock here
    killed the surrounding execution via the broad ``except``.
    """
    if not run_token:
        return
    updates = {"last_heartbeat": timezone.now()}
    if pid is not None:
        updates["pid"] = pid
    TaskRun.objects.filter(run_token=run_token, state=TaskRunState.RUNNING).update(**updates)


def finish_run(run_token, state=TaskRunState.FINISHED):
    """Transition the TaskRun for `run_token` out of RUNNING.

    Idempotent: a row already in a terminal state (FINISHED/EXPIRED/KILLED)
    is left alone so a duplicate finish call can't clobber an earlier,
    more specific terminal state.
    """
    if not run_token:
        return
    TaskRun.objects.filter(run_token=run_token, state=TaskRunState.RUNNING).update(
        state=state, finished_at=timezone.now(),
    )


def current_running_run(task):
    """Return the task's current RUNNING TaskRun, or None if untracked.

    None means the task isn't (yet) tracked by TaskRun — legacy dispatch
    paths that don't call start_run() — so callers should treat "no
    tracked run" as "nothing to fence against" rather than a violation.
    """
    return TaskRun.objects.filter(task=task, state=TaskRunState.RUNNING).order_by("-started_at", "-id").first()
