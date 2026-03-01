"""Add 'summary' to CommentType choices."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0019_add_proof_comment_type"),
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
                    ("summary", "Summary"),
                ],
                default="status_update",
                max_length=20,
            ),
        ),
    ]
