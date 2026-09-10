from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0021_backfill_domainorg_roles"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="domainorg",
            options={
                "permissions": [
                    (
                        "view_orphan_content",
                        "Can view repository-less (orphan) content in a domain",
                    ),
                ],
            },
        ),
    ]
