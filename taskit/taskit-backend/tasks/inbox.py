"""Board inbox assembly (task #258) — everything waiting on a human,
in one call.

Backs ``GET /boards/<id>/inbox/`` (tasks/views.py). Reuses the same
metadata signals ``board_story.py`` already reads for its
``waiting_on_human`` TL;DR count (``merge_status`` /
``hard_action_status`` == ``"needs_human"``) and the open-error-ledger
query ``factory_snapshot.py`` already assembles
(``errors.group_by_signature``), so the inbox never drifts from those
single sources of truth. Mirrors the plain-builder-plus-thin-view
pattern used by ``board_story.py`` and ``factory_snapshot.py``.
"""
from .dependencies import get_blocked_by
from .errors import group_by_signature
from .kanban_ordering import get_statuses_for_column
from .models import CommentType, ErrorEvent, Task, TaskHistory, TaskStatus

OPEN_ERRORS_LIMIT = 20


def _latest_question(task):
    """The most recent QUESTION comment on a task, or None."""
    return (
        task.comments.filter(comment_type=CommentType.QUESTION)
        .order_by("-created_at")
        .first()
    )


def _why_and_comment_id(task):
    """One-line WHY (first line of the latest question) + its comment id.

    Returns ``("", None)`` when the task has no question comment — the
    park still shows up so an operator isn't left guessing why a task
    is stuck, it just has no reply target yet.
    """
    comment = _latest_question(task)
    if not comment:
        return "", None
    why = (comment.content or "").split("\n", 1)[0][:200]
    return why, comment.id


def _serialize_failed_task(task):
    metadata = task.metadata or {}
    failed_at = (
        TaskHistory.objects
        .filter(task_id=task.id, field_name="status", new_value=TaskStatus.FAILED)
        .order_by("-changed_at")
        .values_list("changed_at", flat=True)
        .first()
    ) or task.last_updated_at
    return {
        "task_id": str(task.id),
        "task_title": task.title,
        "last_failure_reason": metadata.get("last_failure_reason") or "",
        "failure_class": metadata.get("failure_class") or "",
        "failed_at": failed_at.isoformat(),
        "blocked_count": len(get_blocked_by(task)),
    }


def _serialize_parked_merge(task):
    why, comment_id = _why_and_comment_id(task)
    metadata = task.metadata or {}
    return {
        "task_id": task.id,
        "task_title": task.title,
        "why": why,
        "question_comment_id": comment_id,
        "conflicting_files": metadata.get("merge_ambiguous_files") or [],
    }


def _serialize_reversibility_park(task):
    why, comment_id = _why_and_comment_id(task)
    metadata = task.metadata or {}
    hard_action = metadata.get("hard_action") or {}
    return {
        "task_id": task.id,
        "task_title": task.title,
        "why": why,
        "question_comment_id": comment_id,
        "action_key": hard_action.get("key", ""),
        "branch": hard_action.get("branch", ""),
    }


def _serialize_shelf_task(task):
    return {
        "task_id": task.id,
        "task_title": task.title,
    }


def _serialize_open_error(group):
    latest_event = ErrorEvent.objects.filter(id=group["event_ids"][0]).only("task_id").first()
    return {
        "source": group["source"],
        "signature": group["signature"],
        "latest_symptom": group["latest_symptom"],
        "count": group["count"],
        "event_ids": group["event_ids"],
        "latest_at": group["latest_at"],
        "task_id": latest_event.task_id if latest_event else None,
    }


def build_inbox(board):
    """Assemble everything waiting on a human for this board.

    Returns a dict with ``board_id``, ``board_name``, five lists —
    ``failed_tasks``, ``parked_merges``, ``reversibility_parks``,
    ``testing_shelf``, ``open_errors`` — and a ``counts`` dict
    summarizing each. Every key is always present, even when empty, so
    the UI can render honest empty states without special-casing missing
    fields.
    """
    tasks = list(Task.objects.filter(board=board))

    failed_tasks = [
        _serialize_failed_task(t) for t in tasks
        if t.status == TaskStatus.FAILED
    ]
    parked_merges = [
        _serialize_parked_merge(t) for t in tasks
        if (t.metadata or {}).get("merge_status") == "needs_human"
    ]
    reversibility_parks = [
        _serialize_reversibility_park(t) for t in tasks
        if (t.metadata or {}).get("hard_action_status") == "needs_human"
    ]
    testing_shelf = [
        _serialize_shelf_task(t) for t in tasks
        if t.status in get_statuses_for_column(TaskStatus.TESTING)
    ]

    error_groups = group_by_signature(disposition=ErrorEvent.DISPOSITION_OPEN, board=board)
    open_errors = [_serialize_open_error(g) for g in error_groups[:OPEN_ERRORS_LIMIT]]

    return {
        "board_id": board.id,
        "board_name": board.name,
        "failed_tasks": failed_tasks,
        "parked_merges": parked_merges,
        "reversibility_parks": reversibility_parks,
        "testing_shelf": testing_shelf,
        "open_errors": open_errors,
        "counts": {
            "failed_tasks": len(failed_tasks),
            "parked_merges": len(parked_merges),
            "reversibility_parks": len(reversibility_parks),
            "testing_shelf": len(testing_shelf),
            "open_errors": len(open_errors),
        },
    }
