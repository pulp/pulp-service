"""
Functional tests for the content-guard-driven access check enforced by DomainBasedPermission
on PyPI views. When a PyPI distribution has a content guard, SAFE_METHOD access is gated
by guard.cast().permit(request). Distributions without content guards keep the pre-existing
open-access behavior.

These follow the pattern used in test_feature_service.py: they exercise the real Features
Service (no mocking) using known staging accounts. Org LIGHTWELL_ENTITLED_ORG_ID has the
lightwell-network feature; org LIGHTWELL_NOT_ENTITLED_ORG_ID does not.

The check is domain-name-agnostic: any domain's distributions can be protected by configuring
a content guard, with no code changes.
"""

import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests

from pulp_service.tests.functional.constants import (
    LIGHTWELL_ENTITLED_ORG_ID,
    LIGHTWELL_NETWORK_FEATURE,
    LIGHTWELL_NOT_ENTITLED_ORG_ID,
)

# An org with no DomainOrg association with the test domains and no lightwell-network
# feature entitlement; only used to own the test domain/repo/distribution.
DOMAIN_OWNER_ORG_ID = "555555555"


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
def configure_pypi_distribution(
    anonymous_user,
    gen_object_with_cleanup,
    add_to_cleanup,
    pulpcore_bindings,
    python_bindings,
    service_content_guards_api_client,
    bindings_cfg,
):
    """
    Creates a domain owned by DOMAIN_OWNER_ORG_ID, with a Python repository and a PyPI
    distribution.
    Optionally assigns a FeatureContentGuard with the given features.

    Returns a (domain_name, pypi_simple_url, repos_url, owner_header) tuple.
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-test-owner")

    def _configure(domain_name, features=None):
        with anonymous_user:
            pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = owner_header

            gen_object_with_cleanup(
                pulpcore_bindings.DomainsApi,
                {
                    "name": domain_name,
                    "storage_class": "pulpcore.app.models.storage.FileSystem",
                    "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
                },
            )

            python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = owner_header
            repo = gen_object_with_cleanup(
                python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
            )

            distro_params = {"name": str(uuid4()), "base_path": str(uuid4()), "repository": repo.pulp_href}
            if features is not None:
                from pulpcore.client.pulp_service import ServiceFeatureContentGuard

                service_content_guards_api_client.api_client.default_headers["x-rh-identity"] = owner_header
                guard = service_content_guards_api_client.create(
                    service_feature_content_guard=ServiceFeatureContentGuard(
                        name=f"guard-{uuid4()}",
                        header_name="x-rh-identity",
                        features=features,
                        jq_filter=".identity.org_id",
                    ),
                    pulp_domain=domain_name,
                )
                add_to_cleanup(service_content_guards_api_client, guard.pulp_href)
                distro_params["content_guard"] = guard.pulp_href

            python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = owner_header
            gen_object_with_cleanup(
                python_bindings.DistributionsPypiApi,
                distro_params,
                pulp_domain=domain_name,
            )

        base_path = distro_params["base_path"]
        pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{domain_name}/{base_path}/simple/")
        repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/repositories/python/python/")
        return domain_name, pypi_url, repos_url, owner_header

    yield _configure

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)
    service_content_guards_api_client.api_client.default_headers.pop("x-rh-identity", None)


@pytest.fixture
def configure_guarded_pypi_distribution(configure_pypi_distribution):
    """Creates a domain with a FeatureContentGuard requiring the lightwell-network feature
    on its PyPI distribution. Uses a unique domain name."""

    def _configure():
        domain_name = f"guarded-{uuid4()}"
        return configure_pypi_distribution(domain_name, features=[LIGHTWELL_NETWORK_FEATURE])

    return _configure


def test_org_without_feature_denied_on_guarded_pypi_simple_api(configure_guarded_pypi_distribution):
    """A user whose org doesn't have the required feature and has no DomainOrg
    association gets 403 on a content-guarded PyPI simple API."""
    _, pypi_url, _, _ = configure_guarded_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_NOT_ENTITLED_ORG_ID, "not-entitled-user")}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_org_with_feature_allowed_on_guarded_pypi_simple_api(configure_guarded_pypi_distribution):
    """A user whose org has the required feature can read a content-guarded PyPI
    simple API, even without a DomainOrg association."""
    _, pypi_url, _, _ = configure_guarded_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-user")}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_domain_org_association_bypasses_content_guard(configure_guarded_pypi_distribution):
    """The domain owner (has a DomainOrg association) can read the guarded PyPI
    simple API regardless of the content guard."""
    _, pypi_url, _, owner_header = configure_guarded_pypi_distribution()
    headers = {"x-rh-identity": owner_header}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_unauthenticated_denied_on_guarded_pypi_simple_api(configure_guarded_pypi_distribution):
    """Without any identity at all, a content-guarded PyPI simple API must not be readable."""
    _, pypi_url, _, _ = configure_guarded_pypi_distribution()

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code in (401, 403)


def test_write_operations_unaffected_by_content_guard(configure_guarded_pypi_distribution):
    """The content guard only gates SAFE_METHOD access -- an entitled org with no
    DomainOrg association must still be denied write access."""
    _, pypi_url, _, _ = configure_guarded_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-write-user")}

    response = requests.post(pypi_url, headers=headers, data={}, timeout=30)

    assert response.status_code in (401, 403)


def test_non_pypi_endpoints_unaffected_by_content_guard(configure_guarded_pypi_distribution):
    """Non-PyPI endpoints (here, the Pulp REST API's repository listing) must keep using the
    existing DomainOrg-based permission model: the content guard on a PyPI distribution
    grants no access to non-PyPI endpoints."""
    _, _, repos_url, _ = configure_guarded_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-rest-user")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_unguarded_distribution_allows_unauthenticated_access(configure_pypi_distribution):
    """Distributions without content guards allow any SAFE_METHOD request, included
    unauthenticated -- the pre-existing behavior."""
    domain_name = f"unguarded-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code == 200


def test_public_domain_allows_unauthenticated_pypi_access(configure_pypi_distribution):
    """A public- domain's PyPI simple API stays open to unauthenticated SAFE_METHOD
    requests -- unaffected by content guards."""
    domain_name = f"public-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code == 200
