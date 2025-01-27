from datetime import timedelta

from pulpcore.app.models import TaskSchedule


def content_sources_periodic_telemetry():
    task_name = "pulp_service.app.tasks.domain_metrics.content_sources_domains_count"
    dispatch_interval = timedelta(hours=1)
    name = "Collect Content Sources metrics periodically"
    TaskSchedule.objects.update_or_create(
        name=name, defaults={"task_name": task_name, "dispatch_interval": dispatch_interval}
    )


def rhel_ai_repos_periodic_telemetry():
    task_name = "pulp_service.app.tasks.domain_metrics.rhel_ai_repos_count"
    dispatch_interval = timedelta(hours=1)
    name = "Collect RHEL AI metrics periodically"
    TaskSchedule.objects.update_or_create(
        name=name, defaults={"task_name": task_name, "dispatch_interval": dispatch_interval}
    )
