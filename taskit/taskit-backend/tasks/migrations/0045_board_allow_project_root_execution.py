# Generated for F43 dispatch guardrails — boards opt-in to project-root execution.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0044_board_auto_start_planned_tasks'),
    ]

    operations = [
        migrations.AddField(
            model_name='board',
            name='allow_project_root_execution',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Opt-in: if True, poll_and_execute will run an agent "
                    "against the board's working_dir when no worktree is "
                    "available. Default is False — tasks without a worktree "
                    "are FAILED with a clear reason instead of silently "
                    "dispatching into the project root."
                ),
            ),
        ),
    ]
