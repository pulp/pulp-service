from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("service", "0017_alter_pypiyankmonitor_pulp_id_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="vulnerabilityreport",
            options={
                "default_related_name": "%(app_label)s_%(model_name)s",
                "permissions": [
                    ("manage_roles_vulnerabilityreport", "Can manage roles on vulnerability reports"),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="pypiyankmonitor",
            options={
                "default_related_name": "%(app_label)s_%(model_name)s",
                "permissions": [
                    ("manage_roles_pypiyankmonitor", "Can manage roles on PyPI yank monitors"),
                ],
            },
        ),
    ]
