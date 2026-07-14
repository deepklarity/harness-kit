"""Board "factory" snapshot assembly (task #240).

A single-call, at-a-glance operator view of a board's live state: what's
running right now, how the queues are stacked, what merged recently,
what error signatures are still open, and a one-line story headline.
Backs ``GET /boards/<id>/factory/`` (tasks/views.py) so a dashboard
widget or CLI status check hits one endpoint instead of stitching
together task/run, error-ledger, and story queries separately.

Mirrors the separation-of-concerns pattern used by
``tasks/board_story.py`` — a plain builder function here, a thin view
action in ``tasks/views.py``.
"""
from django.utils import timezone

from .errors import group_by_signature
from .board_story import build_board_story
from .kanban_ordering import get_statuses_for_column
from .models import ErrorEvent, MergeAttempt, Task, TaskRun, TaskRunState, TaskStatus

OPEN_ERRORS_LIMIT = 20
RECENT_MERGES_LIMIT = 10


def _serialize_running(run):
    now = timezone.now()
    seconds_since_heartbeat = None
    if run.last_heartbeat is not None:
        seconds_since_heartbeat = (now - run.last_heartbeat).total_seconds()
    return {
        "task_id": run.task_id,
        "task_title": run.task.title,
        "run_token": run.run_token,
        "state": run.state,
        "started_at": run.started_at,
        "last_heartbeat": run.last_heartbeat,
        "seconds_since_heartbeat": seconds_since_heartbeat,
    }


def _serialize_merge(merge):
    lag_seconds = None
    if merge.started_at is not None and merge.finished_at is not None:
        lag_seconds = round((merge.finished_at - merge.started_at).total_seconds(), 3)
    return {
        "id": merge.id,
        "task_id": merge.task_id,
        "task_title": merge.task.title,
        "mode": merge.mode,
        "outcome": merge.outcome,
        "trigger": merge.trigger,
        "started_at": merge.started_at,
        "finished_at": merge.finished_at,
        "lag_seconds": lag_seconds,
    }


def _serialize_open_error(group):
    return {
        "source": group["source"],
        "signature": group["signature"],
        "latest_symptom": group["latest_symptom"],
        "latest_disposition": group["latest_disposition"],
        "count": group["count"],
        "event_ids": group["event_ids"],
        "latest_at": group["latest_at"],
    }


def _queue_counts(board):
    waiting = Task.objects.filter(
        board=board,
        status__in=get_statuses_for_column(TaskStatus.BACKLOG) + get_statuses_for_column(TaskStatus.TODO),
    ).count()
    executing = Task.objects.filter(
        board=board, status__in=get_statuses_for_column(TaskStatus.IN_PROGRESS),
    ).count()
    review = Task.objects.filter(
        board=board, status__in=get_statuses_for_column(TaskStatus.REVIEW),
    ).count()
    shelf = Task.objects.filter(
        board=board, status__in=get_statuses_for_column(TaskStatus.TESTING),
    ).count()
    return {"waiting": waiting, "executing": executing, "review": review, "shelf": shelf}


def build_factory_snapshot(board) -> dict:
    """Assemble the factory snapshot for a board.

    Returns a dict with ``board_id``, ``board_name``, ``running``,
    ``queues``, ``recent_merges``, ``open_errors``, and ``story``. All
    keys are always present, even when the underlying collections are
    empty.
    """
    running_runs = (
        TaskRun.objects.filter(task__board=board, state=TaskRunState.RUNNING)
        .select_related("task")
        .order_by("-started_at")
    )
    running = [_serialize_running(run) for run in running_runs]

    queues = _queue_counts(board)

    recent_merges_qs = (
        MergeAttempt.objects.filter(task__board=board)
        .select_related("task")
        .order_by("-started_at")[:RECENT_MERGES_LIMIT]
    )
    recent_merges = [_serialize_merge(m) for m in recent_merges_qs]

    error_groups = group_by_signature(disposition=ErrorEvent.DISPOSITION_OPEN, board=board)
    open_errors = [_serialize_open_error(g) for g in error_groups[:OPEN_ERRORS_LIMIT]]

    story_result = build_board_story(board, since=None)
    events = story_result["events"]
    headline = events[-1]["line"] if events else None
    story = {
        "headline": headline,
        "tldr": story_result["tldr"],
        "event_count": story_result["event_count"],
    }

    return {
        "board_id": board.id,
        "board_name": board.name,
        "running": running,
        "queues": queues,
        "recent_merges": recent_merges,
        "open_errors": open_errors,
        "story": story,
    }
