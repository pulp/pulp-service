"""Unit tests for the role-based Django admin (``pulp_service.app.admin``).

These drive the admin ``ModelAdmin`` classes, forms, and pages directly against a
transactional test database (no live stack): a ``RequestFactory`` request for the
RBAC scoping of the domain and role changelists, the admin forms for scope
validation, and a ``Client`` for the User/Group/Domain page rendering.
"""

from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory
from django.urls import reverse

from pulpcore.app.models.role import GroupRole, Role, UserRole
from pulpcore.plugin.models import Domain, Group
from pulpcore.plugin.util import assign_role

from pulp_service.app.admin import (
    DomainAdmin,
    GroupRoleAdmin,
    PulpUserAdmin,
    UserRoleAdmin,
    UserRoleAdminForm,
    admin_site,
)

pytestmark = pytest.mark.django_db

factory = RequestFactory()


def _request(user):
    request = factory.get("/")
    request.user = user
    return request


def _domain(name):
    """Minimal Domain for scoping tests. ``objects.create`` skips ``full_clean``,
    so only the storage fields need valid values; nothing touches the filesystem."""
    return Domain.objects.create(
        name=name,
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/", "location": "/var/lib/pulp/media/"},
    )


def test_grouprole_admin_scoped_to_membership():
    """GroupRoleAdmin shows a non-superuser only the role assignments of groups
    they belong to; a user outside the group must not see them, and superusers
    see all. Mirrors the UserRole scoping already covered for the per-user case.
    """
    suffix = uuid4().hex[:10]
    domain = _domain(f"grprole-{suffix}")
    member = User.objects.create(username=f"grprole-in-{suffix}")
    nonmember = User.objects.create(username=f"grprole-out-{suffix}")
    group = Group.objects.create(name=f"grprole-grp-{suffix}")
    group.user_set.add(member)
    su = User.objects.create(username=f"grprole-su-{suffix}", is_superuser=True)
    role = Role.objects.get(name="core.domain_owner")
    grouprole = GroupRole.objects.create(group=group, role=role, domain=domain)

    gr_admin = GroupRoleAdmin(GroupRole, admin_site)

    assert grouprole in gr_admin.get_queryset(_request(member)), (
        "a group member must see their group's role assignments"
    )
    assert grouprole not in gr_admin.get_queryset(_request(nonmember)), (
        "a non-member must not see another group's role assignments"
    )
    assert grouprole in gr_admin.get_queryset(_request(su)), "superuser must see all group role assignments"


def test_domain_admin_superuser_only():
    """Domains are superuser-only for now. A non-superuser gets no Domain
    visibility, view, or change permission even when they hold a role on a
    domain, so a read-only ``core.domain_viewer`` can never reach the editable
    storage fields. Superusers retain full access.
    """
    suffix = uuid4().hex[:10]
    held = _domain(f"domscope-held-{suffix}")
    user = User.objects.create(username=f"domscope-{suffix}")
    assign_role("core.domain_viewer", user, held)
    su = User.objects.create(username=f"domscope-su-{suffix}", is_superuser=True)

    domain_admin = DomainAdmin(Domain, admin_site)
    req = _request(user)

    assert not domain_admin.has_module_permission(req), "a non-superuser must not reach the Domain module"
    assert not domain_admin.has_view_permission(req, held), "a role on a domain must not grant view access"
    assert not domain_admin.has_change_permission(req, held), "a role on a domain must not grant change access"
    assert not domain_admin.has_add_permission(req), "only superusers may add domains"
    assert not domain_admin.has_delete_permission(req, held), "only superusers may delete domains"

    su_req = _request(su)
    assert domain_admin.has_module_permission(su_req), "superuser must reach the Domain module"
    assert held.name in set(domain_admin.get_queryset(su_req).values_list("name", flat=True)), (
        "superuser must see all domains"
    )
    assert domain_admin.has_change_permission(su_req, held), "superuser must be able to change domains"


def test_roles_via_groups_summarizes_and_links_to_filtered_changelist():
    """Inherited group roles render as a one-line summary plus a link to the
    GroupRole changelist filtered to the user's groups, rather than every
    assignment inline. This is what keeps an org-wide group spanning many domains
    from blowing up the User page: the count and a link, not N repeated rows."""
    suffix = uuid4().hex[:10]
    domain_a = _domain(f"viagrp-a-{suffix}")
    domain_b = _domain(f"viagrp-b-{suffix}")
    user = User.objects.create(username=f"viagrp-{suffix}")
    group = Group.objects.create(name=f"viagrp-grp-{suffix}")
    group.user_set.add(user)
    owner = Role.objects.get(name="core.domain_owner")
    viewer = Role.objects.get(name="core.domain_viewer")
    # Two roles on domain A, one on domain B: three assignments, all via one group.
    GroupRole.objects.create(group=group, role=owner, domain=domain_a)
    GroupRole.objects.create(group=group, role=viewer, domain=domain_a)
    GroupRole.objects.create(group=group, role=owner, domain=domain_b)

    html = str(PulpUserAdmin(User, admin_site).roles_via_groups(user))

    assert "3 role assignments via 1 group" in html, "summary states the assignment and group counts"
    # The link targets the GroupRole changelist, filtered to exactly this user's group.
    changelist = reverse("myadmin:core_grouprole_changelist")
    assert f'href="{changelist}?group__id__in={group.pk}"' in html, "link filters the changelist to the user's groups"
    assert html.isascii(), "summary uses only regular-keyboard characters"


def test_roles_via_groups_singular_grammar():
    """A single assignment via a single group reads in the singular (no stray 's')."""
    suffix = uuid4().hex[:10]
    domain = _domain(f"viagrp-one-{suffix}")
    user = User.objects.create(username=f"viagrp-one-{suffix}")
    group = Group.objects.create(name=f"viagrp-one-grp-{suffix}")
    group.user_set.add(user)
    GroupRole.objects.create(group=group, role=Role.objects.get(name="core.domain_owner"), domain=domain)

    html = str(PulpUserAdmin(User, admin_site).roles_via_groups(user))

    assert "1 role assignment via 1 group" in html, "singular grammar for a single assignment/group"
    assert "assignments" not in html and "groups" not in html, "no plural 's' in the singular case"


def test_roles_via_groups_empty_when_no_inherited_roles():
    """No groups, or groups with no roles, render '-' rather than an empty link."""
    suffix = uuid4().hex[:10]
    admin = PulpUserAdmin(User, admin_site)
    loner = User.objects.create(username=f"viagrp-none-{suffix}")
    assert admin.roles_via_groups(loner) == "-", "a user in no groups has nothing inherited"

    member = User.objects.create(username=f"viagrp-empty-{suffix}")
    empty_group = Group.objects.create(name=f"viagrp-empty-grp-{suffix}")
    empty_group.user_set.add(member)
    assert admin.roles_via_groups(member) == "-", "a group with no roles contributes nothing"

    assert admin.roles_via_groups(None) == "-", "no object (add page) renders '-'"


def test_role_admin_form_rejects_nonexistent_object_id():
    """The role admin form resolves object_id against the selected content type: a
    nonexistent id (and a malformed one for a typed primary key) is rejected rather
    than saved as an orphaned assignment that would also break the changelist."""
    suffix = uuid4().hex[:10]
    user = User.objects.create(username=f"objid-{suffix}")
    role = Role.objects.get(name="core.domain_owner")
    domain_ct = ContentType.objects.get_for_model(Domain)
    base = {"user": user.pk, "role": role.pk, "content_type": domain_ct.pk}

    missing = UserRoleAdminForm(data={**base, "object_id": str(uuid4())})
    assert not missing.is_valid() and "object_id" in missing.errors, "nonexistent object id is rejected"
    malformed = UserRoleAdminForm(data={**base, "object_id": "not-a-uuid"})
    assert not malformed.is_valid() and "object_id" in malformed.errors, "malformed object id for a UUID pk is rejected"


def test_role_admin_list_filter_hidden_for_nonsuperuser():
    """A non-superuser's role changelist must expose no sidebar filters; those
    dropdowns would otherwise enumerate every role and domain name in the
    deployment. Superusers keep the full filter set.
    """
    suffix = uuid4().hex[:10]
    user = User.objects.create(username=f"listfilter-{suffix}")
    su = User.objects.create(username=f"listfilter-su-{suffix}", is_superuser=True)

    ur = UserRoleAdmin(UserRole, admin_site)
    gr = GroupRoleAdmin(GroupRole, admin_site)

    assert list(ur.get_list_filter(_request(user))) == [], "non-superuser must get no UserRole sidebar filters"
    assert list(gr.get_list_filter(_request(user))) == [], "non-superuser must get no GroupRole sidebar filters"
    assert len(list(ur.get_list_filter(_request(su)))) > 0, "superuser keeps the UserRole sidebar filters"
    assert len(list(gr.get_list_filter(_request(su)))) > 0, "superuser keeps the GroupRole sidebar filters"


def test_userrole_admin_scope_and_permission_matrix():
    """UserRoleAdmin scopes the changelist to the requesting user's own role
    entries (superusers see all), and any authenticated user may view while only
    superusers may add/change/delete.
    """
    suffix = uuid4().hex[:10]
    domain = _domain(f"urole-{suffix}")
    user = User.objects.create(username=f"urole-{suffix}")
    other = User.objects.create(username=f"urole-other-{suffix}")
    su = User.objects.create(username=f"urole-su-{suffix}", is_superuser=True)
    assign_role("core.domain_owner", user, domain)
    assign_role("core.domain_owner", other, domain)

    ur = UserRoleAdmin(UserRole, admin_site)
    req = _request(user)

    own = ur.get_queryset(req)
    assert own.exists() and all(r.user_id == user.id for r in own), "non-superuser sees only their own role entries"
    assert ur.get_queryset(_request(su)).count() >= own.count(), "superuser sees at least as many role entries"
    assert ur.has_view_permission(req), "an authenticated non-superuser may view role entries"
    assert not ur.has_add_permission(req), "non-superuser must not add role entries"
    assert not ur.has_change_permission(req), "non-superuser must not change role entries"
    assert not ur.has_delete_permission(req), "non-superuser must not delete role entries"
    assert ur.has_add_permission(_request(su)) and ur.has_change_permission(_request(su)), "superuser may manage"


def test_userrole_admin_form_scope_validation():
    """The UserRole admin form treats the scope fields as optional (a domain-scoped
    role is valid), requires content_type/object_id as a pair, and rejects setting
    both a domain and an object-level scope.
    """
    suffix = uuid4().hex[:10]
    domain = _domain(f"uform-{suffix}")
    user = User.objects.create(username=f"uform-{suffix}")
    role = Role.objects.get(name="core.domain_owner")
    domain_ct = ContentType.objects.get_for_model(Domain)
    base = {"user": user.pk, "role": role.pk}

    assert UserRoleAdminForm(data={**base, "domain": domain.pk}).is_valid(), "role+domain is valid (domain-scoped)"
    object_level = UserRoleAdminForm(data={**base, "content_type": domain_ct.pk, "object_id": str(domain.pk)})
    assert object_level.is_valid(), "role+content_type+object_id is valid (object-level)"
    ct_only = UserRoleAdminForm(data={**base, "content_type": domain_ct.pk})
    assert not ct_only.is_valid() and "object_id" in ct_only.errors, "content_type without object_id is rejected"
    id_only = UserRoleAdminForm(data={**base, "object_id": str(domain.pk)})
    assert not id_only.is_valid() and "content_type" in id_only.errors, "object_id without content_type is rejected"
    both = UserRoleAdminForm(
        data={**base, "domain": domain.pk, "content_type": domain_ct.pk, "object_id": str(domain.pk)}
    )
    assert not both.is_valid(), "domain and object-level scope are mutually exclusive"


def test_user_group_domain_pages_render_role_sections():
    """The User change page shows direct roles inline plus the 'Roles via groups'
    section; the Group page shows its role inline; the Domain page lists the
    UserRole/GroupRole assignments targeting it. Rendered via the admin Client.
    """
    suffix = uuid4().hex[:10]
    domain = _domain(f"uxpage-{suffix}")
    user = User.objects.create(username=f"uxpage-{suffix}")
    group = Group.objects.create(name=f"uxpage-grp-{suffix}")
    group.user_set.add(user)
    su = User.objects.create(username=f"uxpage-su-{suffix}", is_superuser=True)
    role = Role.objects.get(name="core.domain_owner")
    assign_role("core.domain_owner", user, domain)
    GroupRole.objects.create(group=group, role=role, domain=domain)

    client = Client()
    client.force_login(su)
    user_page = client.get(reverse("myadmin:auth_user_change", args=[user.pk])).content.decode()
    group_page = client.get(reverse("myadmin:core_group_change", args=[group.pk])).content.decode()
    domain_page = client.get(reverse("myadmin:core_domain_change", args=[domain.pk])).content.decode()

    assert "Direct role assignments" in user_page, "User page must show a direct role assignments inline"
    assert "Roles via groups" in user_page and "core.domain_owner" in user_page, "User page must list inherited roles"
    assert "Role assignments" in group_page and "core.domain_owner" in group_page, "Group page must show a role inline"
    assert "core.domain_owner" in domain_page and user.username in domain_page and group.name in domain_page, (
        "Domain page must list the user and group role assignments targeting it"
    )
