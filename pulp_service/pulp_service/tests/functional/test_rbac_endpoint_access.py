"""Behavioral RBAC regression for endpoints #1535 locked down: a domain-admin service
account (org-group member, not a superuser) must be able to use content/file/files,
artifacts/, and orphans/cleanup/ in its own domain. Requires the dev container (RBAC on)."""

import json
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


def test_domain_admin_uploads_file_content(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"filerbac-{uuid4().hex[:10]}", identity_header=owner)

    # FileContentViewSet.create requires a destination repository, so create one first. The
    # domain admin holds file.add_filerepository; repositories/ keeps pulp_file's default policy
    # (not overridden in settings), so a 201 here also confirms that path stays open.
    repo_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/repositories/file/file/")
    repo_resp = requests.post(
        repo_url,
        headers={"x-rh-identity": owner},
        json={"name": f"repo-{uuid4().hex[:8]}"},
        timeout=60,
    )
    assert repo_resp.status_code == 201, f"file repo create got {repo_resp.status_code}: {repo_resp.text}"
    repo_href = repo_resp.json()["pulp_href"]

    url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/content/file/files/")
    resp = requests.post(
        url,
        headers={"x-rh-identity": owner},
        files={"file": ("hello.txt", b"hello world")},
        data={"relative_path": "hello.txt", "repository": repo_href},
        timeout=120,
    )
    # 202 (async create task) proves the create statement now allows the domain admin; the
    # pre-fix behavior was a hard 403 (authorization runs before serializer validation).
    assert resp.status_code == 202, f"file content create got {resp.status_code}: {resp.text}"


def test_outsider_cannot_list_file_content(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"filerbac-{uuid4().hex[:10]}", identity_header=owner)
    outsider = _identity_header(_rand_org(), f"outsider-{uuid4().hex[:8]}")

    url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/content/file/files/")
    resp = requests.get(url, headers={"x-rh-identity": outsider}, timeout=60)
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"


def test_domain_admin_lists_and_creates_artifacts(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"artrbac-{uuid4().hex[:10]}", identity_header=owner)

    base = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/artifacts/")
    resp = requests.get(base, headers={"x-rh-identity": owner}, timeout=60)
    assert resp.status_code == 200, f"artifact list got {resp.status_code}: {resp.text}"

    resp = requests.post(
        base,
        headers={"x-rh-identity": owner},
        files={"file": ("blob.bin", b"artifact-bytes")},
        timeout=120,
    )
    assert resp.status_code == 201, f"artifact create got {resp.status_code}: {resp.text}"


def test_outsider_cannot_create_artifact(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"artrbac-{uuid4().hex[:10]}", identity_header=owner)
    outsider = _identity_header(_rand_org(), f"outsider-{uuid4().hex[:8]}")

    url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/artifacts/")
    resp = requests.post(
        url,
        headers={"x-rh-identity": outsider},
        files={"file": ("blob.bin", b"artifact-bytes")},
        timeout=120,
    )
    # Authorization runs before serializer validation, so a denied outsider gets 403/404
    # rather than a 201. Pins the has_model_or_domain_perms:core.add_artifact create condition.
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"


def test_domain_admin_triggers_orphan_cleanup(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"orphrbac-{uuid4().hex[:10]}", identity_header=owner)

    url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/orphans/cleanup/")
    resp = requests.post(url, headers={"x-rh-identity": owner}, json={}, timeout=120)
    # 202 => dispatched an async cleanup task; pre-fix this was a hard 403 (admin-only default).
    assert resp.status_code == 202, f"orphan cleanup got {resp.status_code}: {resp.text}"


def test_outsider_cannot_trigger_orphan_cleanup(create_service_domain, bindings_cfg):
    org = _rand_org()
    owner = _identity_header(org, f"owner-{uuid4().hex[:8]}")
    domain = create_service_domain(name=f"orphrbac-{uuid4().hex[:10]}", identity_header=owner)
    outsider = _identity_header(_rand_org(), f"outsider-{uuid4().hex[:8]}")

    url = urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/orphans/cleanup/")
    resp = requests.post(url, headers={"x-rh-identity": outsider}, json={}, timeout=120)
    # A denied outsider gets 403/404 instead of a 202 dispatch. Pins the
    # has_domain_perms:core.delete_content condition on the orphans/cleanup override.
    assert resp.status_code in (403, 404), f"outsider got {resp.status_code}: {resp.text}"
