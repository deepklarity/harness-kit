from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0047_taskcomment_promotion_report'),
    ]

    operations = [
        # Board-level config for size-bucketed reviewer selection (W3.18).
        # When null/empty the existing single-reviewer default is preserved.
        # Structure:
        #   {
        #     "thresholds": {"small_max": 8000, "large_min": 24000},
        #     "small": "claude-haiku-4-5",
        #     "medium": "claude-sonnet-4-6",
        #     "large": "claude-sonnet-4-6"
        #   }
        migrations.AddField(
            model_name='board',
            name='reflection_review_strategy',
            field=models.JSONField(blank=True, default=dict),
        ),
        # Why this reviewer was picked — surfaces in the report so operators
        # can audit the selection decision (W3.18 acceptance).
        migrations.AddField(
            model_name='reflectionreport',
            name='selection_reason',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]