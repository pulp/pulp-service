from django.db import migrations

SCHEDULE_NAME = "Sync Lightwell repository with Trusted Libraries content"


def remove_lightwell_sync_schedule(apps, schema_editor):
    """Delete the Lightwell sync TaskSchedule created by the post_migrate receiver.

    The receiver no longer registers it, but environments that already ran a
    migration with the old code still have the row, and pulpcore's scheduler
    keeps dispatching whatever is in the table.
    """
    TaskSchedule = apps.get_model("core", "TaskSchedule")
    TaskSchedule.objects.filter(name=SCHEDULE_NAME).delete()


def restore_lightwell_sync_schedule(apps, schema_editor):
    """Recreate the schedule on migrate backwards (PULP-2709 rollback)."""
    from datetime import timedelta

    TaskSchedule = apps.get_model("core", "TaskSchedule")
    TaskSchedule.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task_name": "pulp_service.app.tasks.lightwell_period_sync.python_repository_sync",
            "dispatch_interval": timedelta(days=1),
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0160_envvarheadercontentguard"),
        ("service", "0026_andgatecontentguard"),
    ]

    operations = [
        migrations.RunPython(remove_lightwell_sync_schedule, restore_lightwell_sync_schedule),
    ]
