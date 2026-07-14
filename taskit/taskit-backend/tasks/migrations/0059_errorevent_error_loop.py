# Adds the error_loop source value to ErrorEvent (task #262), and brings
# the choices list fully in sync with the model (agent_malformed_status
# was added to the model after 0056 without a matching migration). The
# source field is a CharField(30) so the DB column already accepts any
# string; this keeps Django's migration checker + admin in sync.

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
                    ('error_loop', 'Run reconciler error-loop detection'),
                ],
                max_length=30,
            ),
        ),
    ]
