import django.contrib.postgres.fields.hstore
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0144_delete_old_appstatus'),
        ('service', '0015_agentscanreport'),
    ]

    operations = [
        migrations.CreateModel(
            name='PyPIYankMonitor',
            fields=[
                ('pulp_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('pulp_created', models.DateTimeField(auto_now_add=True)),
                ('pulp_last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('name', models.TextField(db_index=True, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('pulp_labels', django.contrib.postgres.fields.hstore.HStoreField(default=dict)),
                ('last_checked', models.DateTimeField(blank=True, null=True)),
                ('repository', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='service_pypiyankmonitor', to='core.repository')),
                ('repository_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='service_pypiyankmonitor', to='core.repositoryversion')),
            ],
            options={
                'default_related_name': '%(app_label)s_%(model_name)s',
            },
        ),
        migrations.AddField(
            model_name='yankedpackagereport',
            name='monitor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='service.pypiyankmonitor'),
        ),
        migrations.AddField(
            model_name='yankedpackagereport',
            name='repository_name',
            field=models.TextField(blank=True, null=True),
        ),
    ]
