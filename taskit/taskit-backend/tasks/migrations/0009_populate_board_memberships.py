from django.db import migrations


def populate_board_memberships(apps, schema_editor):
    """Create BoardMembership for every distinct (board_id, assignee_id) from existing tasks."""
    Task = apps.get_model("tasks", "Task")
    BoardMembership = apps.get_model("tasks", "BoardMembership")

    pairs = (
        Task.objects
        .filter(assignee__isnull=False)
        .values_list("board_id", "assignee_id")
        .distinct()
    )

    memberships = [
        BoardMembership(board_id=board_id, user_id=user_id)
        for board_id, user_id in pairs
    ]

    BoardMembership.objects.bulk_create(memberships, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0008_boardmembership"),
    ]

    operations = [
        migrations.RunPython(populate_board_memberships, migrations.RunPython.noop),
    ]
