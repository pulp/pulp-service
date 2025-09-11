from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from pulpcore.plugin.models import Domain

from pulp_service.app.models import DomainOrg, GroupMembership


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
            try:
                primary_membership = GroupMembership.objects.get(user=user, is_primary=True)
                group = primary_membership.group
                do = DomainOrg.objects.create(org_id=org_id, user=user, group=group)
            except GroupMembership.DoesNotExist:
                do = DomainOrg.objects.create(org_id=org_id, user=user)
            do.domains.add(kwargs['instance'])


@receiver(m2m_changed, sender=get_user_model().groups.through)
def user_groups_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    """
    Signal handler for when a user's groups are changed.
    Handles the relationship being changed from either the User or Group side.
    """
    if action == "post_add":
        if reverse:
            # instance is a Group, pk_set contains User pks
            group = instance
            for user_pk in pk_set:
                user = get_user_model().objects.get(pk=user_pk)
                if not GroupMembership.objects.filter(user=user, is_primary=True).exists():
                    GroupMembership.objects.create(user=user, group=group, is_primary=True)
                else:
                    GroupMembership.objects.get_or_create(user=user, group=group)
        else:
            # instance is a User, pk_set contains Group pks
            user = instance
            has_primary_group = GroupMembership.objects.filter(user=user, is_primary=True).exists()
            for group_pk in pk_set:
                group = Group.objects.get(pk=group_pk)
                if not has_primary_group:
                    GroupMembership.objects.create(user=user, group=group, is_primary=True)
                    has_primary_group = True  # Ensure only the first group in the set is primary
                else:
                    GroupMembership.objects.get_or_create(user=user, group=group)

    elif action in ["post_remove", "post_clear"]:
        if reverse:
            # instance is a Group, pk_set contains User pks
            group = instance
            GroupMembership.objects.filter(group=group, user__pk__in=pk_set).delete()
        else:
            # instance is a User, pk_set contains Group pks
            user = instance
            if pk_set:
                GroupMembership.objects.filter(user=user, group__pk__in=pk_set).delete()
            else:
                # This handles the case where all groups are cleared for a user
                GroupMembership.objects.filter(user=user).delete()

