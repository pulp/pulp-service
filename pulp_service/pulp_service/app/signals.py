from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from pulpcore.plugin.models import Domain

from pulp_service.app.models import DomainOrg


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
            do = DomainOrg.objects.create(org_id=org_id, user=user)
            do.domains.add(kwargs['instance'])
