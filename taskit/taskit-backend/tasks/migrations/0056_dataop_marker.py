from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0055_mistake_fingerprint'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataOpMarker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128, unique=True)),
                ('ran_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'dataop_markers',
                'ordering': ['-ran_at'],
            },
        ),
    ]
