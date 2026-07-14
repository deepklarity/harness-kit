# Adds the comment_attribution_loss source value to ErrorEvent (task #250) and
# brings the choices list back in sync with the model: agent_malformed_status
# (task #237) had been added to the model without a matching migration, so this
# AlterField also closes that drift. The source field is a CharField with
# choices= so the DB accepts any string already, but keeping the choices in
# sync lets the API + admin surface every value uniformly.

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
                    ('comment_attribution_loss', 'Comment attributed to unknown@user (attribution lost)'),
                ],
                max_length=30,
            ),
        ),
    ]
