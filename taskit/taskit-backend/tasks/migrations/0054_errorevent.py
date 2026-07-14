import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0053_mistake_entry'),
    ]

    operations = [
        migrations.CreateModel(
            name='ErrorEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(choices=[('failure_tagger', 'Failure tagger miss'), ('merge_failure', 'Merge ladder failure'), ('reflection_error', 'Reflection ERROR verdict'), ('gate_crash', 'Spec-verify gate crash'), ('celery_exception', 'Celery task exception')], max_length=30)),
                ('source_id', models.CharField(blank=True, default='', max_length=64)),
                ('symptom', models.CharField(max_length=500)),
                ('symptom_signature', models.CharField(db_index=True, max_length=200)),
                ('failure_class', models.CharField(blank=True, default='', max_length=40)),
                ('log_path', models.CharField(blank=True, default='', max_length=500)),
                ('log_tail', models.TextField(blank=True, default='')),
                ('context', models.JSONField(blank=True, default=dict)),
                ('disposition', models.CharField(choices=[('open', 'Open'), ('fixed', 'Fixed'), ('non-issue', 'Non-issue')], db_index=True, default='open', max_length=20)),
                ('disposition_note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('spec', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='error_events', to='tasks.spec')),
                ('task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='error_events', to='tasks.task')),
            ],
            options={
                'db_table': 'error_events',
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]