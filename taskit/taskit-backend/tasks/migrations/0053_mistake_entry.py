# Generated for task #223 — mistakes ledger.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0052_merge_attempt'),
    ]

    operations = [
        migrations.CreateModel(
            name='MistakeEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(max_length=20)),
                ('source_id', models.CharField(blank=True, default='', max_length=64)),
                ('agent', models.CharField(blank=True, default='', max_length=255)),
                ('model', models.CharField(blank=True, default='', max_length=255)),
                ('one_liner', models.CharField(max_length=500)),
                ('failure_class', models.CharField(blank=True, default='', max_length=40)),
                ('verdict', models.CharField(blank=True, default='', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('spec', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mistakes', to='tasks.spec')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mistakes', to='tasks.task')),
            ],
            options={
                'db_table': 'mistake_entries',
                'ordering': ['-created_at'],
            },
        ),
    ]
