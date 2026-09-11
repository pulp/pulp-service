"""
Functional tests validating the RBAC scenario that motivated PULP-2120: once the
default permission class is switched to ``PulpServiceAccessPolicy``, a user who
uploads content into their domain must still be able to *view* that content even
though it is not in any repository (orphan content).

Under the previous ``DomainBasedPermission`` default this worked because content
reads were not repository-scoped. Under naive RBAC, pulpcore's
``BaseContentViewSet.scope_queryset`` scopes content reads by repository
membership, which hides orphan content and breaks read-after-upload.
``PulpServiceAccessPolicy`` (settings-driven, see ``ACCESS_POLICIES``) restores
visibility by gating content ``list`` on the domain-scoped ``core.view_content``
permission and dropping queryset scoping.

Setup mirrors the production self-service flow: the org creates its domain through
pulp-service's ``create-domain`` endpoint (``x-rh-identity``), which dual-writes
the ``service.domain_admin`` role -- including ``core.view_content`` -- to the
org's ``rh-org-<org_id>`` group. Orphan content is then uploaded (no repository)
by the admin superuser, and the reads under test are performed as the org.
"""

import contextlib
import json
import os
import tempfile
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests

DOMAIN_OWNER_ORG_ID = "545454545"
UNRELATED_ORG_ID = "989898989"


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
def domain_with_orphan_content(
    template_domain_s3,  # noqa: ARG001
    pulpcore_bindings,
    file_bindings,
    bindings_cfg,
    monitor_task,
):
    """Create a domain owned by DOMAIN_OWNER_ORG_ID with one orphan file content unit.

    The domain is created via the self-service ``create-domain`` endpoint so the
    owning org receives ``core.view_content`` (through ``service.domain_admin``).

    Returns (owner_header, orphan_href, generic_content_url, file_content_url).
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, f"orphan-owner-{uuid4().hex[:8]}")
    domain_name = f"orphan-{uuid4().hex[:12]}"

    create_resp = requests.post(
        urljoin(bindings_cfg.host, "/api/pulp/create-domain/"),
        headers={"x-rh-identity": owner_header, "Content-Type": "application/json"},
        data=json.dumps({"name": domain_name}),
        timeout=60,
    )
    assert create_resp.status_code == 201, create_resp.text
    domain_href = create_resp.json()["pulp_href"]

    # Upload orphan file content (no repository) into the domain as the admin superuser.
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        tf.write(b"orphan content payload " + uuid4().hex.encode())
        tf_path = tf.name
    try:
        task = file_bindings.ContentFilesApi.create(
            relative_path=f"orphan-{uuid4().hex[:8]}.txt",
            file=tf_path,
            pulp_domain=domain_name,
        ).task
        orphan_href = monitor_task(task).created_resources[0]
    finally:
        os.unlink(tf_path)

    base = f"/api/pulp/{domain_name}/api/v3/content/"
    generic_content_url = urljoin(bindings_cfg.host, base)
    file_content_url = urljoin(bindings_cfg.host, base + "file/files/")
    yield owner_header, orphan_href, generic_content_url, file_content_url

    with contextlib.suppress(Exception):
        monitor_task(pulpcore_bindings.DomainsApi.delete(domain_href).task)


def test_owner_org_views_orphan_content_via_generic_endpoint(domain_with_orphan_content):
    """The domain-owning org can GET orphan content via the generic /content/ endpoint."""
    owner_header, orphan_href, generic_content_url, _ = domain_with_orphan_content

    response = requests.get(generic_content_url, headers={"x-rh-identity": owner_header}, timeout=60)

    assert response.status_code == 200, response.text
    hrefs = [result["pulp_href"] for result in response.json()["results"]]
    assert orphan_href in hrefs


def test_owner_org_views_orphan_content_via_typed_file_endpoint(domain_with_orphan_content):
    """The domain-owning org can GET orphan content via the typed file /content/file/files/ endpoint."""
    owner_header, orphan_href, _, file_content_url = domain_with_orphan_content

    response = requests.get(file_content_url, headers={"x-rh-identity": owner_header}, timeout=60)

    assert response.status_code == 200, response.text
    hrefs = [result["pulp_href"] for result in response.json()["results"]]
    assert orphan_href in hrefs


def test_unrelated_org_cannot_view_orphan_content(domain_with_orphan_content):
    """An org with no access to the domain cannot see its orphan content.

    RBAC either denies the endpoint (403) or returns 200 with the orphan content
    filtered out. Accept either shape.
    """
    _, orphan_href, generic_content_url, _ = domain_with_orphan_content
    stranger_header = _identity_header(UNRELATED_ORG_ID, f"stranger-{uuid4().hex[:8]}")

    response = requests.get(generic_content_url, headers={"x-rh-identity": stranger_header}, timeout=60)

    if response.status_code == 200:
        hrefs = [result["pulp_href"] for result in response.json()["results"]]
        assert orphan_href not in hrefs
    else:
        assert response.status_code == 403
