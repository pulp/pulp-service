from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from pulpcore.plugin.models import Domain
from pulpcore.metrics import init_otel_meter

from pulp_service.app.models import DomainOrg


# Lazy initialization for OTEL meter and counter
_meter = None
_new_users_counter = None


def _get_new_users_counter():
    """Lazily initialize and return the new users counter."""
    global _meter, _new_users_counter
    if _new_users_counter is None:
        _meter = init_otel_meter("pulp-api")
        _new_users_counter = _meter.create_counter(
            name="users.new.count",
            description="Total count of new users created",
            unit="user",
        )
    return _new_users_counter


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def track_new_user(sender, instance, created, **kwargs):
    """Increment new user counter when User is created."""
    if created:
        counter = _get_new_users_counter()
        counter.add(1)


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
