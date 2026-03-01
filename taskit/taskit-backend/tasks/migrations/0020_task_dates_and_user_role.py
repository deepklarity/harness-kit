from django.db import migrations, models


def backfill_user_roles(apps, schema_editor):
    User = apps.get_model("tasks", "User")

    User.objects.filter(is_admin=True).update(role="ADMIN")
    User.objects.filter(
        is_admin=False,
        email__iendswith="@odin.agent",
    ).update(role="AGENT")
    User.objects.filter(role__isnull=True).update(role="HUMAN")
    User.objects.filter(role="").update(role="HUMAN")


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0019_add_proof_comment_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("HUMAN", "Human"), ("AGENT", "Agent"), ("ADMIN", "Admin")],
                db_index=True,
                default="HUMAN",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_user_roles, migrations.RunPython.noop),
    ]
