from django.db import migrations, models


KANBAN_COLUMNS = [
    "BACKLOG",
    "TODO",
    "IN_PROGRESS",
    "REVIEW",
    "TESTING",
    "DONE",
    "FAILED",
]


def _column_for_status(status):
    if status == "EXECUTING":
        return "IN_PROGRESS"
    return status


def backfill_kanban_positions(apps, schema_editor):
    Task = apps.get_model("tasks", "Task")
    board_ids = list(Task.objects.values_list("board_id", flat=True).distinct())
    for board_id in board_ids:
        tasks = list(
            Task.objects
            .filter(board_id=board_id)
            .order_by("-created_at", "-id")
        )
        grouped = {column: [] for column in KANBAN_COLUMNS}
        for task in tasks:
            grouped.setdefault(_column_for_status(task.status), []).append(task)
        for column in KANBAN_COLUMNS:
            for idx, task in enumerate(grouped.get(column, [])):
                Task.objects.filter(pk=task.pk).update(kanban_position=idx)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0026_user_auth_link_and_password_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="kanban_position",
            field=models.IntegerField(db_index=True, default=0),
        ),
        migrations.RunPython(backfill_kanban_positions, noop_reverse),
    ]
