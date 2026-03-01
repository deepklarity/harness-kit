"""Backfill Task.model_name from metadata['selected_model'] for existing tasks."""

from django.db import migrations


def backfill_model_name(apps, schema_editor):
    Task = apps.get_model("tasks", "Task")
    updated = []
    for task in Task.objects.filter(model_name__isnull=True).exclude(metadata={}):
        selected_model = None
        if isinstance(task.metadata, dict):
            selected_model = task.metadata.get("selected_model") or task.metadata.get("model")
        if selected_model:
            task.model_name = selected_model
            updated.append(task)
    if updated:
        Task.objects.bulk_update(updated, ["model_name"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0011_user_available_models_task_model_name"),
    ]

    operations = [
        migrations.RunPython(backfill_model_name, migrations.RunPython.noop),
    ]
