"""Notification signal handlers for TaskHistory events.

Separate from signals.py (which handles auto-commenting) to keep concerns
isolated. Both modules are imported in apps.TasksConfig.ready().
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"DONE", "FAILED"})


@receiver(post_save, sender="tasks.TaskHistory")
def notify_on_status_change(sender, instance, created, **kwargs):
    """Fire a status_changed notification to all board members when a task status changes."""
    if not created:
        return

    if instance.field_name != "status":
        return

    # Avoid circular imports — models are guaranteed initialised by the time
    # any signal fires in a live Django process.
    from .models import BoardMembership, Task
    from .notification_service import notify

    try:
        task = Task.objects.select_related("board").get(pk=instance.task_id)
    except Task.DoesNotExist:
        logger.warning(
            "[notification_signals] TaskHistory %s references missing task %s — skipping",
            instance.pk,
            instance.task_id,
        )
        return

    recipient_ids = list(
        BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)
    )

    if not recipient_ids:
        logger.debug(
            "[notification_signals] No board members for board %s — skipping status_changed",
            task.board_id,
        )
        return

    notify(
        recipient_ids=recipient_ids,
        notification_type="status_changed",
        title=f'Task "{task.title}" \u2192 {instance.new_value}',
        body=f"Changed from {instance.old_value} to {instance.new_value}",
        task=task,
        board=task.board,
        actor_email=instance.changed_by or "",
    )

    logger.info(
        "[notification_signals] status_changed notification dispatched for task %s (%s → %s)",
        task.pk,
        instance.old_value,
        instance.new_value,
    )


@receiver(post_save, sender="tasks.TaskHistory")
def notify_on_spec_finished(sender, instance, created, **kwargs):
    """Fire a spec_finished notification when every task in a spec reaches a terminal status."""
    if not created:
        return

    if instance.field_name != "status":
        return

    if (instance.new_value or "").upper() not in _TERMINAL_STATUSES:
        return

    from .models import BoardMembership, Task
    from .notification_service import notify

    try:
        task = Task.objects.select_related("board", "spec").get(pk=instance.task_id)
    except Task.DoesNotExist:
        logger.warning(
            "[notification_signals] TaskHistory %s references missing task %s — skipping",
            instance.pk,
            instance.task_id,
        )
        return

    if task.spec is None:
        return

    # Check whether all sibling tasks in the spec have reached a terminal status.
    sibling_statuses = list(
        Task.objects.filter(spec=task.spec).values_list("status", flat=True)
    )

    if not sibling_statuses:
        return

    if not all(s.upper() in _TERMINAL_STATUSES for s in sibling_statuses):
        return

    done_count = sum(1 for s in sibling_statuses if s.upper() == "DONE")
    failed_count = sum(1 for s in sibling_statuses if s.upper() == "FAILED")
    body = f"{done_count} done, {failed_count} failed"

    recipient_ids = list(
        BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)
    )

    if not recipient_ids:
        logger.debug(
            "[notification_signals] No board members for board %s — skipping spec_finished",
            task.board_id,
        )
        return

    notify(
        recipient_ids=recipient_ids,
        notification_type="spec_finished",
        title=f'Spec "{task.spec.title}" completed',
        body=body,
        spec=task.spec,
        board=task.board,
        actor_email=instance.changed_by or "",
    )

    logger.info(
        "[notification_signals] spec_finished notification dispatched for spec %s (%s)",
        task.spec.pk,
        body,
    )
