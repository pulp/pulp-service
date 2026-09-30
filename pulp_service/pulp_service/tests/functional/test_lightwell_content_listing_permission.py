"""
Functional tests for the subscription-based content listing access check enforced by
PulpServiceAccessPolicy (via DOMAIN_ACCESS_POLICIES) on content listing APIs in the
lightwell domain. When the authenticated user's org has the lightwell-network feature
(verified via Features Service), SAFE_METHOD access is granted to content listing endpoints
(/api/v3/content/...) even without an RBAC role on the domain or Lightwell-ReadOnly group
membership.

These follow the pattern used in test_content_guard_permission.py: they exercise the real
Features Service (no mocking) using known staging accounts. Org LIGHTWELL_ENTITLED_ORG_ID
has the lightwell-network feature; org LIGHTWELL_NOT_ENTITLED_ORG_ID does not.

NOTE: like test_lightwell_readonly_group_permission.py, this is keyed off the literal
domain name "lightwell" (see pulp_service.app.authorization.LIGHTWELL_DOMAIN_NAME), so the
domain created here can't use a random per-test suffix. These tests assume they run against
an ephemeral Pulp instance where no "lightwell" domain already exists, and are not run
concurrently with other tests that also create a "lightwell" domain.
"""

import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests

from pulp_service.tests.functional.constants import (
    LIGHTWELL_ENTITLED_ORG_ID,
    LIGHTWELL_NOT_ENTITLED_ORG_ID,
)

# See DOMAIN_ACCESS_POLICIES in settings.py
LIGHTWELL_DOMAIN_NAME = "lightwell"
LIGHTWELL_READONLY_GROUP_NAME = "Lightwell-ReadOnly"

DOMAIN_OWNER_ORG_ID = "555555555"
GROUP_MEMBER_ORG_ID = "666666666"


def _combined_username(org_id, username):
    return f"{org_id}|{username}"


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode()).decode()


@pytest.fixture
def configure_lightwell_domain(
    anonymous_user,
    gen_object_with_cleanup,
    pulpcore_bindings,
    file_bindings,
    bindings_cfg,
    create_service_domain,
):
    """
    Creates the "lightwell" domain (owned by DOMAIN_OWNER_ORG_ID), with a File repository.

    Returns (content_url, repos_url, owner_header).
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-content-listing-test-owner")

    with anonymous_user:
        # Under RBAC non-admins create domains via the self-service endpoint, not DomainsApi.
        create_service_domain(LIGHTWELL_DOMAIN_NAME, identity_header=owner_header)

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = owner_header
        gen_object_with_cleanup(
            file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=LIGHTWELL_DOMAIN_NAME
        )

    content_url = urljoin(bindings_cfg.host, f"/api/pulp/{LIGHTWELL_DOMAIN_NAME}/api/v3/content/file/files/")
    repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{LIGHTWELL_DOMAIN_NAME}/api/v3/repositories/file/file/")

    yield content_url, repos_url, owner_header

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)


def test_entitled_org_can_list_content(configure_lightwell_domain):
    """A user whose org has the lightwell-network feature can list content in the lightwell
    domain, even without a DomainOrg association or read-only group membership."""
    content_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-content-user")}

    response = requests.get(content_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_non_entitled_org_denied_content_listing(configure_lightwell_domain):
    """A user whose org does not have the lightwell-network feature and holds no RBAC role gets
    403 on content listing in the lightwell domain. Under RBAC the typed content-list policy
    (ACCESS_POLICIES["content/file/files"]) gates list on has_domain_perms:core.view_content, so a
    caller without that permission and without a subscription/group grant is denied outright."""
    content_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_NOT_ENTITLED_ORG_ID, "not-entitled-content-user")}

    response = requests.get(content_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_entitled_org_denied_on_non_content_endpoints(configure_lightwell_domain):
    """The subscription check only applies to content listing endpoints -- an entitled org
    with no RBAC role on the domain gets no repositories. Under RBAC the repository list allows
    any authenticated user and scopes results by role, so the entitled-but-unroled caller sees
    an empty 200 (the subscription grant doesn't extend to repository listing), not a 403."""
    _, repos_url, _ = configure_lightwell_domain
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-repo-user")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_unauthenticated_denied_content_listing(configure_lightwell_domain):
    """Without any identity header, content listing in the lightwell domain is denied."""
    content_url, _, _ = configure_lightwell_domain

    response = requests.get(content_url, timeout=30)

    assert response.status_code in (401, 403)


def test_domain_owner_can_list_content(configure_lightwell_domain):
    """The domain owner (via DomainOrg association) can list content regardless of
    subscription status."""
    content_url, _, owner_header = configure_lightwell_domain
    headers = {"x-rh-identity": owner_header}

    response = requests.get(content_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_entitled_org_post_denied_on_content_endpoint(configure_lightwell_domain):
    """A subscribed user cannot POST to a content listing endpoint -- the subscription check
    only applies to SAFE_METHODS (GET/HEAD/OPTIONS), so the POST falls through to the typed
    content-create policy.

    That policy never yields a clean 403 for this request: pulpcore's create conditions
    (has_required_repo_perms_on_upload / has_upload_param_model_or_domain_or_obj_perms) call
    serializer.is_valid(raise_exception=True) before deciding authorization, so the empty body
    400s during permission evaluation. The assertion is therefore "POST is rejected, not
    granted" -- a subscription grant would only ever turn a SAFE read into a 200, never a POST
    into a 2xx."""
    content_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-post-user")}

    response = requests.post(content_url, headers=headers, json={}, timeout=30)

    assert response.status_code in (400, 401, 403)


@pytest.fixture
def lightwell_readonly_group(gen_group):
    return gen_group(name=LIGHTWELL_READONLY_GROUP_NAME)


@pytest.fixture
def gen_readonly_group_member(pulpcore_bindings, gen_object_with_cleanup, lightwell_readonly_group):
    def _gen_member(username_suffix):
        username = f"readonly-member-{username_suffix}-{uuid4()}"
        combined_username = _combined_username(GROUP_MEMBER_ORG_ID, username)
        gen_object_with_cleanup(
            pulpcore_bindings.UsersApi,
            {"username": combined_username},
        )
        pulpcore_bindings.GroupsUsersApi.create(
            group_href=lightwell_readonly_group.pulp_href,
            group_user={"username": combined_username},
        )
        return _identity_header(GROUP_MEMBER_ORG_ID, username)

    return _gen_member


def test_readonly_group_member_without_subscription_can_list_content(
    configure_lightwell_domain, gen_readonly_group_member
):
    """A user in the Lightwell-ReadOnly group whose org does not have the lightwell-network
    feature can still list content via the group-based fallback path."""
    content_url, _, _ = configure_lightwell_domain
    headers = {"x-rh-identity": gen_readonly_group_member("content-list")}

    response = requests.get(content_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_entitled_org_denied_on_non_lightwell_domain(
    pulpcore_bindings, file_bindings, anonymous_user, gen_object_with_cleanup, bindings_cfg, create_service_domain
):
    """A subscribed user hitting /api/v3/content/ on a non-lightwell domain without an RBAC role
    gets 403 -- the subscription check is scoped to the lightwell domain name, so off that domain
    the caller falls through to the typed content-list policy, which gates list on
    has_domain_perms:core.view_content; lacking that permission, the read is denied."""
    other_domain_owner_header = _identity_header("777777777", "other-domain-owner")
    domain_name = f"not-lightwell-{uuid4()}"

    with anonymous_user:
        # Under RBAC non-admins create domains via the self-service endpoint, not DomainsApi.
        create_service_domain(domain_name, identity_header=other_domain_owner_header)

        file_bindings.RepositoriesFileApi.api_client.default_headers["x-rh-identity"] = other_domain_owner_header
        gen_object_with_cleanup(file_bindings.RepositoriesFileApi, {"name": str(uuid4())}, pulp_domain=domain_name)

    content_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/content/file/files/")
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-wrong-domain-user")}

    response = requests.get(content_url, headers=headers, timeout=30)

    assert response.status_code == 403

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)
