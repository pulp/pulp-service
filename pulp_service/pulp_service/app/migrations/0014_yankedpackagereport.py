import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service', '0013_domainorg_group_alter_domainorg_user'),
    ]

    operations = [
        migrations.CreateModel(
            name='YankedPackageReport',
            fields=[
                ('pulp_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('pulp_created', models.DateTimeField(auto_now_add=True)),
                ('pulp_last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('report', models.JSONField()),
            ],
            options={
                'default_related_name': '%(app_label)s_%(model_name)s',
            },
        ),
    ]
