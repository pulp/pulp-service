from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.contrib import admin
from django.db.models import Q

from django import forms
from django.core.validators import RegexValidator

from .models import DomainOrg
import re

from pulpcore.plugin.models import Domain
from pulp_service.app.constants import CONTENT_SOURCES_LABEL_NAME

USERNAME_PATTERN = r'^[\w.@+=/-]+$'
USERNAME_ERROR_MSG = "Username can only contain letters, numbers, and these special characters: @, ., +, -, =, /, _"
USERNAME_HELP_TEXT = "Required. 150 characters or fewer. Letters, numbers, and these special characters: @, ., +, -, =, /, _"

# Override Django's username validator
pulp_username_validator = RegexValidator(
    USERNAME_PATTERN,
    USERNAME_ERROR_MSG,
    'invalid'
)


# Apply the new validator to the User model
User._meta.get_field('username').validators = [pulp_username_validator]
# Update the help_text as well
User._meta.get_field('username').help_text = USERNAME_HELP_TEXT

# Custom/Pulp forms to allow additional characters
class PulpUserCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields['username'].help_text = USERNAME_HELP_TEXT

    def clean_username(self):
        username = self.cleaned_data['username']
        if not re.match(USERNAME_PATTERN, username):
            raise forms.ValidationError(USERNAME_ERROR_MSG)
        return username


class PulpUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields['username'].help_text = USERNAME_HELP_TEXT
        
    def clean_username(self):
        username = self.cleaned_data['username']
        if not re.match(r'^[\w.@+=/-]+$', username):
            raise forms.ValidationError(
                "Username can only contain letters, numbers, and the characters @/./+/-/=/_"
            )
        return username


class PulpUserAdmin(UserAdmin):
    form = PulpUserChangeForm
    add_form = PulpUserCreationForm

    def get_queryset(self, request):
        """
        Filter users based on group memberships for non-superusers.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # Staff users can see themselves + users in their groups
        user_groups = request.user.groups.all()
        user_filter = Q(pk=request.user.pk)  # Always include themselves

        if user_groups.exists():
            user_filter |= Q(groups__in=user_groups)

        return qs.filter(user_filter).distinct()

    def has_change_permission(self, request, obj=None):
        """
        Staff users can only modify users in their groups or themselves.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        if obj == request.user:
            return True

        # Check if user shares any groups with the target user
        user_groups = set(request.user.groups.values_list('pk', flat=True))
        target_groups = set(obj.groups.values_list('pk', flat=True))

        return bool(user_groups.intersection(target_groups))

    def has_delete_permission(self, request, obj=None):
        """
        Only superusers can delete users.
        """
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        """
        Staff users can view users based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_module_permission(self, request):
        """
        Allow staff users to access the User module.
        """
        return request.user.is_staff


class PulpGroupAdmin(GroupAdmin):
    exclude = ('permissions',)

    def get_queryset(self, request):
        """
        Filter groups based on user's group memberships for non-superusers.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # Staff users can only see their own groups
        user_groups = request.user.groups.all()
        return qs.filter(pk__in=user_groups.values_list('pk', flat=True))

    def has_change_permission(self, request, obj=None):
        """
        Staff users can only modify groups they belong to.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return request.user.groups.filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        """
        Only superusers can delete groups.
        """
        return request.user.is_superuser

    def has_add_permission(self, request):
        """
        Only superusers can add new groups.
        """
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        """
        Staff users can view groups based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_module_permission(self, request):
        """
        Allow staff users to access the Group module.
        """
        return request.user.is_staff


class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"

    def has_permission(self, request):
        """
        Allow staff users (is_staff=True) to access admin, not just superusers.
        """
        return request.user.is_active and request.user.is_staff

    def get_app_list(self, request, app_label=None):
        """
        Filter app list based on user permissions and group memberships.
        """
        app_list = super().get_app_list(request, app_label)

        if request.user.is_superuser:
            return app_list

        # For staff users, let them see all registered models
        # The individual model admins will handle the detailed filtering
        return app_list

class ContentSourceDomainFilter(admin.SimpleListFilter):
    title = 'ContentSource Domains'
    parameter_name = 'content_source_filter'

    def lookups(self, request, model_admin):
        return [
            ("cs-domains", "Content Source Domains"),
            ("non-cs-domains", "Non Content Source Domains"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "cs-domains":
            return queryset.filter(
                pulp_labels__contains={CONTENT_SOURCES_LABEL_NAME: "true"},
            )
        if self.value() == "non-cs-domains":
            return queryset.exclude(
                pulp_labels__contains={CONTENT_SOURCES_LABEL_NAME: "true"},
            )
        return queryset


class DomainOrgAdmin(admin.ModelAdmin):
    list_display = ["user", "org_id", "group"]
    list_filter = ["user", "org_id", "group"]

    def get_queryset(self, request):
        """
        Filter DomainOrg based on user's group memberships and access.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # For staff users, show DomainOrg entries where:
        # 1. User is assigned directly, OR
        # 2. User belongs to the group assigned to the DomainOrg
        user_groups = request.user.groups.all()
        query = Q(user=request.user)

        if user_groups.exists():
            query |= Q(group__in=user_groups)

        return qs.filter(query).distinct()

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "domains":
            if not request.user.is_superuser:
                # Filter domains based on user's accessible DomainOrg entries
                user_groups = request.user.groups.all()
                domain_query = Q(domain_orgs__user=request.user)

                if user_groups.exists():
                    domain_query |= Q(domain_orgs__group__in=user_groups)

                kwargs["queryset"] = Domain.objects.filter(domain_query).distinct().order_by("name")
            else:
                kwargs["queryset"] = Domain.objects.order_by("name")
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user" and not request.user.is_superuser:
            # Staff users can only assign users from their groups
            user_groups = request.user.groups.all()
            if user_groups.exists():
                kwargs["queryset"] = User.objects.filter(groups__in=user_groups).distinct()
            else:
                kwargs["queryset"] = User.objects.filter(pk=request.user.pk)
        elif db_field.name == "group" and not request.user.is_superuser:
            # Staff users can only assign their own groups
            kwargs["queryset"] = request.user.groups.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        """
        Staff users can only modify DomainOrg entries they have access to.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        # Check if user has access to this DomainOrg entry
        user_groups = request.user.groups.all()
        if obj.user == request.user:
            return True

        if user_groups.exists() and obj.group in user_groups:
            return True

        return False

    def has_delete_permission(self, request, obj=None):
        """
        Use same logic as change permission for delete.
        """
        return self.has_change_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        """
        Staff users can view DomainOrg entries based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        """
        Staff users can add DomainOrg entries if they have at least one group.
        """
        if request.user.is_superuser:
            return True

        # Staff users can add if they have groups to work with
        return request.user.groups.exists()

    def has_module_permission(self, request):
        """
        Allow staff users to access the DomainOrg module.
        """
        return request.user.is_staff


class DomainAdmin(admin.ModelAdmin):
    list_display = ["name", "description", "storage_class"]
    list_filter = ["name", "description", "storage_class", ContentSourceDomainFilter]

    def get_queryset(self, request):
        """
        Filter domains based on user's access through DomainOrg relationships.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # Filter domains based on user's DomainOrg associations
        user_groups = request.user.groups.all()
        domain_query = Q(domain_orgs__user=request.user)

        if user_groups.exists():
            domain_query |= Q(domain_orgs__group__in=user_groups)

        return qs.filter(domain_query).distinct()

    def has_change_permission(self, request, obj=None):
        """
        Staff users can only modify domains they have access to.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        # Check if user has access to this domain through DomainOrg
        user_groups = request.user.groups.all()
        domain_query = Q(user=request.user)

        if user_groups.exists():
            domain_query |= Q(group__in=user_groups)

        return DomainOrg.objects.filter(domain_query, domains=obj).exists()

    def has_delete_permission(self, request, obj=None):
        """
        Only superusers can delete domains.
        """
        return request.user.is_superuser

    def has_add_permission(self, request):
        """
        Only superusers can add new domains.
        """
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        """
        Staff users can view domains based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_module_permission(self, request):
        """
        Allow staff users to access the Domain module.
        """
        return request.user.is_staff


admin_site = PulpAdminSite(name="myadmin")

admin_site.register(DomainOrg, DomainOrgAdmin)
#We are replacing the default UserAdmin with our PulpUserAdmin
admin_site.register(User, PulpUserAdmin)
admin_site.register(Group, PulpGroupAdmin) 
admin_site.register(Domain, DomainAdmin)
