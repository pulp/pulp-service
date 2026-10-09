from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0023_backfill_domain_creator_role"),
    ]

    operations = [
        # Expand-contract: remove DomainOrg from Django's migration state so the code and
        # makemigrations stay consistent, but keep the physical table so old-version pods and
        # workers still serving during a rolling deploy do not hit a dropped relation. A
        # follow-up migration drops the table once all old code has drained.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(
                    name="DomainOrg",
                ),
            ],
            database_operations=[],
        ),
    ]
