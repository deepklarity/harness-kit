from django.conf import settings
from django.db import migrations, models


def _link_existing_users(apps, schema_editor):
    task_user_model = apps.get_model("tasks", "User")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    auth_user_model = apps.get_model(app_label, model_name)

    for task_user in task_user_model.objects.all().iterator():
        email = (task_user.email or "").strip().lower()
        if not email:
            continue

        auth_user = auth_user_model.objects.filter(username=email).first()
        if auth_user is None:
            auth_user = auth_user_model(
                username=email,
                email=email,
                first_name=(task_user.name or "")[:150],
            )
            # Historical migration models do not expose model instance methods
            # like set_unusable_password(). A leading "!" marks unusable passwords.
            auth_user.password = "!"
            auth_user.save()

        updates = []
        if task_user.auth_user_id is None:
            task_user.auth_user_id = auth_user.id
            updates.append("auth_user")
        if task_user.role != "AGENT" and not task_user.must_change_password:
            task_user.must_change_password = True
            updates.append("must_change_password")
        if updates:
            task_user.save(update_fields=updates)


def _unlink_existing_users(apps, schema_editor):
    task_user_model = apps.get_model("tasks", "User")
    task_user_model.objects.update(auth_user_id=None, must_change_password=False)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tasks", "0025_add_execution_trace_to_reflectionreport"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="auth_user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="taskit_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="password_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(_link_existing_users, _unlink_existing_users),
    ]
