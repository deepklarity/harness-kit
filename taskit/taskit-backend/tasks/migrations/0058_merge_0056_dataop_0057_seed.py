# Merge migration: wave-7 close — 246 (0056_errorevent_reflection_no_reviewer
# -> 0057_errorevent_seed_task246) and 247 (0056_dataop_marker) landed as
# sibling leaves.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0056_dataop_marker'),
        ('tasks', '0057_errorevent_seed_task246'),
    ]

    operations = []
