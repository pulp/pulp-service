"""
Functional tests for the lightwell-network feature check enforced by DomainBasedPermission,
scoped specifically to the "lightwell" domain's PyPI views (simple API, package metadata,
etc.). Other domains' PyPI views, and all non-PyPI endpoints (even within the lightwell
domain), keep the pre-existing permission model unaffected by this feature check.

These follow the pattern used in test_feature_service.py: they exercise the real Features
Service (no mocking) using known staging accounts. Org LIGHTWELL_ENTITLED_ORG_ID has the
lightwell-network feature; org LIGHTWELL_NOT_ENTITLED_ORG_ID does not.

NOTE: the feature check is keyed off the literal domain name "lightwell" (see
pulp_service.app.authorization.LIGHTWELL_DOMAIN_NAME), so the domain created here can't use
a random per-test suffix like most other functional tests in this suite. These tests assume
they run against an ephemeral Pulp instance where no "lightwell" domain already exists.
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

LIGHTWELL_DOMAIN_NAME = "lightwell"

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
    pulpcore_bindings,
    python_bindings,
    bindings_cfg,
):
    """
    Creates a domain owned by DOMAIN_OWNER_ORG_ID, with a Python repository and a PyPI
    distribution.

    Returns a (domain_name, pypi_simple_url, repos_url, owner_header) tuple.
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-test-owner")

    def _configure(domain_name):
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

            python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = owner_header
            base_path = str(uuid4())
            gen_object_with_cleanup(
                python_bindings.DistributionsPypiApi,
                {"name": str(uuid4()), "base_path": base_path, "repository": repo.pulp_href},
                pulp_domain=domain_name,
            )

        pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{domain_name}/{base_path}/simple/")
        repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/repositories/python/python/")
        return domain_name, pypi_url, repos_url, owner_header

    yield _configure

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
    python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)


@pytest.fixture
def configure_lightwell_pypi_distribution(configure_pypi_distribution):
    """Same as configure_pypi_distribution, but uses the literal "lightwell" domain name that
    DomainBasedPermission gates behind the lightwell-network feature."""

    def _configure():
        return configure_pypi_distribution(LIGHTWELL_DOMAIN_NAME)

    return _configure


def test_org_without_feature_denied_on_lightwell_pypi_simple_api(configure_lightwell_pypi_distribution):
    """A user whose org doesn't have the lightwell-network feature and has no DomainOrg
    association gets 403 on the lightwell domain's PyPI simple API."""
    _, pypi_url, _, _ = configure_lightwell_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_NOT_ENTITLED_ORG_ID, "not-entitled-user")}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_org_with_feature_allowed_on_lightwell_pypi_simple_api(configure_lightwell_pypi_distribution):
    """A user whose org has the lightwell-network feature can read the lightwell domain's
    PyPI simple API, even without a DomainOrg association."""
    _, pypi_url, _, _ = configure_lightwell_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-user")}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_domain_org_association_bypasses_feature_check(configure_lightwell_pypi_distribution):
    """The domain owner (has a DomainOrg association) can read the lightwell domain's PyPI
    simple API regardless of the lightwell-network feature."""
    _, pypi_url, _, owner_header = configure_lightwell_pypi_distribution()
    headers = {"x-rh-identity": owner_header}

    response = requests.get(pypi_url, headers=headers, timeout=30)

    assert response.status_code == 200


def test_unauthenticated_denied_on_lightwell_pypi_simple_api(configure_lightwell_pypi_distribution):
    """Without any identity at all (no org_id to check a feature for), the lightwell domain's
    PyPI simple API must not be readable."""
    _, pypi_url, _, _ = configure_lightwell_pypi_distribution()

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code in (401, 403)


def test_write_operations_unaffected_by_feature_check(configure_lightwell_pypi_distribution):
    """The lightwell-network feature only grants read access -- an entitled org with no
    DomainOrg association must still be denied write access to the lightwell domain's PyPI
    views."""
    _, pypi_url, _, _ = configure_lightwell_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-write-user")}

    response = requests.post(pypi_url, headers=headers, data={}, timeout=30)

    assert response.status_code in (401, 403)


def test_non_pypi_endpoints_unaffected_by_feature_check(configure_lightwell_pypi_distribution):
    """Non-PyPI endpoints (here, the Pulp REST API's repository listing) must keep using the
    existing DomainOrg-based permission model, even within the lightwell domain: the
    lightwell-network feature grants no access to them."""
    _, _, repos_url, _ = configure_lightwell_pypi_distribution()
    headers = {"x-rh-identity": _identity_header(LIGHTWELL_ENTITLED_ORG_ID, "entitled-rest-user")}

    response = requests.get(repos_url, headers=headers, timeout=30)

    assert response.status_code == 403


def test_other_domains_pypi_simple_api_unaffected_by_feature_check(configure_pypi_distribution):
    """Domains other than "lightwell" must keep the pre-existing behavior: any SAFE_METHOD
    request -- even unauthenticated -- can read the PyPI simple API, regardless of the
    lightwell-network feature."""
    domain_name = f"not-lightwell-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code == 200


def test_public_domain_allows_unauthenticated_pypi_access(configure_pypi_distribution):
    """A public- domain's PyPI simple API stays open to unauthenticated SAFE_METHOD
    requests -- unaffected by the lightwell-network feature check (which never applies to
    public- domains, or to non-"lightwell" domains in general)."""
    domain_name = f"public-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code == 200
