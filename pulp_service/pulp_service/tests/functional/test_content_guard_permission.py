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

from pulpcore.tests.functional.utils import PulpTaskError

from pulp_service.app.constants import DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME, DEFAULT_VPN_CONTENT_GUARD_NAME
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
def configure_pypi_distribution(request):  # noqa: PLR0915 - fixture orchestrates multi-step API setup and cleanup
    """
    Creates a domain owned by DOMAIN_OWNER_ORG_ID, with a Python repository and a PyPI
    distribution.
    Optionally assigns a FeatureContentGuard with the given features or the domain's
    identity-or-VPN composite guard.

    Returns a (domain_name, pypi_simple_url, repos_url, owner_header) tuple.
    """
    anonymous_user = request.getfixturevalue("anonymous_user")
    gen_object_with_cleanup = request.getfixturevalue("gen_object_with_cleanup")
    add_to_cleanup = request.getfixturevalue("add_to_cleanup")
    create_service_domain = request.getfixturevalue("create_service_domain")
    pulpcore_bindings = request.getfixturevalue("pulpcore_bindings")
    python_bindings = request.getfixturevalue("python_bindings")
    service_content_guards_api_client = request.getfixturevalue("service_content_guards_api_client")
    bindings_cfg = request.getfixturevalue("bindings_cfg")
    monitor_task = request.getfixturevalue("monitor_task")
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, "lightwell-test-owner")

    def _create_distribution(domain_name, distro_params, features):
        with anonymous_user:
            python_bindings.DistributionsPypiApi.api_client.default_headers["x-rh-identity"] = owner_header
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

            try:
                response = python_bindings.DistributionsPypiApi.create(
                    distro_params,
                    pulp_domain=domain_name,
                )
            finally:
                python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)

        if hasattr(response, "task"):
            monitor_task(response.task)
        distributions = python_bindings.DistributionsPypiApi.list(
            name=distro_params["name"],
            pulp_domain=domain_name,
        )
        assert distributions.count == 1
        add_to_cleanup(python_bindings.DistributionsPypiApi, distributions.results[0].pulp_href)

    def _configure(domain_name, features=None, use_vpn_composite=False):
        create_service_domain(domain_name, identity_header=owner_header)
        composite_href = None
        with anonymous_user:
            python_bindings.RepositoriesPythonApi.api_client.default_headers["x-rh-identity"] = owner_header
            repo = gen_object_with_cleanup(
                python_bindings.RepositoriesPythonApi, {"name": str(uuid4())}, pulp_domain=domain_name
            )

        python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
        pulpcore_bindings.ContentguardsCompositeApi.api_client.default_headers.pop("x-rh-identity", None)

        distro_params = {"name": str(uuid4()), "base_path": str(uuid4()), "repository": repo.pulp_href}
        if use_vpn_composite:
            composites = pulpcore_bindings.ContentguardsCompositeApi.list(
                name=DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME,
                pulp_domain=domain_name,
            )
            assert composites.count == 1
            composite_href = composites.results[0].pulp_href
        if composite_href:
            distro_params["content_guard"] = composite_href

        _create_distribution(domain_name, distro_params, features)

        base_path = distro_params["base_path"]
        pypi_url = urljoin(bindings_cfg.host, f"/api/pypi/{domain_name}/{base_path}/simple/")
        repos_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/repositories/python/python/")
        return domain_name, pypi_url, repos_url, owner_header

    yield _configure

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    pulpcore_bindings.ContentguardsCompositeApi.api_client.default_headers.pop("x-rh-identity", None)
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


def test_non_public_distribution_denies_unauthenticated_access(configure_pypi_distribution):
    """New non-public distributions inherit the domain's identity content guard."""
    domain_name = f"private-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code in (401, 403)


def test_identity_or_vpn_composite_accepts_either_assertion(configure_pypi_distribution):
    domain_name = f"vpn-composite-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name, use_vpn_composite=True)

    vpn_response = requests.get(pypi_url, headers={"x-pulp-vpn-verified": "dHJ1ZQ=="}, timeout=30)
    assert vpn_response.status_code == 200

    identity_response = requests.get(
        pypi_url,
        headers={"x-rh-identity": _identity_header("777777777", "non-domain-owner")},
        timeout=30,
    )
    assert identity_response.status_code == 200

    denied_response = requests.get(
        pypi_url,
        headers={"x-pulp-vpn-verified": "ZmFsc2U="},
        timeout=30,
    )
    assert denied_response.status_code in (401, 403)


def test_domain_delete_is_blocked_while_distribution_uses_composite(
    configure_pypi_distribution, pulpcore_bindings, python_bindings, monitor_task
):
    domain_name = f"vpn-delete-{uuid4().hex}"
    configure_pypi_distribution(domain_name, use_vpn_composite=True)
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    pulpcore_bindings.ContentguardsCompositeApi.api_client.default_headers.pop("x-rh-identity", None)
    pulpcore_bindings.TasksApi.api_client.default_headers.pop("x-rh-identity", None)

    domain = pulpcore_bindings.DomainsApi.list(name=domain_name).results[0]
    vpn_guards = pulpcore_bindings.ContentguardsHeaderApi.list(
        name=DEFAULT_VPN_CONTENT_GUARD_NAME,
        pulp_domain=domain_name,
    )
    composites = pulpcore_bindings.ContentguardsCompositeApi.list(
        pulp_domain=domain_name,
        name=DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME,
    )
    distributions = python_bindings.DistributionsPypiApi.list(pulp_domain=domain_name)
    assert vpn_guards.count == composites.count == distributions.count == 1
    distribution = distributions.results[0]
    composite = composites.results[0]
    assert domain.default_content_guard is not None
    assert distribution.content_guard == composite.pulp_href

    delete_task = pulpcore_bindings.DomainsApi.delete(domain.pulp_href).task
    with pytest.raises(PulpTaskError):
        monitor_task(delete_task)

    domain_after = pulpcore_bindings.DomainsApi.list(name=domain_name).results[0]
    distribution_after = python_bindings.DistributionsPypiApi.read(distribution.pulp_href)
    assert domain_after.default_content_guard == domain.default_content_guard
    assert distribution_after.content_guard == composite.pulp_href
    assert (
        pulpcore_bindings.ContentguardsHeaderApi.list(
            name=DEFAULT_VPN_CONTENT_GUARD_NAME,
            pulp_domain=domain_name,
        ).count
        == 1
    )
    assert (
        pulpcore_bindings.ContentguardsCompositeApi.list(
            name=DEFAULT_IDENTITY_OR_VPN_CONTENT_GUARD_NAME,
            pulp_domain=domain_name,
        ).count
        == 1
    )


def test_public_domain_allows_unauthenticated_pypi_access(configure_pypi_distribution):
    """A public- domain's PyPI simple API stays open to unauthenticated SAFE_METHOD
    requests -- unaffected by content guards."""
    domain_name = f"public-{uuid4()}"
    _, pypi_url, _, _ = configure_pypi_distribution(domain_name)

    response = requests.get(pypi_url, timeout=30)

    assert response.status_code == 200
