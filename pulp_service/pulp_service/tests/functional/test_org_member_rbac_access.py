"""Regression: a member of an org-owned domain's owning org must be able to read that
domain's repositories and content under RBAC.

The failing shape is a CI *service account* that authenticates via the RH identity header
and is auto-added to ``rh-org-<org_id>`` only -- never to the domain's "team" group. Under
RBAC that org group must hold the domain roles, or the service account is scoped out (404
on reads, 400 on uploads).

Only meaningful when ``PulpServiceAccessPolicy`` is the active permission class (the dev
container enables RBAC). Under the ``DomainBasedPermission`` default a same-org member is
granted by the org_id match anyway, so these pass there too. This guards the *forward*
create path (a self-service create carrying ``identity.internal.org_id`` grants
``rh-org-<org_id>`` its roles) so the regression is caught if that grant or its org_id
capture breaks again. The historical broken-state repair is covered by migration
``0022_backfill_org_group_roles``.
"""

import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests


def _identity_header(org_id, username):
    """An RH identity header carrying org_id (used for the username) and internal.org_id
    (used by the auth mixin to auto-join ``rh-org-<org_id>`` and by DomainBasedPermission
    to capture org_id at create time) -- the real service-account shape."""
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode("ascii")).decode("ascii")


def _rand_org():
    # Unique org per run so a leftover rh-org-<org_id> group from an earlier run cannot
    # mask the result (these fixtures clean up domains, not groups).
    return str(uuid4().int % 90000000 + 10000000)


@pytest.fixture
def org_domain_with_repo(create_service_domain, bindings_cfg):
    """An org-owned domain (non-``public-*`` so the public-domain read bypass can't mask
    the role check) with a python repo, created by an owner carrying internal.org_id."""
    owner_org = _rand_org()
    owner = _identity_header(owner_org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"orgrbac-{uuid4().hex[:10]}", identity_header=owner)
    repo_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/python/python/")
    resp = requests.post(
        repo_url,
        headers={"x-rh-identity": owner, "Content-Type": "application/json"},
        data=json.dumps({"name": f"repo-{uuid4().hex[:8]}"}),
        timeout=60,
    )
    assert resp.status_code == 201, f"repo create failed: {resp.status_code} {resp.text}"
    return owner_org, domain, resp.json()["pulp_href"]


def test_same_org_member_reads_repo_under_rbac(org_domain_with_repo, bindings_cfg):
    """A DIFFERENT member of the OWNING org -- only in rh-org-<org_id>, never in the team
    group (the service-account shape) -- reads the repo and its content -> 200."""
    owner_org, domain, repo_href = org_domain_with_repo
    member = _identity_header(owner_org, f"member-{uuid4().hex[:8]}")

    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 200, f"org member got {resp.status_code} reading repo: {resp.text}"
    assert resp.json()["pulp_href"] == repo_href

    content_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/content/python/packages/")
    resp = requests.get(content_url, headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 200, f"org member got {resp.status_code} listing content: {resp.text}"


def test_other_org_member_cannot_read_repo_under_rbac(org_domain_with_repo, bindings_cfg):
    """A member of an unrelated org is scoped out -> proves scoping is active, so the 200s
    in the same-org test are real and not an unscoped read."""
    _owner_org, _domain, repo_href = org_domain_with_repo
    outsider = _identity_header(_rand_org(), f"outsider-{uuid4().hex[:8]}")
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": outsider}, timeout=60)
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"
