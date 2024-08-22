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
        # The default domain is created when migrations are run for the first time.
        # org_id is only available when REST API is used to create the domain.
        if org_id:
            DomainOrg.objects.create(domain=kwargs['instance'], org_id=org_id)

        user_id = user_id_var.get(None)
        if user_id:
            user = get_user_model().objects.get(pk=user_id)
            DomainOrg.objects.create(domain=kwargs['instance'], user=user)

            for group in user.groups:
                DomainOrg.objects.get_or_create(domain=kwargs['instance'], group=group)
