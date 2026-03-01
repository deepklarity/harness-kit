from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0010_widen_spec_odin_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="available_models",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="task",
            name="model_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
