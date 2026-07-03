"""
Functional tests for the hardcoded LIGHTWELL_READONLY_GROUP_NAME group enforced by
DomainBasedPermission, scoped specifically to the "lightwell" domain's non-PyPI endpoints
(Pulp REST API: repository listing, etc.).

Unlike normal groups (created through CreateDomainView's group_name flow and linked to a
domain via DomainOrg, which grant unrestricted read+write access), membership in this
hardcoded group grants read-only (SAFE_METHODS) access to the lightwell domain, independent
of any DomainOrg association. It does not grant write access, and it does not apply to the
lightwell domain's PyPI views, which remain gated exclusively by the lightwell-network
feature check (see test_lightwell_feature_permission.py) -- group membership must not bypass
that check.

These follow the pattern used in test_group_based_permissions.py (group setup via gen_group
/ UsersApi / GroupsUsersApi) and test_lightwell_feature_permission.py (the "lightwell"
domain/PyPI fixtures).

NOTE: like test_lightwell_feature_permission.py, this is keyed off the literal domain name
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

from pulp_service.app.authorization import LIGHTWELL_READONLY_GROUP_NAME

LIGHTWELL_DOMAIN_NAME = "lightwell"

# An org with no DomainOrg association with the test domain and no lightwell-network
# feature entitlement; only used to own the test domain/repo/distribution.
DOMAIN_OWNER_ORG_ID = "555555555"
# A distinct org used for the group members created in these tests, so they never
# accidentally collide with the domain owner's DomainOrg association.
GROUP_MEMBER_ORG_ID = "666666666"


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
    return gen_group(name=LIGHTWELL_READONLY_GROUP_NAME)


@pytest.fixture
def configure_lightwell_domain(
    anonymous_user,
    gen_object_with_cleanup,
    pulpcore_bindings,
    file_bindings,
    python_bindings,
    bindings_cfg,
):
    """
    Creates the "lightwell" domain (owned by DOMAIN_OWNER_ORG_ID, no relation to the
    read-only group), with a File repository and a PyPI-distributed Python repository.

    Returns (repos_url, pypi_url, owner_header).
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-readonly-test-owner")

    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = owner_header

        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": LIGHTWELL_DOMAIN_NAME,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = owner_header
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=LIGHTWELL_DOMAIN_NAME
        )

        python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = owner_header
        repo = gen_object_with_cleanup(
            python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=LIGHTWELL_DOMAIN_NAME
        )

        python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = owner_header
        pypi_base_path = str(uuid4())
        gen_object_with_cleanup(
            python_bindings.DistributionsPypiApi,
            {"name": str(uuid4()), "base_path": pypi_base_path, "repository": repo.pulp_href},
            pulp_domain=LIGHTWELL_DOMAIN_NAME,
        )

    repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{LIGHTWELL_DOMAIN_NAME}/api/v3/repositories/file/file/")
    pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{LIGHTWELL_DOMAIN_NAME}/{pypi_base_path}/simple/")

    yield repos_url, pypi_url, owner_header

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


@pytest.fixture
def gen_readonly_group_member(pulpcore_bindings, gen_object_with_cleanup, lightwell_readonly_group):
    """Creates a user that's a member of the hardcoded read-only group, and returns an
    x-rh-identity header for that user."""

    def _gen_member(username_suffix):
        username = f"readonly-member-{username_suffix}-{uuid4()}"
        combined_username = _combined_username(GROUP_MEMBER_ORG_ID, username)
        gen_object_with_cleanup(
            pulpcore_bindings.UsersApi,
            {"username": combined_username, "groups": [lightwell_readonly_group.pulp_href]},
        )
        return _identity_header(GROUP_MEMBER_ORG_ID, username)

    return _gen_member


def test_readonly_group_member_can_read_lightwell_repositories(configure_lightwell_domain, gen_readonly_group_member):
    """A user with no DomainOrg association, whose only access path is membership in the
    hardcoded read-only group, can list repositories in the lightwell domain."""
    repos_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": gen_readonly_group_member("read")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_non_member_denied_reading_lightwell_repositories(
    configure_lightwell_domain, gen_object_with_cleanup, pulpcore_bindings
):
    """A user with no DomainOrg association and no read-only group membership gets 403 when
    listing repositories in the lightwell domain."""
    repos_url, _, _ = configure_lightwell_domain
    username = f"non-member-{uuid4()}"
    combined_username = _combined_username(GROUP_MEMBER_ORG_ID, username)
    gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": combined_username})
    headers = {"x-rh-identity": _identity_header(GROUP_MEMBER_ORG_ID, username)}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 403


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
    pulpcore_bindings, anonymous_user, gen_object_with_cleanup, gen_readonly_group_member, bindings_cfg
):
    """Membership in the lightwell read-only group grants no access to domains other than
    lightwell."""
    other_domain_owner_header = _identity_header("777777777", "other-domain-owner")
    domain_name = f"not-lightwell-{uuid4()}"

    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = other_domain_owner_header
        gen_object_with_cleanup(
            pulpcore_bindings.DomainsApi,
            {
                "name": domain_name,
                "storage_class": "pulpcore.app.models.storage.FileSystem",
                "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
            },
        )
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/repositories/file/file/")
    headers = {"x-rh-identity": gen_readonly_group_member("other-domain")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_readonly_group_member_sees_lightwell_domain_in_listing(
    configure_lightwell_domain, gen_readonly_group_member, bindings_cfg
):
    """The lightwell domain shows up in GET /domains/ for read-only group members, via
    DomainBasedPermission.scope_queryset()."""
    del configure_lightwell_domain  # ensure the "lightwell" domain exists
    headers = {"x-rh-identity": gen_readonly_group_member("domain-list")}
    domains_url = urljoin(bindings_cfg.host, "/api/pulp/api/v3/domains/")

    response = requests.get(domains_url, headers=headers, timeout=30)

    assert response.status_code == 200
    domain_names = {domain["name"] for domain in response.json()["results"]}
    assert LIGHTWELL_DOMAIN_NAME in domain_names
