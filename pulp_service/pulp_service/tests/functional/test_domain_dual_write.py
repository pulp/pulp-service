import json
from base64 import b64encode
from types import SimpleNamespace
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests
from django.db.models.deletion import ProtectedError

from pulpcore.app.models import CompositeContentGuard, HeaderContentGuard
from pulpcore.plugin.models import Domain

from pulp_service.app.constants import (
    DEFAULT_IDENTITY_CONTENT_GUARD_NAME,
    DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME,
    DEFAULT_VPN_CONTENT_GUARD_NAME,
    LEGACY_DEFAULT_IDENTITY_CONTENT_GUARD_NAME,
    VPN_VERIFIED_HEADER_NAME,
)

# These tests exercise the RBAC dual-write in the post_create_domain signal
# (pulp_service/app/signals.py) across both entry points:
# - the generic DomainsApi, which runs in autocommit -- the path where a failed
#   dual-write could orphan the Domain (domain_committed_standalone=True);
# - the self-service create-domain endpoint (CreateDomainView), the real
#   production path, which sets group_var and wraps the save in
#   transaction.atomic() so the signal nests as a savepoint
#   (domain_committed_standalone=False, the explicit_group branch).
#
# Domain listing works via the pre-existing DomainBasedPermission (org_id match)
# regardless of RBAC, so it cannot prove the dual-write. Instead we introspect the
# role assignments the signal writes. There is no DomainOrg API; since the role
# writes and the DomainOrg insert share one transaction.atomic() in the signal,
# asserting the roles committed transitively confirms the DomainOrg row too.
#
# Not covered here: the delete-on-failure rollback path. It needs fault injection
# that is brittle over black-box HTTP -- better suited to a unit test with mocking.

ORG_ID = 1


def _auth_header(identity):
    return b64encode(bytes(json.dumps(identity), "ascii"))


def _identity(username, org_id=ORG_ID):
    return {"identity": {"org_id": org_id, "internal": {"org_id": org_id}, "user": {"username": username}}}


def _user_role_assignments(pulpcore_bindings, username, org_id=ORG_ID):
    """Role assignments for the auth-flow user auto-created as "<org_id>|<username>"."""
    users = pulpcore_bindings.UsersApi.list(username=f"{org_id}|{username}")
    assert users.count == 1, f"expected exactly one user {org_id}|{username}, got {users.count}"
    return pulpcore_bindings.UsersRolesApi.list(users.results[0].pulp_href).results


def _group_role_assignments(pulpcore_bindings, group_href):
    return pulpcore_bindings.GroupsRolesApi.list(group_href).results


def _assert_domain_guard_bundle(domain_name):
    domain = Domain.objects.get(name=domain_name)
    identity_guard = domain.default_content_guard.cast()
    assert identity_guard.name == DEFAULT_IDENTITY_CONTENT_GUARD_NAME
    assert identity_guard.header_name == "x-rh-identity"
    assert identity_guard.header_value == ""
    assert identity_guard.jq_filter == '""'

    vpn_guard = HeaderContentGuard.objects.get(pulp_domain=domain, name=DEFAULT_VPN_CONTENT_GUARD_NAME)
    assert vpn_guard.header_name == VPN_VERIFIED_HEADER_NAME
    assert vpn_guard.header_value == "true"
    assert vpn_guard.jq_filter is None

    composite_guard = CompositeContentGuard.objects.get(
        pulp_domain=domain,
        name=DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME,
    )
    assert set(composite_guard.guards.values_list("pk", flat=True)) == {identity_guard.pk, vpn_guard.pk}
    return domain, identity_guard, vpn_guard, composite_guard


def _assert_owns_domain(assignments, domain_href):
    """Assert the two-role pair the signal writes for an entity on one domain.

    - core.domain_owner: object-level, asserted ON the domain (content_object)
    - service.domain_admin: domain-scoped, asserted on objects inside it (domain)
    """
    owner = any(a.role == "core.domain_owner" and a.content_object == domain_href for a in assignments)
    admin = any(a.role == "service.domain_admin" and a.domain == domain_href for a in assignments)
    assert owner, f"missing object-level core.domain_owner on {domain_href}"
    assert admin, f"missing domain-scoped service.domain_admin on {domain_href}"


def _create_domain(pulpcore_bindings, gen_object_with_cleanup, anonymous_user, username):
    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = _auth_header(_identity(username))
        domain = gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": str(uuid4()),
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )
    # Role introspection needs admin auth, not the creator's identity.
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    return domain


def test_dual_write_grants_creator_and_org_group(pulpcore_bindings, anonymous_user, gen_object_with_cleanup):
    """Creator with no team group: creator gets direct roles, rh-org-<org_id> gets the pair."""
    username = str(uuid4())
    domain = _create_domain(pulpcore_bindings, gen_object_with_cleanup, anonymous_user, username)

    _assert_owns_domain(_user_role_assignments(pulpcore_bindings, username), domain.pulp_href)

    org_groups = pulpcore_bindings.GroupsApi.list(name=f"rh-org-{ORG_ID}")
    assert org_groups.count == 1, f"expected the signal to create group rh-org-{ORG_ID}"
    _assert_owns_domain(_group_role_assignments(pulpcore_bindings, org_groups.results[0].pulp_href), domain.pulp_href)


def test_dual_write_grants_team_group(pulpcore_bindings, anonymous_user, gen_group, gen_object_with_cleanup):
    """Creator in a team group: the team group AND the org group get the pair, creator keeps direct roles."""
    team_group = gen_group(name=f"test-team-{uuid4()}")

    username = str(uuid4())
    gen_object_with_cleanup(
        pulpcore_bindings.UsersApi,
        {"username": f"{ORG_ID}|{username}", "groups": [team_group.pulp_href]},
    )
    gen_object_with_cleanup(
        pulpcore_bindings.GroupsUsersApi,
        group_href=team_group.pulp_href,
        group_user={"username": f"{ORG_ID}|{username}"},
    )

    domain = _create_domain(pulpcore_bindings, gen_object_with_cleanup, anonymous_user, username)

    # Creator always gets direct roles, even when the domain is group-scoped (signals.py divergence).
    _assert_owns_domain(_user_role_assignments(pulpcore_bindings, username), domain.pulp_href)
    _assert_owns_domain(_group_role_assignments(pulpcore_bindings, team_group.pulp_href), domain.pulp_href)

    org_groups = pulpcore_bindings.GroupsApi.list(name=f"rh-org-{ORG_ID}")
    assert org_groups.count == 1
    _assert_owns_domain(_group_role_assignments(pulpcore_bindings, org_groups.results[0].pulp_href), domain.pulp_href)


@pytest.fixture
def template_domain_s3(pulpcore_bindings, gen_object_with_cleanup):
    """Ensure the 'template-domain-s3' domain CreateDomainView copies storage from exists.

    Real environments create this out of band; on a bare test stack we create it with
    FileSystem storage (not real S3) so the copied storage_class works locally. Created
    as plain admin with no x-rh-identity, so the dual-write signal no-ops for it.
    """
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    existing = pulpcore_bindings.DomainsApi.list(name="template-domain-s3")
    if existing.count:
        return existing.results[0]
    return gen_object_with_cleanup(
        pulpcore_bindings.DomainsApi,
        {
            "name": "template-domain-s3",
            "storage_class": "pulpcore.app.models.storage.FileSystem",
            "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
        },
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("template_domain_s3")
def test_dual_write_self_service_create_domain(pulpcore_bindings, bindings_cfg, request):
    """Self-service create-domain endpoint: creator, the group_name group, and the org group get the pair.

    This is the production path (CreateDomainView) that sets group_var and wraps the save
    in transaction.atomic(), exercising the signal's explicit_group +
    domain_committed_standalone=False branches the generic-DomainsApi tests never touch.
    """
    username = str(uuid4())
    team_name = f"self-service-team-{uuid4()}"
    domain_name = str(uuid4())

    resp = requests.post(
        urljoin(bindings_cfg.host, "/api/pulp/create-domain/"),
        headers={"x-rh-identity": _auth_header(_identity(username)).decode()},
        json={"name": domain_name, "group_name": team_name},
        timeout=30,
    )
    assert resp.status_code == 201, f"create-domain failed: {resp.status_code} {resp.text}"
    domain_href = resp.json()["pulp_href"]
    _assert_domain_guard_bundle(domain_name)

    # No binding created this domain/group, so register admin-auth cleanup ourselves.
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    request.addfinalizer(lambda: pulpcore_bindings.DomainsApi.delete(domain_href))
    team_group = pulpcore_bindings.GroupsApi.list(name=team_name).results[0]
    request.addfinalizer(lambda: pulpcore_bindings.GroupsApi.delete(team_group.pulp_href))

    _assert_owns_domain(_user_role_assignments(pulpcore_bindings, username), domain_href)
    _assert_owns_domain(_group_role_assignments(pulpcore_bindings, team_group.pulp_href), domain_href)

    org_groups = pulpcore_bindings.GroupsApi.list(name=f"rh-org-{ORG_ID}")
    assert org_groups.count == 1
    _assert_owns_domain(_group_role_assignments(pulpcore_bindings, org_groups.results[0].pulp_href), domain_href)


@pytest.mark.django_db
def test_domain_with_automatic_identity_guard_bundle_can_be_deleted(
    pulpcore_bindings, anonymous_user, gen_object_with_cleanup, monitor_task
):
    """A new domain gets the guard bundle and can be deleted with no content references."""
    domain_name = str(uuid4())
    auth_header = _auth_header(_identity(str(uuid4())))
    delete_task = None

    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = auth_header
        try:
            domain = gen_object_with_cleanup(
                pulpcore_bindings.DomainsApi,
                {
                    "name": domain_name,
                    "storage_class": "pulpcore.app.models.storage.FileSystem",
                    "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
                },
            )
            _assert_domain_guard_bundle(domain_name)

            delete_task = pulpcore_bindings.DomainsApi.delete(domain.pulp_href).task
        finally:
            pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    result = monitor_task(delete_task)
    assert result.state == "completed"


@pytest.mark.django_db
def test_legacy_identity_guard_is_cleaned_up_on_domain_delete():
    domain = Domain.objects.create(
        name=f"legacy-{uuid4()}",
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/"},
    )
    legacy_guard = HeaderContentGuard.objects.create(
        name=LEGACY_DEFAULT_IDENTITY_CONTENT_GUARD_NAME,
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=domain,
    )
    domain.default_content_guard = legacy_guard
    domain.save(update_fields=["default_content_guard"])

    domain.delete()

    assert not Domain.objects.filter(pk=domain.pk).exists()
    assert not HeaderContentGuard.objects.filter(pulp_domain_id=domain.pk).exists()
    assert not CompositeContentGuard.objects.filter(pulp_domain_id=domain.pk).exists()


@pytest.mark.django_db
def test_public_domain_has_no_automatic_content_guards():
    domain = Domain.objects.create(
        name=f"public-{uuid4()}",
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/"},
    )

    try:
        assert domain.default_content_guard_id is None
        assert not HeaderContentGuard.objects.filter(pulp_domain=domain).exists()
        assert not CompositeContentGuard.objects.filter(pulp_domain=domain).exists()
    finally:
        domain.delete()


@pytest.mark.django_db
def test_composite_allows_identity_or_verified_vpn():
    domain = Domain.objects.create(
        name=f"composite-{uuid4()}",
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/"},
    )
    _, _, _, composite_guard = _assert_domain_guard_bundle(domain.name)

    try:
        composite_guard.permit(SimpleNamespace(headers={VPN_VERIFIED_HEADER_NAME: "dHJ1ZQ=="}))
        composite_guard.permit(
            SimpleNamespace(
                headers={
                    "x-rh-identity": _auth_header({"identity": {"org_id": "1"}}).decode(),
                }
            )
        )

        with pytest.raises(PermissionError):
            composite_guard.permit(SimpleNamespace(headers={}))
        with pytest.raises(PermissionError):
            composite_guard.permit(SimpleNamespace(headers={VPN_VERIFIED_HEADER_NAME: "ZmFsc2U="}))
    finally:
        domain.delete()


@pytest.mark.django_db
def test_customer_guard_protects_domain_and_rolls_back_default_guard_cleanup():
    """Failed domain deletion preserves both customer and automatic guards."""
    domain = Domain.objects.create(
        name=f"delete-{uuid4()}",
        storage_class="pulpcore.app.models.storage.FileSystem",
        storage_settings={"MEDIA_ROOT": "/var/lib/pulp/media/"},
    )
    _, identity_guard, vpn_guard, composite_guard = _assert_domain_guard_bundle(domain.name)
    customer_guard = HeaderContentGuard.objects.create(
        name="customer-managed",
        header_name="x-customer-token",
        header_value="expected",
        pulp_domain=domain,
    )

    try:
        with pytest.raises(ProtectedError):
            domain.delete()

        assert Domain.objects.filter(pk=domain.pk).exists()
        assert HeaderContentGuard.objects.filter(pk=identity_guard.pk).exists()
        assert HeaderContentGuard.objects.filter(pk=vpn_guard.pk).exists()
        assert CompositeContentGuard.objects.filter(pk=composite_guard.pk).exists()
        assert HeaderContentGuard.objects.filter(pk=customer_guard.pk).exists()
    finally:
        customer_guard.delete()
        domain.delete()
