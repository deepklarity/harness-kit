from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0030_board_working_dir"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="cost_tier",
            field=models.CharField(blank=True, default="medium", max_length=20),
        ),
        migrations.AddField(
            model_name="user",
            name="capabilities",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="user",
            name="cli_command",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="default_model",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="premium_model",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="boardmembership",
            name="disabled_models",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
