"""Regression for the calunga create-time footgun.

A self-service domain create whose X-RH-IDENTITY header lacks identity.internal.org_id
used to write a DomainOrg with org_id=NULL and never grant the rh-org-<org_id> group its
roles -- so org members (service accounts in rh-org-<org_id> only) got 404s under RBAC.

The fix (signals._derive_org_id_from_user) recovers org_id from the creating user's own
rh-org-<org_id> membership. This test drives the real HTTP flow: warm up the user's rh-org
membership with a header that has internal.org_id, then create the target domain with a
header that has identity.org_id (same username) but NO internal.org_id, and prove an org
member reads the domain's repo (200) and that DomainOrg.org_id was stored.

Only meaningful under PulpServiceAccessPolicy (the dev container enables RBAC).
"""

import json
import subprocess
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import requests


def _header(*, org_id, username, internal):
    """internal=True includes identity.internal.org_id (org_id captured on create);
    internal=False omits it (the calunga shape) while keeping identity.org_id so the
    derived username -- <org_id>|<username> -- stays the same user."""
    identity = {"org_id": org_id, "user": {"username": username}}
    if internal:
        identity["internal"] = {"org_id": org_id}
    return b64encode(json.dumps({"identity": identity}).encode("ascii")).decode("ascii")


def _rand_org():
    return str(uuid4().int % 90000000 + 10000000)


def _stored_org_id(domain_name):
    code = "\n".join(
        [
            "from django.apps import apps as A",
            "DomainOrg = A.get_model('service', 'DomainOrg')",
            f"do = DomainOrg.objects.filter(domains__name='{domain_name}').first()",
            "print(do.org_id if do else 'NO-DOMAINORG')",
        ]
    )
    return subprocess.run(  # noqa: S603
        ["django-admin", "shell", "-c", code],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_create_without_internal_org_id_still_grants_org_group(create_service_domain, bindings_cfg):
    org = _rand_org()
    username = f"owner-{uuid4().hex[:8]}"
    warmup = _header(org_id=org, username=username, internal=True)
    no_internal = _header(org_id=org, username=username, internal=False)
    member = _header(org_id=org, username=f"member-{uuid4().hex[:8]}", internal=True)

    # Warm up: an authenticated request carrying internal.org_id adds the owner to
    # rh-org-<org> (OrgGroupAssignmentMixin runs on every authenticated request). A
    # create is a guaranteed-authenticating request that also creates the rh-org group.
    warmup_domain = create_service_domain(
        name=f"warmup-{uuid4().hex[:10]}", identity_header=warmup, group_name=f"warm-{uuid4().hex[:8]}"
    )
    assert warmup_domain.name  # sanity: warm-up create succeeded

    # Target: create WITHOUT internal.org_id -> org_id_var is None on the signal. The fix
    # must derive org from the owner's rh-org-<org> membership seeded by the warm-up.
    domain = create_service_domain(
        name=f"nointernal-{uuid4().hex[:10]}", identity_header=no_internal, group_name=f"team-{uuid4().hex[:8]}"
    )

    # The fallback stored the derived org_id (was NULL before the fix).
    assert _stored_org_id(domain.name) == org, "expected create-time fallback to store derived org_id"

    # Create a repo (owner has direct roles regardless of the fallback).
    repo_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/python/python/")
    resp = requests.post(
        repo_url,
        headers={"x-rh-identity": no_internal, "Content-Type": "application/json"},
        data=json.dumps({"name": f"repo-{uuid4().hex[:8]}"}),
        timeout=60,
    )
    assert resp.status_code == 201, f"repo create failed: {resp.status_code} {resp.text}"
    repo_href = resp.json()["pulp_href"]

    # The payoff: an org member (only in rh-org-<org>) reads the repo -> 200, because the
    # fallback granted rh-org-<org> its roles. Without the fix this is 404.
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 200, f"expected 200 for org member, got {resp.status_code}: {resp.text}"

    # Isolation intact: an unrelated org stays scoped out, so the 200 is real.
    outsider = _header(org_id=_rand_org(), username=f"outsider-{uuid4().hex[:8]}", internal=True)
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": outsider}, timeout=60)
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"
