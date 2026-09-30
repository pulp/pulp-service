"""Regression for the calunga failure: the *historical* broken-state repair.

The forward create path (a self-service create carrying ``internal.org_id`` grants
``rh-org-<org_id>`` its roles) is covered by ``test_org_member_rbac_access.py``. This test
covers the other half -- rows that were written *before* org_id capture / role dual-write and
so ended up with ``DomainOrg.org_id = NULL`` and a ``rh-org-<org_id>`` group holding **zero
roles**. That is exactly the ``public-trusted-libraries`` / ``calunga-internal`` shape that
404'd repo reads and blocked PyPI uploads once RBAC became the default.

The API can no longer *produce* that shape (create always captures org_id), so we build an
org-owned domain via the real HTTP flow, corrupt it to the historical shape through
``django-admin shell`` (the same escape hatch ``test_pypi_yank_check.py`` uses), prove an org
member is now scoped out (404), run migration ``0022``'s backfill, and prove access is
restored -- while an unrelated org stays scoped out.

Only meaningful under ``PulpServiceAccessPolicy`` (the dev container enables RBAC).
"""

import json
import subprocess
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import requests


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode("ascii")).decode("ascii")


def _rand_org():
    return str(uuid4().int % 90000000 + 10000000)


def _shell(code):
    """Run server-side ORM code and return stripped stdout (see test_pypi_yank_check.py)."""
    result = subprocess.run(  # noqa: S603
        ["django-admin", "shell", "-c", code],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _corrupt_to_historical_shape(domain_name, org_id):
    """Recreate the pre-0019 calunga row: DomainOrg.org_id NULL + rh-org-<org_id> role-less,
    with a team group that still has an rh-org member (what 0022 derives org_id from)."""
    code = "\n".join(
        [
            "from django.apps import apps as A",
            "Group = A.get_model('core', 'Group')",
            "GroupRole = A.get_model('core', 'GroupRole')",
            "DomainOrg = A.get_model('service', 'DomainOrg')",
            f"do = DomainOrg.objects.filter(domains__name='{domain_name}').first()",
            f"org_g = Group.objects.get(name='rh-org-{org_id}')",
            "member = org_g.user_set.first()",  # the owner, auto-joined on create
            "do.group.user_set.add(member)",  # team group keeps an rh-org member
            "do.org_id = None",
            "do.save(update_fields=['org_id'])",
            "deleted = GroupRole.objects.filter(group=org_g).delete()",
            "print(GroupRole.objects.filter(group=org_g).count())",
        ]
    )
    assert _shell(code) == "0", "expected rh-org group to be role-less after corruption"


def _run_backfill():
    _shell(
        "\n".join(
            [
                "import importlib",
                "from django.apps import apps as A",
                "m = importlib.import_module('pulp_service.app.migrations.0022_backfill_org_group_roles')",
                "m.backfill_org_group_roles(A, None)",
            ]
        )
    )


def _stored_org_id(domain_name):
    return _shell(
        "\n".join(
            [
                "from django.apps import apps as A",
                "DomainOrg = A.get_model('service', 'DomainOrg')",
                f"do = DomainOrg.objects.filter(domains__name='{domain_name}').first()",
                "print(do.org_id)",
            ]
        )
    )


def test_backfill_restores_org_member_access_for_null_org_id_domain(create_service_domain, bindings_cfg):
    owner_org = _rand_org()
    owner = _identity_header(owner_org, f"owner-{uuid4().hex[:8]}")
    member = _identity_header(owner_org, f"member-{uuid4().hex[:8]}")

    # Org-owned domain (team group) + repo, created through the real self-service flow.
    domain = create_service_domain(
        name=f"histbackfill-{uuid4().hex[:10]}", identity_header=owner, group_name=f"team-{uuid4().hex[:8]}"
    )
    repo_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/python/python/")
    resp = requests.post(
        repo_url,
        headers={"x-rh-identity": owner, "Content-Type": "application/json"},
        data=json.dumps({"name": f"repo-{uuid4().hex[:8]}"}),
        timeout=60,
    )
    assert resp.status_code == 201, f"repo create failed: {resp.status_code} {resp.text}"
    repo_href = resp.json()["pulp_href"]

    # Corrupt to the historical (pre-role-grant) calunga shape.
    _corrupt_to_historical_shape(domain.name, owner_org)

    # Symptom: an org member (only in the role-less rh-org group) is scoped out -> 404.
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 404, f"expected 404 in broken state, got {resp.status_code}: {resp.text}"

    # Fix: migration 0022 backfills org_id and grants rh-org-<org_id> its roles.
    _run_backfill()
    assert _stored_org_id(domain.name) == owner_org, "backfill should re-derive and store org_id"

    # Restored: the same org member now reads the repo and its content.
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 200, f"expected 200 after backfill, got {resp.status_code}: {resp.text}"
    assert resp.json()["pulp_href"] == repo_href

    content_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/content/python/packages/")
    resp = requests.get(content_url, headers={"x-rh-identity": member}, timeout=60)
    assert resp.status_code == 200, f"org member got {resp.status_code} listing content: {resp.text}"

    # Isolation intact: an unrelated org is still scoped out, so the 200s are real.
    outsider = _identity_header(_rand_org(), f"outsider-{uuid4().hex[:8]}")
    resp = requests.get(urljoin(bindings_cfg.host, repo_href), headers={"x-rh-identity": outsider}, timeout=60)
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"
