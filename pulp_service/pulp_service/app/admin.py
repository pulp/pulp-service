import re
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from hijack.contrib.admin import HijackUserAdminMixin

from pulpcore.app.models import Task
from pulpcore.app.models.role import GroupRole, UserRole
from pulpcore.plugin.models import Domain, Group

from pulp_service.app.constants import CONTENT_SOURCES_LABEL_NAME

USERNAME_PATTERN = r"^[\w.@+=/\-|]+$"
USERNAME_ERROR_MSG = "Username can only contain letters, numbers, and these special characters: @, ., +, -, =, /, _, |"
USERNAME_HELP_TEXT = (
    "Required. 150 characters or fewer. Letters, numbers, and these special characters: @, ., +, -, =, /, _, |"
)


# Override Django's username validator
pulp_username_validator = RegexValidator(USERNAME_PATTERN, USERNAME_ERROR_MSG, "invalid")


# Apply the new validator to the User model.
# NOTE: only mutate attributes that are NOT tracked by Django's migration
# autodetector. `validators` is safe because Field.deconstruct() reads the
# original `_validators`, not this assignment (which only overrides the
# cached `validators` property). Do NOT set `help_text` here: it IS tracked
# by deconstruct(), so mutating it on the vendored auth.User field makes
# `migrate` report "app 'auth' has changes not yet reflected in a migration"
# on every deploy. The user-facing help_text is set at the form level in
# PulpUserFormMixin instead.
User._meta.get_field("username").validators = [pulp_username_validator]


# Custom/Pulp forms to allow additional characters
class PulpUserFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override help_text in the form field
        self.fields["username"].help_text = USERNAME_HELP_TEXT

    def clean_username(self):
        username = self.cleaned_data["username"]
        if not re.match(USERNAME_PATTERN, username):
            raise forms.ValidationError(USERNAME_ERROR_MSG)
        return username


class PulpUserCreationForm(PulpUserFormMixin, UserCreationForm):
    pass


class PulpUserChangeForm(PulpUserFormMixin, UserChangeForm):
    pass


def _userrole_links(user_roles):
    """Render UserRole assignments as links to their admin change pages."""
    return [
        format_html(
            '<a href="{}">User: {} - {}</a>',
            reverse("myadmin:core_userrole_change", args=[user_role.pk]),
            user_role.user.username,
            user_role.role.name,
        )
        for user_role in user_roles
    ]


def _role_scope(role):
    """What a role is scoped to, for display, covering all three cases a role can
    take: ``"domain: <name>"`` for a domain-scoped role (``domain`` FK),
    ``"<type>: <name>"`` for an object-level role (``content_object``, usually a
    Domain here, but may be a repository, group, etc.), or ``"global"`` for a role
    with no scope that applies account-wide. Lets otherwise-identical group+role
    lines be told apart by what they apply to.
    """
    if role.domain_id:
        return f"domain: {role.domain.name}"
    target = role.content_object
    if target is not None:
        name = getattr(target, "name", None) or str(target)
        return f"{role.content_type.model}: {name}"
    return "global"


def _grouprole_links(group_roles, show_scope=False):
    """Render GroupRole assignments as links to their admin change pages. With
    ``show_scope``, append the scope (domain/object/global) each role applies to.
    """
    links = []
    for group_role in group_roles:
        link = format_html(
            '<a href="{}">Group: {} - {}</a>',
            reverse("myadmin:core_grouprole_change", args=[group_role.pk]),
            group_role.group.name,
            group_role.role.name,
        )
        if show_scope:
            link = format_html("{} ({})", link, _role_scope(group_role))
        links.append(link)
    return links


class ReadOnlyRoleInline(admin.TabularInline):
    """Display-only inline of role assignments. Management happens on the
    dedicated UserRole/GroupRole admin pages, so every row is read-only."""

    extra = 0
    can_delete = False
    fields = ["role", "domain", "role_target", "change_link"]
    readonly_fields = ["role", "domain", "role_target", "change_link"]
    verbose_name_plural = "Role assignments"

    @admin.display(description="Target object")
    def role_target(self, obj):
        return obj.content_object or "-"

    @admin.display(description="Edit")
    def change_link(self, obj):
        """Link each row to its own UserRole/GroupRole change page so an admin can
        jump straight from the user/group to manage the assignment."""
        if obj.pk is None:
            return "-"
        url = reverse(f"myadmin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
        return format_html('<a href="{}">Edit</a>', url)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        # Safe default; subclasses widen this to let a non-superuser see the
        # roles on their own User/Group page.
        return request.user.is_superuser


class UserRoleInline(ReadOnlyRoleInline):
    model = UserRole
    fk_name = "user"
    verbose_name_plural = "Direct role assignments"

    def has_view_permission(self, request, obj=None):
        # obj is the parent User. Non-superusers see only their own roles,
        # matching UserRoleAdmin's queryset scoping.
        if request.user.is_superuser:
            return True
        return obj is not None and obj.pk == request.user.pk


class GroupRoleInline(ReadOnlyRoleInline):
    model = GroupRole
    fk_name = "group"

    def has_view_permission(self, request, obj=None):
        # obj is the parent Group. Non-superusers see only roles for groups
        # they belong to, matching GroupRoleAdmin's queryset scoping.
        if request.user.is_superuser:
            return True
        return obj is not None and request.user.groups.filter(pk=obj.pk).exists()


class PulpUserAdmin(HijackUserAdminMixin, UserAdmin):
    form = PulpUserChangeForm
    add_form = PulpUserCreationForm
    inlines = [UserRoleInline]
    readonly_fields = ("roles_via_groups",)
    fieldsets = (*UserAdmin.fieldsets, ("Roles via groups", {"fields": ("roles_via_groups",)}))

    def get_inline_instances(self, request, obj=None):
        # No object yet (add page) means there are no role rows to show.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    @admin.display(description="Roles inherited from group memberships")
    def roles_via_groups(self, obj):
        """Summarize the GroupRole assignments this user inherits through group
        membership and link to the GroupRole changelist filtered to those groups,
        rather than rendering every assignment inline (direct roles are in the
        inline above). The changelist handles sorting, search, and pagination, so
        this scales to a user in an org-wide group that spans many domains."""
        if not obj or not obj.pk:
            return "-"
        group_ids = list(obj.groups.values_list("pk", flat=True))
        count = GroupRole.objects.filter(group_id__in=group_ids).count() if group_ids else 0
        if not count:
            return "-"
        query = urlencode({"group__id__in": ",".join(str(pk) for pk in group_ids)})
        return format_html(
            '{} role assignment{} via {} group{} - <a href="{}?{}">view in GroupRole admin</a>',
            count,
            "" if count == 1 else "s",
            len(group_ids),
            "" if len(group_ids) == 1 else "s",
            reverse("myadmin:core_grouprole_changelist"),
            query,
        )


class PulpGroupForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        widget=admin.widgets.FilteredSelectMultiple("Users", False),
        required=False,
        help_text="Select users to add to this group.",
    )

    class Meta:
        model = Group
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        # Extract the request object if passed
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            # Existing group - populate with current members
            self.fields["users"].initial = self.instance.user_set.all()
        elif self.request and self.request.user.is_authenticated:
            # New group - prepopulate with current user
            self.fields["users"].initial = [self.request.user.pk]
            # Store the current user for validation
            self._current_user = self.request.user

    def clean_users(self):
        users = self.cleaned_data.get("users")

        # For new groups, ensure the creating user is included (unless they're a superuser)
        if (
            not self.instance.pk
            and hasattr(self, "_current_user")
            and not self._current_user.is_superuser
            and self._current_user not in users
        ):
            raise ValidationError(
                f"You must include yourself ({self._current_user.username}) in the group members. "
                "This ensures you maintain access to the group after creation."
            )

        return users

    def save(self, commit=True):
        group = super().save(commit=False)

        def save_m2m():
            group.user_set.set(self.cleaned_data["users"])

        if commit:
            group.save()
            save_m2m()
        else:
            self.save_m2m = save_m2m

        return group


class PulpGroupAdmin(GroupAdmin):
    form = PulpGroupForm
    fields = ("name", "users")  # Show name and users fields
    inlines = [GroupRoleInline]

    def get_queryset(self, request):
        """
        Filter groups based on user's group memberships for non-superusers.
        """
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        # Regular users can only see their own groups
        user_groups = request.user.groups.all()
        return qs.filter(pk__in=user_groups.values_list("pk", flat=True))

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
            form_class.base_fields["users"].queryset = User.objects.all().order_by("username")

        # Create a wrapper to inject request into form instantiation
        class FormWithRequest(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["request"] = request
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
    title = "ContentSource Domains"
    parameter_name = "content_source_filter"

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


# Help text for the interdependent scoping fields on the role add/change forms.
# A role is either object-level (content_type + object_id) or domain-scoped
# (domain), never both; pulpcore provides no help text of its own.
ROLE_FIELD_HELP = {
    "content_type": (
        "The kind of object this role applies to (e.g. domain, pythonrepository, group). "
        "Required when setting 'Object id'; leave blank for a domain-scoped role."
    ),
    "object_id": (
        "The primary key of the Content Type object set above: a UUID for most Pulp objects like a "
        "domain or repository, an integer for Django objects like a group. Leave this and 'Content "
        "type' blank to scope the role to an entire domain using the 'Domain' field instead."
    ),
    "domain": (
        "Scope the role to an entire domain, so it applies to everything in that domain. Use this "
        "instead of Content type/Object id. Leave blank for an object-level or global role."
    ),
}


class RoleAdminForm(forms.ModelForm):
    """Shared form for the UserRole/GroupRole admins. The model allows NULL for the
    scope fields, so the admin would otherwise mark them required. This form makes
    them optional and enforces the scope invariants pulpcore applies in
    ``assign_role`` (which the admin bypasses by writing the row directly):

    * content_type and object_id are set together; a GenericForeignKey can't
      resolve with only one half.
    * domain and the object-level scope (content_type/object_id) are mutually
      exclusive; a role is domain-scoped, object-level, or global, never a mix.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content_type"].required = False
        self.fields["object_id"].required = False
        self.fields["domain"].required = False

    def clean(self):
        cleaned_data = super().clean()
        content_type = cleaned_data.get("content_type")
        object_id = cleaned_data.get("object_id")
        domain = cleaned_data.get("domain")
        if content_type and not object_id:
            self.add_error("object_id", "Required when Content type is set.")
        elif object_id and not content_type:
            self.add_error("content_type", "Required when Object id is set.")
        elif content_type and object_id and not self._object_exists(content_type, object_id):
            # Resolve the id against the selected content type: an orphaned
            # assignment grants no access, and a malformed id for a typed (e.g.
            # UUID) primary key raises during content_object prefetch and breaks
            # the whole changelist.
            self.add_error("object_id", "No object of the selected Content type has this Object id.")
        if domain and (content_type or object_id):
            raise ValidationError(
                "A role is either domain-scoped (set Domain) or object-level (set Content type and "
                "Object id), not both. Clear Domain, or clear Content type and Object id."
            )
        return cleaned_data

    @staticmethod
    def _object_exists(content_type, object_id):
        model = content_type.model_class()
        if model is None:
            return False
        try:
            return model._base_manager.filter(pk=object_id).exists()
        except (ValueError, ValidationError, TypeError):
            # Malformed id for the model's primary-key type (e.g. a non-UUID
            # string for a UUID pk) cannot identify a valid object.
            return False


class UserRoleAdminForm(RoleAdminForm):
    class Meta:
        model = UserRole
        fields = ["user", "role", "domain", "content_type", "object_id"]


class GroupRoleAdminForm(RoleAdminForm):
    class Meta:
        model = GroupRole
        fields = ["group", "role", "domain", "content_type", "object_id"]


class RoleNameListFilter(admin.RelatedFieldListFilter):
    """Role sidebar filter that labels choices with the plain role name instead
    of Role's inherited "<Role: name>" __str__."""

    def field_choices(self, field, request, model_admin):
        return [(role.pk, role.name) for role in field.related_model.objects.order_by("name")]


class RoleAdminMixin:
    """Shared permission rules for role admins: any authenticated user may view,
    only superusers may add/change/delete."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field is not None and db_field.name in ROLE_FIELD_HELP:
            field.help_text = ROLE_FIELD_HELP[db_field.name]
        if field is not None and db_field.name == "role":
            # Show the plain role name in the dropdown; Role's inherited __str__
            # wraps it as "<Role: name>", redundant in a field already labeled Role.
            field.label_from_instance = lambda role: role.name
            # Role defines no default ordering, so the dropdown would otherwise be
            # in arbitrary DB order. Sort by name to match the content_type dropdown
            # and the RoleNameListFilter sidebar.
            field.queryset = field.queryset.order_by("name")
        if field is not None and db_field.name == "content_type":
            # The raw dropdown lists every ContentType in the deployment (hundreds).
            # Narrow it to content types some role grants permissions on, the only
            # valid targets for an object-level role. Coarse (not specific to the
            # role chosen on this same form) but a large usability win that blocks
            # obviously-wrong targets at the admin boundary.
            field.queryset = (
                ContentType.objects.filter(permission__role__isnull=False).distinct().order_by("app_label", "model")
            )
        return field

    @admin.display(description="Role", ordering="role__name")
    def role_name(self, obj):
        """Show the plain role name in the changelist (avoids the "<Role: name>"
        that Role's inherited __str__ would render)."""
        return obj.role.name

    def get_list_filter(self, request):
        # A non-superuser's changelist is already scoped to their own rows; the
        # unfiltered sidebar dropdowns would otherwise enumerate every
        # role/domain name.
        return self.list_filter if request.user.is_superuser else ()

    def has_module_permission(self, request):
        return request.user.is_authenticated and request.user.is_active

    def has_view_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_active

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Target object")
    def content_object(self, obj):
        """Render the role's generic target (``content_object`` is a GFK, which
        Django's admin cannot label directly in ``list_display``)."""
        return obj.content_object or "-"


class UserRoleAdmin(RoleAdminMixin, admin.ModelAdmin):
    form = UserRoleAdminForm
    list_display = ["user", "role_name", "domain", "content_object"]
    list_select_related = ["user", "role", "domain"]  # content_object is a GFK, can't select_related
    list_filter = [("role", RoleNameListFilter), "domain"]
    search_fields = ["user__username", "role__name"]

    def get_queryset(self, request):
        """Non-superusers see only their own role assignments."""
        qs = super().get_queryset(request).prefetch_related("content_object")
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)


class GroupRoleAdmin(RoleAdminMixin, admin.ModelAdmin):
    form = GroupRoleAdminForm
    list_display = ["group", "role_name", "domain", "content_object"]
    list_select_related = ["group", "role", "domain"]  # content_object is a GFK, can't select_related
    list_filter = [("role", RoleNameListFilter), "domain"]
    search_fields = ["group__name", "role__name"]

    def get_queryset(self, request):
        """Non-superusers see only role assignments for groups they belong to."""
        qs = super().get_queryset(request).prefetch_related("content_object")
        if request.user.is_superuser:
            return qs
        return qs.filter(group__in=request.user.groups.all())


class DomainAdminForm(forms.ModelForm):
    class Meta:
        model = Domain
        fields = [
            "name",
            "description",
            "storage_class",
            "storage_settings",
            "redirect_to_object_storage",
            "hide_guarded_distributions",
            "pulp_labels",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make pulp_labels field optional since it can be empty
        self.fields["pulp_labels"].required = False
        self.fields["description"].required = False


class DomainAdmin(admin.ModelAdmin):
    form = DomainAdminForm
    list_display = ["name", "description", "storage_class"]
    list_filter = ["description", "storage_class", ContentSourceDomainFilter]
    search_fields = ["name"]
    readonly_fields = ["domain_url", "role_assignments"]

    def domain_url(self, obj):
        """Display the domain's API URL."""
        api_url = f"/api/pulp/{obj.name}/api/v3/"
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', api_url, api_url)

    @admin.display(description="Role assignments")
    def role_assignments(self, obj):
        """List the UserRole/GroupRole assignments targeting this domain, linked
        to their admin pages. Covers both domain-scoped (``domain`` FK) and
        object-level (``content_object`` is this Domain) roles."""
        domain_ct = ContentType.objects.get_for_model(Domain)
        target = Q(domain=obj) | Q(content_type=domain_ct, object_id=str(obj.pk))
        user_roles = UserRole.objects.filter(target).select_related("user", "role")
        group_roles = GroupRole.objects.filter(target).select_related("group", "role")

        links = _userrole_links(user_roles) + _grouprole_links(group_roles)
        if not links:
            return "-"
        return format_html_join("", "{}<br>", ((link,) for link in links))

    # Domains are superuser-only for now. Editing a domain exposes its storage
    # backend and settings, so until view-vs-change is untangled for RBAC roles
    # (a read-only ``core.domain_viewer`` must not imply write), non-superusers
    # get no Domain visibility here at all.
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_module_permission(self, request):
        return request.user.is_superuser


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
        return obj.pulp_created.strftime("%Y-%m-%d %H:%M:%S.%f %Z") if obj.pulp_created else "-"

    @admin.display(description="Last Updated")
    def pulp_last_updated_display(self, obj):
        """Display pulp_last_updated with full precision."""
        return obj.pulp_last_updated.strftime("%Y-%m-%d %H:%M:%S.%f %Z") if obj.pulp_last_updated else "-"

    @admin.display(description="Unblocked At")
    def unblocked_at_display(self, obj):
        """Display unblocked_at with full precision."""
        return obj.unblocked_at.strftime("%Y-%m-%d %H:%M:%S.%f %Z") if obj.unblocked_at else "-"

    @admin.display(description="Started At")
    def started_at_display(self, obj):
        """Display started_at with full precision."""
        return obj.started_at.strftime("%Y-%m-%d %H:%M:%S.%f %Z") if obj.started_at else "-"

    @admin.display(description="Finished At")
    def finished_at_display(self, obj):
        """Display finished_at with full precision."""
        return obj.finished_at.strftime("%Y-%m-%d %H:%M:%S.%f %Z") if obj.finished_at else "-"

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

admin_site.register(UserRole, UserRoleAdmin)
admin_site.register(GroupRole, GroupRoleAdmin)
admin_site.register(User, PulpUserAdmin)
admin_site.register(Group, PulpGroupAdmin)
admin_site.register(Domain, DomainAdmin)
admin_site.register(Task, TaskAdmin)
