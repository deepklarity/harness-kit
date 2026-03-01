"""Add 'proof' to CommentType choices."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0018_remove_telemetry_comments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskcomment",
            name="comment_type",
            field=models.CharField(
                choices=[
                    ("status_update", "Status Update"),
                    ("question", "Question"),
                    ("reply", "Reply"),
                    ("proof", "Proof"),
                ],
                default="status_update",
                max_length=20,
            ),
        ),
    ]
