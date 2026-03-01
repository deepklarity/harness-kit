from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0005_user_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="firebase_uid",
            field=models.CharField(
                blank=True, max_length=128, null=True, unique=True
            ),
        ),
    ]
