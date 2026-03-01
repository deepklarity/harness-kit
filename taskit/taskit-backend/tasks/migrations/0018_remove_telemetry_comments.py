"""Data migration: convert telemetry comments to status_update and store metrics in task.metadata."""

import re

from django.db import migrations


def migrate_telemetry_to_metadata(apps, schema_editor):
    """Find all telemetry comments, extract metrics into task.metadata, convert to status_update."""
    TaskComment = apps.get_model("tasks", "TaskComment")
    Task = apps.get_model("tasks", "Task")

    telemetry_comments = TaskComment.objects.filter(comment_type="telemetry")

    for comment in telemetry_comments:
        # Try to extract duration and summary from comment content
        task = comment.task
        task_metadata = task.metadata or {}

        content = comment.content or ""
        lines = content.split("\n")
        metrics_line = lines[0] if lines else ""

        # Parse "Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)"
        duration_match = re.search(r"in (\d+\.?\d*)s", metrics_line)
        if duration_match and "last_duration_ms" not in task_metadata:
            task_metadata["last_duration_ms"] = float(duration_match.group(1)) * 1000

        # Extract summary (everything after the metrics line)
        body = "\n".join(lines[1:]).strip()
        if body and "last_execution_summary" not in task_metadata:
            task_metadata["last_execution_summary"] = body

        # Determine success from verb
        if "last_execution_success" not in task_metadata:
            if metrics_line.startswith("Completed"):
                task_metadata["last_execution_success"] = True
            elif metrics_line.startswith("Failed"):
                task_metadata["last_execution_success"] = False

        # Store agent from author_label
        if comment.author_label and "last_execution_agent" not in task_metadata:
            task_metadata["last_execution_agent"] = comment.author_label

        task.metadata = task_metadata
        task.save(update_fields=["metadata"])

        # Convert comment type — preserves history, no data loss
        comment.comment_type = "status_update"
        comment.save(update_fields=["comment_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0017_backfill_comment_type"),
    ]

    operations = [
        migrations.RunPython(
            migrate_telemetry_to_metadata,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
