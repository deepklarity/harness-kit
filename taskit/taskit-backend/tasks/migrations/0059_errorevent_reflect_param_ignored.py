# Adds the reflect_param_ignored source value to ErrorEvent (task #249).
# The source field is a CharField with choices= so the DB accepts any
# string already, but keeping the choices in sync lets the API + admin
# surface the new value uniformly. Also reconciles the full choices list
# (agent_malformed_status was dropped from the 0056 AlterField by mistake).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0058_merge_0056_dataop_0057_seed'),
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
                    ('agent_malformed_status', 'Agent emitted malformed ODIN-STATUS block'),
                    ('reflection_no_reviewer', 'Reflection no reviewer available'),
                    ('reflect_param_ignored', 'Reflect endpoint ignored explicit reviewer'),
                ],
                max_length=30,
            ),
        ),
    ]
