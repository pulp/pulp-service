import django.contrib.postgres.fields.hstore
import django.db.models.deletion
from django.db import migrations, models

import pulpcore.app.models.base
import pulpcore.app.util


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0154_task_api_version"),
        ("service", "0019_convert_domainorg_to_roles"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentView",
            fields=[
                (
                    "pulp_id",
                    models.UUIDField(
                        default=pulpcore.app.models.base.pulp_uuid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("pulp_created", models.DateTimeField(auto_now_add=True)),
                ("pulp_last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("name", models.TextField(db_index=True)),
                ("description", models.TextField(null=True)),
                ("pulp_labels", django.contrib.postgres.fields.hstore.HStoreField(default=dict)),
                (
                    "distributions",
                    models.ManyToManyField(related_name="service_content_views", to="core.distribution"),
                ),
                (
                    "pulp_domain",
                    models.ForeignKey(
                        default=pulpcore.app.util.get_domain_pk,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="core.domain",
                    ),
                ),
            ],
            options={
                "permissions": [
                    ("manage_roles_contentview", "Can manage role assignments on content view"),
                ],
                "unique_together": {("name", "pulp_domain")},
            },
        ),
        migrations.CreateModel(
            name="ContentViewSearchScope",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ],
            options={
                "managed": False,
            },
        ),
    ]
