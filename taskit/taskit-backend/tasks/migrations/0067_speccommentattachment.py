from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0066_alter_notification_notification_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecCommentAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="spec_attachments/%Y/%m/")),
                ("original_filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=100, default="application/octet-stream")),
                ("file_size", models.BigIntegerField(default=0)),
                ("uploaded_by", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("comment", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="file_attachments", to="tasks.speccomment",
                )),
                ("spec", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="spec_attachments", to="tasks.spec",
                )),
            ],
            options={
                "db_table": "spec_comment_attachments",
            },
        ),
    ]
