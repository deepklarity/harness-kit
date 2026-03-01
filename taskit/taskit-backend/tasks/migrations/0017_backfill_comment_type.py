"""Data migration: backfill comment_type from attachments/content patterns."""

from django.db import migrations


def backfill_comment_types(apps, schema_editor):
    TaskComment = apps.get_model("tasks", "TaskComment")
    for comment in TaskComment.objects.filter(comment_type="status_update"):
        inferred = _infer_type(comment)
        if inferred != "status_update":
            comment.comment_type = inferred
            comment.save(update_fields=["comment_type"])


def _infer_type(comment):
    """Infer comment_type from attachments and content patterns."""
    attachments = comment.attachments or []
    for att in attachments:
        if isinstance(att, dict):
            att_type = att.get("type")
            if att_type == "question":
                return "question"
            if att_type == "reply":
                return "reply"

    content = comment.content or ""
    if content.startswith("Completed in ") or content.startswith("Failed in "):
        return "telemetry"

    return "status_update"


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0016_taskcomment_comment_type"),
    ]

    operations = [
        migrations.RunPython(
            backfill_comment_types,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
