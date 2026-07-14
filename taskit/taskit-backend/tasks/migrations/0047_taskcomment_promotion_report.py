from django.db import migrations, models


COMMENT_TYPE_CHOICES = [
    ('status_update', 'Status Update'),
    ('question', 'Question'),
    ('reply', 'Reply'),
    ('proof', 'Proof'),
    ('summary', 'Summary'),
    ('reflection', 'Reflection'),
    ('planning', 'Planning'),
    ('promotion_report', 'Promotion Report'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0046_user_is_active'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskcomment',
            name='comment_type',
            field=models.CharField(
                choices=COMMENT_TYPE_CHOICES,
                default='status_update',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='speccomment',
            name='comment_type',
            field=models.CharField(
                choices=COMMENT_TYPE_CHOICES,
                default='status_update',
                max_length=20,
            ),
        ),
    ]