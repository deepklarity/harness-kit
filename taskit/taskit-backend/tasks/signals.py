"""Auto-comment on significant task status transitions, and resume a
parked merge when a human replies to it.

Listens for TaskHistory post_save events. When a task transitions to
FAILED or DONE via a mechanism that doesn't already post its own comment
(e.g. manual status change in the UI, DAG executor fallback), this signal
creates a system comment recording the transition.

Execution results from Odin's execution_result endpoint already post
detailed metric comments — those are identified by changed_by containing
"@odin.agent" and are skipped here to avoid duplicate comments.

Also listens for TaskComment post_save events: when a human (non-agent)
comment lands on a task parked with ``merge_status="needs_human"``, it
re-dispatches the merge with the comment as resolution guidance (see
``tasks.dag_executor.resume_merge_with_guidance``). The same TaskComment
listener also resumes a task parked with
``metadata.hard_action_status="needs_human"`` (task #244's reversibility
gate — see ``tasks.dag_executor.resume_hard_action_with_reply``).
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
        schedule_run_id=instance.schedule_run_id,
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
            schedule_run_id=instance.schedule_run_id,
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
        schedule_run=instance.schedule_run,
        author_email="system@taskit",
        author_label="system",
        content=content,
    )
    logger.info(
        "[task:%s] Auto-comment: %s \u2192 %s (by %s)",
        instance.task_id, old_status, new_status, changed_by,
    )


def _is_agent_or_system_author(email):
    """True for odin-agent identities and internal system/merge-agent
    authors — comments from these must never re-trigger a merge resume,
    or the agent's own re-ask/status comments would loop it forever.
    """
    email = (email or "").lower()
    return (
        email.endswith("@odin.agent")
        or email.endswith("@odin")
        or email == "system@taskit"
    )


@receiver(post_save, sender="tasks.TaskComment")
def resume_merge_on_human_reply(sender, instance, created, **kwargs):
    """Re-dispatch a parked merge when a human replies to it.

    A merge conflict the agent couldn't classify parks the task in
    REVIEW with ``metadata.merge_status = "needs_human"`` and posts a
    blocking QUESTION comment explaining the conflict (see
    ``odin.merge_agent.format_merge_question``). When the human answers
    — via the plain comment box or the question's Reply action, both of
    which land here as a new ``TaskComment`` — this re-dispatches the
    merge with the reply text as resolution guidance.

    Fires on ANY new comment (not just replies to the specific question)
    since a human may just leave a top-level comment instead of using
    Reply; the ``merge_status`` guard keeps this a no-op otherwise.

    Guarded to human authors only: an agent identity (``@odin.agent``),
    the merge agent's own comments (``merge-agent@odin``), or system
    comments (``system@taskit``) never trigger resumption — otherwise
    the agent's own re-ask comment would dispatch itself in a loop.
    """
    if not created:
        return

    if _is_agent_or_system_author(instance.author_email):
        return

    from .models import Task

    try:
        task = Task.objects.get(id=instance.task_id)
    except Task.DoesNotExist:
        return

    if (task.metadata or {}).get("merge_status") != "needs_human":
        return

    from .dag_executor import resume_merge_with_guidance

    logger.info(
        "[task:%s] Human reply (%s) on needs_human merge — resuming with guidance",
        task.id, instance.author_email,
    )
    resume_merge_with_guidance.delay(task.id, instance.id)


@receiver(post_save, sender="tasks.TaskComment")
def resume_hard_action_on_human_reply(sender, instance, created, **kwargs):
    """Re-dispatch a parked hard action when a human replies to it.

    A hard-classed action (task #244's reversibility gate — see
    ``odin.reversibility``) parks with
    ``metadata.hard_action_status = "needs_human"`` instead of proceeding
    autonomously, and posts a blocking QUESTION explaining what's
    irreversible about it (see ``tasks.dag_executor._cleanup_task_worktree``).
    When a human answers, this re-dispatches
    ``tasks.dag_executor.resume_hard_action_with_reply`` — mirrors
    ``resume_merge_on_human_reply`` above, including the same
    agent/system-author guard so the mechanism's own comments never
    re-trigger themselves.
    """
    if not created:
        return

    if _is_agent_or_system_author(instance.author_email):
        return

    from .models import Task

    try:
        task = Task.objects.get(id=instance.task_id)
    except Task.DoesNotExist:
        return

    if (task.metadata or {}).get("hard_action_status") != "needs_human":
        return

    from .dag_executor import resume_hard_action_with_reply

    logger.info(
        "[task:%s] Human reply (%s) on needs_human hard action — resuming",
        task.id, instance.author_email,
    )
    resume_hard_action_with_reply.delay(task.id, instance.id)


@receiver(post_save, sender="tasks.TaskComment")
def flag_unknown_user_attribution(sender, instance, created, **kwargs):
    """Record an ErrorEvent when a comment lands as unknown@user (task #250).

    ``unknown@user`` means author identity was lost — the reply-resume
    signal, the L2 counter, and humans all decode author_email to decide who
    said what, so an unknown author is a silent lie. This makes that loss
    visible instead of burying it in a stored value. Covers every creation
    path (the API POST, direct objects.create — how the reflection verdict
    comment is built — and any future caller) because it lives on the signal.
    """
    if not created:
        return
    if (instance.author_email or "") != "unknown@user":
        return

    from .errors import record_comment_attribution_loss
    from .models import Task

    try:
        task = Task.objects.get(id=instance.task_id)
    except Task.DoesNotExist:
        task = None

    try:
        record_comment_attribution_loss(
            task=task,
            comment_id=instance.id,
            author_email=instance.author_email,
            comment_type=instance.comment_type,
        )
        logger.error(
            "[task:%s] Comment %s attributed to unknown@user — author "
            "identity lost; recorded to the error ledger.",
            instance.task_id, instance.id,
        )
    except Exception:
        logger.exception(
            "attribution guard: failed to record comment_attribution_loss "
            "for comment %s",
            instance.id,
        )


@receiver(post_save, sender="tasks.SpecComment")
def resume_board_plan_on_human_reply(sender, instance, created, **kwargs):
    """Resume a board-driven plan when a human replies to the gate questions.

    Mirrors ``resume_merge_on_human_reply`` but for specs: when a spec has
    ``metadata.board_plan_status == "awaiting_answers"`` and a non-agent
    SpecComment lands, this dispatches
    ``tasks.board_planner.resume_board_driven_plan`` — which runs
    ``odin plan --board-resume`` to do task breakdown with the reply as
    clarification answers.
    """
    if not created:
        return

    if _is_agent_or_system_author(instance.author_email):
        return

    from .models import Spec

    try:
        spec = Spec.objects.get(id=instance.spec_id)
    except Spec.DoesNotExist:
        return

    meta = spec.metadata or {}
    if meta.get("board_plan_status") != "awaiting_answers":
        return

    from .board_planner import resume_board_driven_plan

    logger.info(
        "[spec:%s] Human reply (%s) on board-driven plan awaiting answers — resuming",
        spec.id, instance.author_email,
    )
    resume_board_driven_plan.delay(spec.id, instance.id)
