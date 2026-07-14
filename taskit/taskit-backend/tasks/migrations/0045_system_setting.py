# Generated migration for SystemSetting model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0044_board_auto_start_planned_tasks'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemSetting',
            fields=[
                ('key', models.CharField(max_length=255, primary_key=True, serialize=False, unique=True)),
                ('value', models.IntegerField()),
                ('help_text', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'system_settings',
            },
        ),
    ]
