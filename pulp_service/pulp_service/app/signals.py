import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from pulpcore.plugin.models import Domain

from pulp_service.app.authorization import group_var
from pulp_service.app.models import DomainOrg

_logger = logging.getLogger(__name__)


@receiver(post_migrate)
def register_scheduled_tasks(sender, **kwargs):  # noqa: ARG001
    if sender.name == "pulp_service.app":
        from pulp_service.app.tasks.util import (
            content_sources_periodic_telemetry,
            lightwell_sync_schedule,
            register_pypi_yank_monitor_schedule,
            rhel_ai_repos_periodic_telemetry,
        )

        content_sources_periodic_telemetry()
        rhel_ai_repos_periodic_telemetry()
        register_pypi_yank_monitor_schedule()
        lightwell_sync_schedule()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def log_new_user(sender, instance, created, **kwargs):  # noqa: ARG001
    """Log when a new user is created, including the route they first accessed."""
    if created:
        from pulp_service.app.middleware import request_path_var

        request_path = request_path_var.get(None)
        _logger.info("New user created: username=%s, route=%s", instance.username, request_path or "unknown")


@receiver(post_save, sender=Domain)
def post_create_domain(sender, **kwargs):  # noqa: ARG001
    if kwargs["created"]:
        from pulp_service.app.authorization import org_id_var, user_id_var

        org_id = org_id_var.get(None)
        org_id_var.set(None)
        user_id = user_id_var.get(None)
        user_id_var.set(None)
        explicit_group = group_var.get(None)
        group_var.set(None)
        if user_id:
            user = get_user_model().objects.get(pk=user_id)
            if explicit_group:
                do = DomainOrg.objects.create(org_id=org_id, group=explicit_group)
            elif group := user.groups.first():
                do = DomainOrg.objects.create(org_id=org_id, group=group)
            else:
                do = DomainOrg.objects.create(org_id=org_id, user=user)
            do.domains.add(kwargs["instance"])
