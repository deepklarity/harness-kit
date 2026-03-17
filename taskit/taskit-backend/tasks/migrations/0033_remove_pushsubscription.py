from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0032_notification_system"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PushSubscription",
        ),
    ]
