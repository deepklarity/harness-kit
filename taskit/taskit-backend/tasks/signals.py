"""Auto-comment on significant task status transitions.

Listens for TaskHistory post_save events. When a task transitions to
FAILED or DONE via a mechanism that doesn't already post its own comment
(e.g. manual status change in the UI, DAG executor fallback), this signal
creates a system comment recording the transition.

Execution results from Odin's execution_result endpoint already post
detailed metric comments — those are identified by changed_by containing
"@odin.agent" and are skipped here to avoid duplicate comments.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger("taskit.signals")


@receiver(post_save, sender="tasks.TaskHistory")
def auto_comment_on_status_transition(sender, instance, created, **kwargs):
    """Create a system comment when a task transitions to FAILED or DONE."""
    if not created:
        return

    if instance.field_name != "status":
        return

    new_status = (instance.new_value or "").upper()
    if new_status not in ("FAILED", "DONE"):
        return

    # Skip if this change came from execution_result (already posts its own comment).
    # execution_result uses actor emails like "claude+model@odin.agent".
    changed_by = instance.changed_by or ""
    if "@odin.agent" in changed_by:
        return

    # Avoid circular import
    from .models import TaskComment

    # Guard against duplicate: don't comment if a system comment for the same
    # transition was already posted in the last 5 seconds
    from django.utils import timezone
    import datetime

    cutoff = timezone.now() - datetime.timedelta(seconds=5)
    existing = TaskComment.objects.filter(
        task_id=instance.task_id,
        author_email="system@taskit",
        created_at__gte=cutoff,
        content__contains=new_status,
    ).exists()
    if existing:
        return

    # If we already have a rich failure comment, skip generic status noise.
    if new_status == "FAILED":
        rich_failure_exists = TaskComment.objects.filter(
            task_id=instance.task_id,
            created_at__gte=cutoff,
        ).exclude(author_email="system@taskit").filter(
            content__contains="Failure type:"
        ).exists()
        if rich_failure_exists:
            return

    old_status = instance.old_value or "unknown"
    content = f"Status changed: {old_status} \u2192 {new_status} (by {changed_by})"

    TaskComment.objects.create(
        task_id=instance.task_id,
        author_email="system@taskit",
        author_label="system",
        content=content,
    )
    logger.info(
        "[task:%s] Auto-comment: %s \u2192 %s (by %s)",
        instance.task_id, old_status, new_status, changed_by,
    )
