from django.db import migrations
from django.db.models import Q

SCHEDULE_NAME = "Sync Lightwell repository with Trusted Libraries content"
TASK_NAME = "pulp_service.app.tasks.lightwell_period_sync.python_repository_sync"


def remove_lightwell_sync_schedule(apps, schema_editor):  # noqa: ARG001
    """Delete the Lightwell sync TaskSchedule created by the post_migrate receiver.

    The receiver no longer registers it, but environments that already ran a
    migration with the old code still have the row, and pulpcore's scheduler
    keeps dispatching whatever is in the table.

    Match on ``task_name`` as well as ``name``: task schedules are editable through
    the REST API, so a row may have been renamed while still pointing at the removed
    task, which would otherwise keep dispatching (and failing) forever.
    """
    TaskSchedule = apps.get_model("core", "TaskSchedule")
    TaskSchedule.objects.filter(Q(name=SCHEDULE_NAME) | Q(task_name=TASK_NAME)).delete()


def restore_lightwell_sync_schedule(apps, schema_editor):  # noqa: ARG001
    """Recreate the schedule on migrate backwards (PULP-2709 rollback)."""
    from datetime import timedelta

    TaskSchedule = apps.get_model("core", "TaskSchedule")
    TaskSchedule.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task_name": TASK_NAME,
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
