import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests

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
