"""Beat job: send a one-time reminder for FAILED tasks still stuck after 30 min.

Mirrors scan_pending_reflections (tasks/dag_executor.py) — runs on a beat
schedule, queries for FAILED tasks whose last failure was >30 min ago and
which haven't received a reminder yet, then dispatches a
TASK_FAILED_REMINDER notification per task.

The reminder uses metadata.failure_reminder_sent_at as a one-shot guard,
not a beat-window guard: each failure event triggers at most one reminder.
If a task is reset to TODO and re-fails later, it gets another reminder.
"""

try:
    from celery import shared_task
except ImportError:
    def shared_task(*args, **kwargs):
        def decorator(func):
            func.delay = lambda *a, **kw: func(*a, **kw)
            return func
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

from django.utils import timezone
from django.utils.dateparse import parse_datetime

REMINDER_THRESHOLD = timezone.timedelta(minutes=30)


def _failed_since(task, metadata):
    from .models import TaskHistory, TaskStatus

    latest = (
        TaskHistory.objects
        .filter(task_id=task.id, field_name="status", new_value=TaskStatus.FAILED)
        .order_by("-changed_at")
        .values_list("changed_at", flat=True)
        .first()
    )
    if latest is not None:
        return latest

    notified_at = metadata.get("failure_notified_at")
    if not notified_at:
        return None
    parsed = parse_datetime(notified_at)
    if parsed is not None and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@shared_task(name="tasks.failed_reminder.scan_for_failed_reminders")
def scan_for_failed_reminders(*, now=None) -> int:
    """Send TASK_FAILED_REMINDER notifications for FAILED tasks stuck >= 30 min.

    Returns the count of reminders dispatched. Operates in two queries to
    keep this cheap on large boards: one to find candidates, one to mark
    them so the next beat cycle skips them.
    """
    from .models import BoardMembership, Task, TaskStatus
    from .notification_service import notify

    moment = now or timezone.now()
    cutoff = moment - REMINDER_THRESHOLD

    candidate_ids = []
    for task in Task.objects.filter(status=TaskStatus.FAILED).only("id", "metadata"):
        metadata = task.metadata or {}
        if not metadata.get("failure_notified_at"):
            continue
        if metadata.get("failure_reminder_sent_at"):
            continue
        failed_since = _failed_since(task, metadata)
        if failed_since is None:
            continue
        if failed_since <= cutoff:
            candidate_ids.append(task.id)

    if not candidate_ids:
        return 0

    sent = 0
    for task in Task.objects.filter(id__in=candidate_ids).select_related("board"):
        metadata = dict(task.metadata or {})
        if metadata.get("failure_reminder_sent_at"):
            continue
        metadata["failure_reminder_sent_at"] = moment.isoformat()
        Task.objects.filter(id=task.id).update(metadata=metadata)

        recipient_ids = list(
            BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)
        )
        if not recipient_ids:
            sent += 1
            continue

        notify(
            recipient_ids=recipient_ids,
            notification_type="task_failed_reminder",
            title=f'Task "{task.title}" still failed',
            body=(
                f"{task.metadata.get('last_failure_reason', '') or 'Failed earlier'} "
                f"— still unresolved after 30 minutes."
            ),
            task=task,
            board=task.board,
            actor_email="system@taskit",
        )
        sent += 1
    return sent
