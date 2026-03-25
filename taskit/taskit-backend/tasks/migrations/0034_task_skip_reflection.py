from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0033_remove_pushsubscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="skip_reflection",
            field=models.BooleanField(default=False),
        ),
    ]
