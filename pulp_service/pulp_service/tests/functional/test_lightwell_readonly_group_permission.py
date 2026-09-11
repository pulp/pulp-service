"""
Functional tests for the hardcoded lightwell read-only group's access to the "lightwell"
domain's non-PyPI endpoints (Pulp REST API: repository listing, etc.).

After PULP-2120 the default permission class is PulpServiceAccessPolicy (RBAC). The read-only
group gets read access to the lightwell domain from two RBAC roles migration 0019 assigns it
on that domain: object-level core.domain_viewer (the domain is visible in listings) and
domain-scoped service.domain_viewer (its content is readable). These are view-only roles, so
the group still has no write access, and they do not apply to the lightwell domain's PyPI
views, which remain gated by the lightwell-network feature check (see
test_content_guard_permission.py) -- group membership must not bypass that check.

Because the RBAC roles are assigned by migration 0019 against a domain that already exists,
and these tests create the lightwell domain per-run, the configure_lightwell_domain fixture
assigns the same two roles explicitly (mirroring the migration).

These follow the pattern used in test_group_based_permissions.py (group setup via gen_group
/ UsersApi / GroupsUsersApi) and test_content_guard_permission.py (the "lightwell"
domain/PyPI fixtures).

NOTE: like test_content_guard_permission.py, this is keyed off the literal domain name
"lightwell" (see pulp_service.app.authorization.LIGHTWELL_DOMAIN_NAME), so the domain created
here can't use a random per-test suffix. These tests assume they run against an ephemeral
Pulp instance where no "lightwell" domain already exists, and are not run concurrently with
other tests that also create a "lightwell" domain.
"""

import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests
from django.conf import settings

# An org with no DomainOrg association with the test domain and no lightwell-network
# feature entitlement; only used to own the test domain/repo/distribution.
DOMAIN_OWNER_ORG_ID = "555555555"
# A distinct org used for the group members created in these tests, so they never
# accidentally collide with the domain owner's DomainOrg association.
GROUP_MEMBER_ORG_ID = "666666666"
# See DOMAIN_ACCESS_POLICIES in settings.py
LIGHTWELL_DOMAIN_NAME = "lightwell"


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode()).decode()


def _combined_username(org_id, username):
    """Matches the "{org_id}|{username}" format RHTermsBasedRegistryAuthentication resolves
    identity headers to (see pulp_service.app.authentication)."""
    return f"{org_id}|{username}"


@pytest.fixture
def lightwell_readonly_group(gen_group):
    """The hardcoded read-only group, created with its real (hardcoded) name."""
    policy = getattr(settings, "DOMAIN_ACCESS_POLICIES", {}).get("lightwell", {})
    group = policy.get("readonly_group")
    return gen_group(name=group)


@pytest.fixture
def configure_lightwell_domain(
    anonymous_user,
    gen_object_with_cleanup,
    add_to_cleanup,
    pulpcore_bindings,
    file_bindings,
    python_bindings,
    service_content_guards_api_client,
    bindings_cfg,
    create_service_domain,
    lightwell_readonly_group,
):
    """
    Creates the "lightwell" domain (owned by DOMAIN_OWNER_ORG_ID, no relation to the
    read-only group), with a File repository and a PyPI-distributed Python repository.

    After PULP-2120 the read-only group grants access via RBAC roles rather than a
    DomainBasedPermission special case: this fixture assigns the group the same roles
    migration 0019 grants it on the lightwell domain -- object-level core.domain_viewer
    (so the domain is visible in listings) and domain-scoped service.domain_viewer (so
    members can read content inside the domain). Real deployments get these at migrate
    time; here the domain is created per-test, so the fixture assigns them explicitly.

    Returns (repos_url, pypi_url, owner_header).
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-readonly-test-owner")

    with anonymous_user:
        # After PULP-2120 non-admins create domains via the self-service endpoint, not DomainsApi.
        domain = create_service_domain(LIGHTWELL_DOMAIN_NAME, identity_header=owner_header)

        # monitor_task (used by gen_object_with_cleanup for the async PyPI distribution create
        # below) reads via pulpcore_bindings.TasksApi, which shares this client and needs the
        # identity header while inside anonymous_user (basic auth is stripped there).
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = owner_header

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = owner_header
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=LIGHTWELL_DOMAIN_NAME
        )

        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = owner_header
        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=LIGHTWELL_DOMAIN_NAME
        )

        from pulpcore.client.pulp_service import ServiceFeatureContentGuard

        service_content_guards_api_client.api_client.default_headers["x-rh-identity"] = owner_header
        guard = service_content_guards_api_client.create(
            service_feature_content_guard=ServiceFeatureContentGuard(
                name=f"lightwell-guard-{uuid4()}",
                header_name="x-rh-identity",
                features=["lightwell-network"],
                jq_filter=".identity.org_id",
            ),
            pulp_domain=LIGHTWELL_DOMAIN_NAME,
        )
        add_to_cleanup(service_content_guards_api_client, guard.pulp_href)

        python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = owner_header
        pypi_base_path = str(uuid4())
        gen_object_with_cleanup(
            python_bindings.DistributionsPypiApi,
            {
                "name": str(uuid4()),
                "base_path": pypi_base_path,
                "repository": repo.pulp_href,
                "content_guard": guard.pulp_href,
            },
            pulp_domain=LIGHTWELL_DOMAIN_NAME,
        )

    # Assign the read-only group the same two roles migration 0019 grants it on the lightwell
    # domain: object-level core.domain_viewer (so the domain shows up in listings) and
    # domain-scoped service.domain_viewer (so members can read content inside the domain).
    # Done as admin, so drop the owner identity header the async creates above left on the
    # shared pulpcore client. The unused key of each pair must be an explicit None (the API
    # rejects it being omitted), matching pulpcore's own gen_user role helpers.
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    pulpcore_bindings.GroupsRolesApi.create(
        lightwell_readonly_group.pulp_href,
        group_role={"role": "core.domain_viewer", "domain": None, "content_object": domain.pulp_href},
    )
    pulpcore_bindings.GroupsRolesApi.create(
        lightwell_readonly_group.pulp_href,
        group_role={"role": "service.domain_viewer", "domain": domain.pulp_href, "content_object": None},
    )

    repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{LIGHTWELL_DOMAIN_NAME}/api/v3/repositories/file/file/")
    pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{LIGHTWELL_DOMAIN_NAME}/{pypi_base_path}/simple/")

    yield repos_url, pypi_url, owner_header

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)
    service_content_guards_api_client.api_client.default_headers.pop("x-rh-identity", None)


@pytest.fixture
def gen_readonly_group_member(pulpcore_bindings, gen_object_with_cleanup, lightwell_readonly_group):
    """Creates a user that's a member of the hardcoded read-only group, and returns an
    x-rh-identity header for that user."""

    def _gen_member(username_suffix):
        username = f"readonly-member-{username_suffix}-{uuid4()}"
        combined_username = _combined_username(GROUP_MEMBER_ORG_ID, username)
        gen_object_with_cleanup(
            pulpcore_bindings.UsersApi,
            {"username": combined_username},
        )
        gen_object_with_cleanup(
            pulpcore_bindings.GroupsUsersApi,
            group_href=lightwell_readonly_group.pulp_href,
            group_user={"username": combined_username},
        )
        return _identity_header(GROUP_MEMBER_ORG_ID, username)

    return _gen_member


def test_readonly_group_member_can_read_lightwell_repositories(configure_lightwell_domain, gen_readonly_group_member):
    """A user with no DomainOrg association, whose only access path is the read-only group's
    RBAC roles on the lightwell domain, can list repositories in the lightwell domain and
    actually sees them (the domain-scoped service.domain_viewer role scopes them in)."""
    repos_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": gen_readonly_group_member("read")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 200
    assert response.json()["count"] >= 1


def test_non_member_denied_reading_lightwell_repositories(
    configure_lightwell_domain, gen_object_with_cleanup, pulpcore_bindings
):
    """A user with no DomainOrg association and no read-only group membership has no RBAC
    role granting content access. Under the RBAC default the request is not denied outright;
    it returns 200 with an empty, scoped list (no repositories leak)."""
    repos_url, _, _ = configure_lightwell_domain
    username = f"non-member-{uuid4()}"
    combined_username = _combined_username(GROUP_MEMBER_ORG_ID, username)
    gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": combined_username})
    headers = {"x-rh-identity": _identity_header(GROUP_MEMBER_ORG_ID, username)}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_readonly_group_member_write_denied(configure_lightwell_domain, gen_readonly_group_member):
    """The read-only group grants no write access -- a member must still be denied when
    trying to create a repository in the lightwell domain."""
    repos_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": gen_readonly_group_member("write")}

    response = requests.post(repos_url, headers=headers, json={"name": str(uuid4())}, timeout=30)

    assert response.status_code in (401, 403)


def test_readonly_group_member_pypi_still_requires_feature(configure_lightwell_domain, gen_readonly_group_member):
    """Read-only group membership does not bypass the lightwell-network feature check on
    PyPI views -- a member with no feature entitlement and no DomainOrg association still
    gets 403 on the PyPI simple API."""
    _, pypi_url, _ = configure_lightwell_domain
    headers = {"x-rh-identity": gen_readonly_group_member("pypi")}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_readonly_group_member_denied_on_other_domains(
    pulpcore_bindings,
    anonymous_user,
    gen_readonly_group_member,
    bindings_cfg,
    create_service_domain,
):
    """The read-only group's RBAC roles are scoped to the lightwell domain, so they grant no
    access to other domains. Listing another domain's repositories returns 200 with an empty,
    scoped list (no repositories leak)."""
    other_domain_owner_header = _identity_header("777777777", "other-domain-owner")
    domain_name = f"not-lightwell-{uuid4()}"

    with anonymous_user:
        # After PULP-2120 non-admins create domains via the self-service endpoint, not DomainsApi.
        create_service_domain(domain_name, identity_header=other_domain_owner_header)
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/repositories/file/file/")
    headers = {"x-rh-identity": gen_readonly_group_member("other-domain")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_readonly_group_member_sees_lightwell_domain_in_listing(
    configure_lightwell_domain, gen_readonly_group_member, pulpcore_bindings, anonymous_user
):
    """The lightwell domain shows up in GET /domains/ for read-only group members, via the
    object-level core.domain_viewer role the group holds on the domain (scoped in by
    PulpServiceAccessPolicy.scope_queryset())."""
    del configure_lightwell_domain  # ensure the "lightwell" domain exists
    member_header = gen_readonly_group_member("domain-list")

    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = member_header
        response = pulpcore_bindings.DomainsApi.list()

    domain_names = {domain.name for domain in response.results}
    assert LIGHTWELL_DOMAIN_NAME in domain_names
