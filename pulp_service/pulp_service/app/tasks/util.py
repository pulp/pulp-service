import time
from datetime import timedelta
from functools import wraps

from django.db import connections

from pulpcore.app.models import TaskSchedule
from pulpcore.plugin.models import Distribution


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


def except_catch_and_raise(queue):
    """
    Decorator to catch all exceptions, put them into a queue, and raise them again.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Put the exception into the queue
                queue.put(e)
                # Re-raise the exception
                raise
        return wrapper
    return decorator


def no_op_task():
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
    time.sleep(0.3) # This task will not take less than 300ms to execute
