from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0034_task_skip_reflection")]
    operations = [
        migrations.AddField(model_name="board", name="skip_reflection", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="board", name="reflection_model", field=models.CharField(max_length=255, null=True, blank=True)),
    ]
