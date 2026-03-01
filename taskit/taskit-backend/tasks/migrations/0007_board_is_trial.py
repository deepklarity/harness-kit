from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0006_user_firebase_auth"),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="is_trial",
            field=models.BooleanField(default=False),
        ),
    ]
