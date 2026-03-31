from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0038_merge_20260325_1044"),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="skip_proof",
            field=models.BooleanField(default=False),
        ),
    ]
