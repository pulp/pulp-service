"""Fix A regression: reads of a public-* domain's repositories must not 404 under RBAC.

Reproduces the production GitHub CI failure (daily_slack_available_packages):

    GET .../public-trusted-libraries/api/v3/repositories/python/python/<uuid>/ -> 404

for an account that authenticates but holds no role on the domain (its only effective group
is the zero-role rh-org-<org_id>). ``PulpServiceAccessPolicy.has_permission`` allowed the read
on a public-* domain, but ``scope_queryset`` filtered the repo out of the queryset, so
``get_object_or_404`` raised 404. Fix A makes ``scope_queryset`` honour the same public-*
bypass, so the read resolves to 200.

Only meaningful when ``PulpServiceAccessPolicy`` is the active permission class (the dev
container enables RBAC). Under the reverted ``DomainBasedPermission`` default a public-* domain
is readable anyway, so these tests pass trivially there too.
"""

import json
import uuid
from base64 import b64encode
from urllib.parse import urljoin

import pytest
import requests

OWNER_ORG_ID = "545454545"
STRANGER_ORG_ID = "989898989"  # different org: no role on the domain


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
def public_domain_with_python_repo(create_service_domain, bindings_cfg):
    """Create a public-* domain (self-service create-domain) with one python repo.

    Returns (domain, repo_href). The repo is created by the owner org, which receives
    service.domain_admin on the domain via its rh-org-<org_id> group (post_create_domain).
    """
    owner_header = _identity_header(OWNER_ORG_ID, f"owner-{uuid.uuid4().hex[:8]}")
    domain = create_service_domain(name=f"public-{uuid.uuid4().hex[:12]}", identity_header=owner_header)
    repo_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/python/python/")
    resp = requests.post(
        repo_url,
        headers={"x-rh-identity": owner_header, "Content-Type": "application/json"},
        data=json.dumps({"name": f"repo-{uuid.uuid4().hex[:8]}"}),
        timeout=60,
    )
    assert resp.status_code == 201, f"repo create failed: {resp.status_code} {resp.text}"
    return domain, resp.json()["pulp_href"]


def test_stranger_can_get_repo_in_public_domain(public_domain_with_python_repo, bindings_cfg):
    """A role-less account (different org) must be able to GET a repo in a public-* domain."""
    _, repo_href = public_domain_with_python_repo
    stranger_header = _identity_header(STRANGER_ORG_ID, f"stranger-{uuid.uuid4().hex[:8]}")

    resp = requests.get(
        urljoin(bindings_cfg.host, repo_href),
        headers={"x-rh-identity": stranger_header},
        timeout=60,
    )
    # Pre-Fix-A this is 404 ("No PythonRepository matches the given query."); post-fix 200.
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["pulp_href"] == repo_href


def test_stranger_can_list_repos_in_public_domain(public_domain_with_python_repo, bindings_cfg):
    """List (not just detail) must also return the public domain's repos to a role-less caller."""
    domain, repo_href = public_domain_with_python_repo
    stranger_header = _identity_header(STRANGER_ORG_ID, f"stranger-{uuid.uuid4().hex[:8]}")

    resp = requests.get(
        urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/python/python/"),
        headers={"x-rh-identity": stranger_header},
        timeout=60,
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    hrefs = [r["pulp_href"] for r in resp.json()["results"]]
    assert repo_href in hrefs
