# Adds the reflection_no_reviewer source value to ErrorEvent (task #246).
# The source field is a CharField with choices= so the DB accepts any string
# already, but keeping the choices in sync lets the API + admin surface the
# new value uniformly.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0055_mistake_fingerprint'),
    ]

    operations = [
        migrations.AlterField(
            model_name='errorevent',
            name='source',
            field=models.CharField(
                choices=[
                    ('failure_tagger', 'Failure tagger miss'),
                    ('merge_failure', 'Merge ladder failure'),
                    ('reflection_error', 'Reflection ERROR verdict'),
                    ('gate_crash', 'Spec-verify gate crash'),
                    ('celery_exception', 'Celery task exception'),
                    ('reflection_no_reviewer', 'Reflection no reviewer available'),
                ],
                max_length=30,
            ),
        ),
    ]
