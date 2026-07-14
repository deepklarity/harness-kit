# Generated for task #225 — failure fingerprints.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0054_errorevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='mistakeentry',
            name='fingerprint',
            field=models.CharField(blank=True, db_index=True, default='', max_length=200),
        ),
    ]
