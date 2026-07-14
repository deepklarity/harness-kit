from django.db import migrations, models


class Migration(migrations.Migration):
    """Add CANCELED as a terminal-neutral Task status (fable task 192).

    Allowed transitions to CANCELED come from BACKLOG/TODO/IN_PROGRESS/FAILED/
    REVIEW/TESTING via the API; from EXECUTING only via the stop_execution
    flow's target_status. From DONE never (DONE is fully terminal). See
    tasks.state_transitions for the transition matrix and helper.
    """

    dependencies = [
        ('tasks', '0048_board_reflection_review_strategy'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='status',
            field=models.CharField(
                choices=[
                    ('BACKLOG', 'Backlog'),
                    ('TODO', 'Todo'),
                    ('IN_PROGRESS', 'In Progress'),
                    ('EXECUTING', 'Executing'),
                    ('REVIEW', 'Review'),
                    ('TESTING', 'Testing'),
                    ('DONE', 'Done'),
                    ('FAILED', 'Failed'),
                    ('CANCELED', 'Canceled'),
                ],
                default='TODO',
                max_length=20,
            ),
        ),
    ]
