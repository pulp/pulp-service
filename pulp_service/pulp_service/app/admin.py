from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib import admin
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from django import forms
from django.core.validators import RegexValidator

from hijack.contrib.admin import HijackUserAdminMixin

from .models import DomainOrg
import re

from pulpcore.plugin.models import Domain, Group
from pulpcore.app.models import Task
from pulp_service.app.constants import CONTENT_SOURCES_LABEL_NAME

USERNAME_PATTERN = r'^[\w.@+=/\-|]+$'
USERNAME_ERROR_MSG = "Username can only contain letters, numbers, and these special characters: @, ., +, -, =, /, _, |"
USERNAME_HELP_TEXT = "Required. 150 characters or fewer. Letters, numbers, and these special characters: @, ., +, -, =, /, _, |"


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

class PulpUserFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields['username'].help_text = USERNAME_HELP_TEXT
        
    def clean_username(self):
        username = self.cleaned_data['username']
        if not re.match(USERNAME_PATTERN, username):
            raise forms.ValidationError(USERNAME_ERROR_MSG)
        return username

class PulpUserCreationForm(UserCreationForm, PulpUserFormMixin):
    pass


class PulpUserChangeForm(UserChangeForm, PulpUserFormMixin):
    pass


class PulpUserAdmin(HijackUserAdminMixin, UserAdmin):
    form = PulpUserChangeForm
    add_form = PulpUserCreationForm


class PulpGroupForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        widget=admin.widgets.FilteredSelectMultiple('Users', False),
        required=False,
        help_text='Select users to add to this group.'
    )

    class Meta:
        model = Group
        fields = ['name']

    def __init__(self, *args, **kwargs):
        # Extract the request object if passed
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            # Existing group - populate with current members
            self.fields['users'].initial = self.instance.user_set.all()
        elif self.request and self.request.user.is_authenticated:
            # New group - prepopulate with current user
            self.fields['users'].initial = [self.request.user.pk]
            # Store the current user for validation
            self._current_user = self.request.user

    def clean_users(self):
        users = self.cleaned_data.get('users')

        # For new groups, ensure the creating user is included (unless they're a superuser)
        if not self.instance.pk and hasattr(self, '_current_user'):
            if not self._current_user.is_superuser and self._current_user not in users:
                raise ValidationError(
                    f'You must include yourself ({self._current_user.username}) in the group members. '
                    'This ensures you maintain access to the group after creation.'
                )

        return users

    def save(self, commit=True):
        group = super().save(commit=False)

        def save_m2m():
            group.user_set.set(self.cleaned_data['users'])

        if commit:
            group.save()
            save_m2m()
        else:
            self.save_m2m = save_m2m

        return group


class PulpGroupAdmin(GroupAdmin):
    form = PulpGroupForm
    fields = ('name', 'users')  # Show name and users fields

    def get_queryset(self, request):
        """
        Filter groups based on user's group memberships for non-superusers.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # Regular users can only see their own groups
        user_groups = request.user.groups.all()
        return qs.filter(pk__in=user_groups.values_list('pk', flat=True))

    def has_change_permission(self, request, obj=None):
        """
        Users can only modify groups they belong to (including adding/removing other users).
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return request.user.groups.filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        """
        Only superusers and group members can delete groups, based on has_change_permission.
        """
        return request.user.is_superuser or self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        """
        Allow any authenticated user to add a new group.
        """
        return request.user.is_authenticated and request.user.is_active

    def has_view_permission(self, request, obj=None):
        """
        Authenticated users can view groups based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        """
        Customize the form based on user permissions and pass request to form.
        """
        form_class = super().get_form(request, obj, **kwargs)

        if not request.user.is_superuser:
            # Non-superusers can see all users in the form
            # but can only save groups they belong to (checked in has_change_permission)
            form_class.base_fields['users'].queryset = User.objects.all().order_by('username')

        # Create a wrapper to inject request into form instantiation
        class FormWithRequest(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs['request'] = request
                super().__init__(*args, **form_kwargs)

        return FormWithRequest

    def has_module_permission(self, request):
        """
        Allow any authenticated user to access the Group module.
        """
        return request.user.is_authenticated and request.user.is_active

class PulpAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        """
        This override allows non-staff users to login into pulp-mgmt.
        """
        super().confirm_login_allowed(user)

class PulpAdminSite(admin.AdminSite):
    site_header = "Pulp administration"
    login_form = PulpAuthenticationForm

    def has_permission(self, request):
        """
        Allow any authenticated user to access admin.
        """
        return request.user.is_authenticated and request.user.is_active


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


class DomainOrgForm(forms.ModelForm):
    class Meta:
        model = DomainOrg
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make user and group field optional since it can be null
        self.fields['org_id'].required = False
        self.fields['user'].required = False
        self.fields['group'].required = False


class DomainOrgAdmin(admin.ModelAdmin):
    form = DomainOrgForm
    list_display = ["user", "org_id", "group", "domains_display"]
    list_filter = ["user", "org_id", "group"]

    def domains_display(self, obj):
        """Display related domains for this DomainOrg with links to detail view."""
        domains = obj.domains.all()
        if not domains:
            return "-"

        links = []
        for domain in domains:
            url = reverse('admin:core_domain_change', args=[domain.pk])
            label = domain.name if domain.name else "Unnamed domain"
            links.append(format_html('<a href="{}">{}</a>', url, label))

        return format_html(', '.join(links))

    def get_queryset(self, request):
        """
        Filter DomainOrg based on user's group memberships and access.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # For common users, show DomainOrg entries where:
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
            # Regular users can only assign users from their groups
            user_groups = request.user.groups.all()
            if user_groups.exists():
                kwargs["queryset"] = User.objects.filter(groups__in=user_groups).distinct()
            else:
                kwargs["queryset"] = User.objects.filter(pk=request.user.pk)
        elif db_field.name == "group" and not request.user.is_superuser:
            # Regular users can only assign their own groups
            kwargs["queryset"] = request.user.groups.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        """
        Regular users can only modify DomainOrg entries they have access to.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        if obj.user == request.user:
            return True

        # Check if user has access to this DomainOrg entry
        user_groups = request.user.groups.all()

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
        Regular users can view DomainOrg entries based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        """
        Only superusers can add new DomainOrg entries.
        """
        return request.user.is_superuser

    def has_module_permission(self, request):
        """
        Allow any authenticated user to access the DomainOrg module.
        """
        return request.user.is_authenticated and request.user.is_active


class DomainAdminForm(forms.ModelForm):
    class Meta:
        model = Domain
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make pulp_labels field optional since it can be empty
        self.fields["pulp_labels"].required = False
        self.fields["description"].required = False


class DomainAdmin(admin.ModelAdmin):
    form = DomainAdminForm
    list_display = ["name", "description", "storage_class", "domain_orgs_display"]
    list_filter = ["description", "storage_class", ContentSourceDomainFilter]
    search_fields = ["name"]
    readonly_fields = ["domain_url", "domain_orgs_detail"]

    def domain_url(self, obj):
        """Display the domain's API URL."""
        api_url = f"/api/pulp/{obj.name}/api/v3/"
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', api_url, api_url)

    def domain_orgs_display(self, obj):
        """Display related DomainOrg entries for this domain with links."""
        domain_orgs = obj.domain_orgs.all()
        if not domain_orgs:
            return "-"

        links = []
        for domain_org in domain_orgs:
            url = reverse('myadmin:service_domainorg_change', args=[domain_org.pk])
            parts = []
            if domain_org.org_id:
                parts.append(f"Org: {domain_org.org_id}")
            if domain_org.user:
                parts.append(f"User: {domain_org.user.username}")
            if domain_org.group:
                parts.append(f"Group: {domain_org.group.name}")

            label = " | ".join(parts) if parts else f"DomainOrg #{domain_org.pk}"
            links.append(format_html('<a href="{}">{}</a>', url, label))

        return format_html('<br>'.join(links))

    def domain_orgs_detail(self, obj):
        """Display related DomainOrg entries with links in detail view."""
        domain_orgs = obj.domain_orgs.all()
        if not domain_orgs:
            return "-"

        links = []
        for domain_org in domain_orgs:
            url = reverse('myadmin:service_domainorg_change', args=[domain_org.pk])
            parts = []
            if domain_org.org_id:
                parts.append(f"Org: {domain_org.org_id}")
            if domain_org.user:
                parts.append(f"User: {domain_org.user.username}")
            if domain_org.group:
                parts.append(f"Group: {domain_org.group.name}")

            label = " | ".join(parts) if parts else f"DomainOrg #{domain_org.pk}"
            links.append(format_html('<a href="{}">{}</a>', url, label))

        return format_html('<br>'.join(links))

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
        Regular users can only modify domains they have access to.
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
        Regular users can view domains based on same rules as change permission.
        """
        if request.user.is_superuser:
            return True

        if obj is None:
            return True

        return self.has_change_permission(request, obj)

    def has_module_permission(self, request):
        """
        Allow any authenticated user to access the Domain module.
        """
        return request.user.is_authenticated and request.user.is_active


class TaskAdmin(admin.ModelAdmin):
    """
    Admin interface for Task model - restricted to superusers only.
    Only the state field is editable.
    """
    list_display = ["pk", "name", "state", "domain_name", "pulp_created"]
    list_filter = ["state", "pulp_domain", "pulp_created", "started_at"]
    search_fields = ["name", "pk"]

    # Only show date fields, name, state, uuid, and domain on detail page
    fields = [
        "pk",
        "name",
        "state",
        "pulp_domain",
        "pulp_created_display",
        "pulp_last_updated_display",
        "unblocked_at_display",
        "started_at_display",
        "finished_at_display",
    ]

    # Make all fields readonly except state
    readonly_fields = [
        "pk",
        "name",
        "pulp_domain",
        "pulp_created_display",
        "pulp_last_updated_display",
        "unblocked_at_display",
        "started_at_display",
        "finished_at_display",
    ]

    @admin.display(description="Domain", ordering="pulp_domain__name")
    def domain_name(self, obj):
        """Display just the domain name."""
        return obj.pulp_domain.name if obj.pulp_domain else "-"

    @admin.display(description="Created")
    def pulp_created_display(self, obj):
        """Display pulp_created with full precision."""
        return obj.pulp_created.strftime('%Y-%m-%d %H:%M:%S.%f %Z') if obj.pulp_created else "-"

    @admin.display(description="Last Updated")
    def pulp_last_updated_display(self, obj):
        """Display pulp_last_updated with full precision."""
        return obj.pulp_last_updated.strftime('%Y-%m-%d %H:%M:%S.%f %Z') if obj.pulp_last_updated else "-"

    @admin.display(description="Unblocked At")
    def unblocked_at_display(self, obj):
        """Display unblocked_at with full precision."""
        return obj.unblocked_at.strftime('%Y-%m-%d %H:%M:%S.%f %Z') if obj.unblocked_at else "-"

    @admin.display(description="Started At")
    def started_at_display(self, obj):
        """Display started_at with full precision."""
        return obj.started_at.strftime('%Y-%m-%d %H:%M:%S.%f %Z') if obj.started_at else "-"

    @admin.display(description="Finished At")
    def finished_at_display(self, obj):
        """Display finished_at with full precision."""
        return obj.finished_at.strftime('%Y-%m-%d %H:%M:%S.%f %Z') if obj.finished_at else "-"

    def has_view_permission(self, request, obj=None):
        """Only superusers can view tasks."""
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        """Only superusers can change tasks."""
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        """Only superusers can delete tasks."""
        return request.user.is_superuser

    def has_add_permission(self, request):
        """Only superusers can add tasks."""
        return request.user.is_superuser

    def has_module_permission(self, request):
        """Only superusers can access the Task module."""
        return request.user.is_superuser


admin_site = PulpAdminSite(name="myadmin")

admin_site.register(DomainOrg, DomainOrgAdmin)
admin_site.register(User, PulpUserAdmin)
admin_site.register(Group, PulpGroupAdmin)
admin_site.register(Domain, DomainAdmin)
admin_site.register(Task, TaskAdmin)
