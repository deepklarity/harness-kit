from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0007_board_is_trial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BoardMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="tasks.board")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="board_memberships", to="tasks.user")),
            ],
            options={
                "db_table": "board_memberships",
                "unique_together": {("board", "user")},
            },
        ),
    ]
