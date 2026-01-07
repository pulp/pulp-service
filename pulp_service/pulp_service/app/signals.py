import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from pulpcore.plugin.models import Domain

from pulp_service.app.models import DomainOrg


_logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def log_new_user(sender, instance, created, **kwargs):
    """Log when a new user is created, including the route they first accessed."""
    if created:
        from pulp_service.app.middleware import request_path_var

        request_path = request_path_var.get(None)
        _logger.info(
            f"New user created: username={instance.username}, route={request_path or 'unknown'}"
        )


@receiver(post_save, sender=Domain)
def post_create_domain(sender, **kwargs):
    if kwargs['created']:
        from pulp_service.app.authorization import org_id_var, user_id_var
        org_id = org_id_var.get(None)
        org_id_var.set(None)
        user_id = user_id_var.get(None)
        user_id_var.set(None)
        if user_id:
            # The default domain is created when migrations are run for the first time.
            # User is only available when the REST API is used to create the domain.
            user = get_user_model().objects.get(pk=user_id)
            if group := user.groups.first():
                # if the Group data exists, set it as the only owner of the DomainOrg
                do = DomainOrg.objects.create(org_id=org_id, group=group)
            else:
                do = DomainOrg.objects.create(org_id=org_id, user=user)
            do.domains.add(kwargs['instance'])
