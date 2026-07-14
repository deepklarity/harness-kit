from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0048_board_reflection_review_strategy'),
    ]

    operations = [
        # W4.2 (task #186) — board-level off switch for auto-promote.
        # The metadata JSONField is a free-form feature-flag bag; the
        # auto-promote key is `auto_promote_enabled: false` to disable.
        # Default: empty dict, every flag-defaults-on.  No data backfill
        # needed because the field is added with a dict default.
        migrations.AddField(
            model_name='board',
            name='metadata',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Board-level feature flags. Wave 4: "
                    "{'auto_promote_enabled': false} disables auto-promotion "
                    "for this board. Absent or true (default) keeps the feature on."
                ),
            ),
        ),
    ]
