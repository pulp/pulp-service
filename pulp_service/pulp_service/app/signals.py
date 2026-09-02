import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from pulpcore.plugin.models import Domain, Group
from pulpcore.plugin.util import assign_role

from pulp_service.app.authorization import group_var
from pulp_service.app.constants import ORG_GROUP_PREFIX
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


def _assign_domain_roles(entity, domain):
    """
    Assign the following RBAC roles for the passed entity:
    - core.domain_owner: object-level, can manage and see the domain itself
    - service.domain_admin: domain-scoped, manage objects inside the domain
    """
    assign_role("core.domain_owner", entity, obj=domain)
    assign_role("service.domain_admin", entity, domain=domain)


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

        domain = kwargs["instance"]
        if user_id:
            # post_save fires after the Domain INSERT. When the creating request already
            # wraps the save in a transaction (self-service CreateDomainView), the block
            # below nests as a savepoint and a failure rolls the Domain back with it. When
            # the caller is in autocommit (generic DomainViewSet), the Domain row is already
            # committed on its own, so on failure we delete it to avoid leaving a domain
            # without its RBAC/DomainOrg dual-write state.
            domain_committed_standalone = not connection.in_atomic_block
            try:
                with transaction.atomic():
                    user = get_user_model().objects.get(pk=user_id)
                    # The creator always gets direct roles, even when the domain is group-scoped.
                    # This diverges from migration 0019 (which assigns to user OR group per
                    # DomainOrg row); on a rollback+re-migrate the creator would lose this
                    # direct service.domain_admin. The creator keeps direct admin.
                    _assign_domain_roles(user, domain)
                    group = explicit_group
                    if explicit_group:
                        do = DomainOrg.objects.create(org_id=org_id, group=explicit_group)
                    # Skip the auto-assigned rh-org-<org_id> groups: those are per-org and
                    # already covered by the org_id match in DomainBasedPermission. Only an
                    # explicit "team" group should scope domain visibility to a group.
                    # Query through the pulpcore Group proxy (not user.groups, which yields
                    # base auth.Group instances) so assign_role classifies it as a Group.
                    elif group := Group.objects.filter(user=user).exclude(name__startswith=ORG_GROUP_PREFIX).first():
                        do = DomainOrg.objects.create(org_id=org_id, group=group)
                    else:
                        do = DomainOrg.objects.create(org_id=org_id, user=user)

                    if group is not None:
                        _assign_domain_roles(group, domain)
                    if org_id:
                        org_group, _ = Group.objects.get_or_create(name=f"{ORG_GROUP_PREFIX}{org_id}")
                        if org_group != group:
                            _assign_domain_roles(org_group, domain)

                    do.domains.add(domain)
            except Exception:
                if domain_committed_standalone:
                    domain.delete()
                raise
