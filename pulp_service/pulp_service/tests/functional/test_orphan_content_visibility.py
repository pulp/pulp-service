"""
Functional tests: a user who can access a domain can view (GET) orphan content --
content that has been uploaded but not yet added to any repository -- in that domain.

Under RBAC (``PulpServiceAccessPolicy``), content reads are scoped by repository
membership (pulpcore ``BaseContentViewSet.scope_queryset``), which hides orphan content
and breaks read-after-upload. Patch 0064 restores visibility for users holding the
domain-scoped ``service.view_orphan_content`` permission, which is granted via the
``service.domain_admin`` / ``service.domain_viewer`` roles. The domain creator's org
receives ``service.domain_admin`` domain-scoped automatically on domain creation.

The orphan content is created by the (superuser) admin binding directly into the domain;
the reads under test are performed as the domain-owning org via ``x-rh-identity``.
"""

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
    anonymous_user,
    gen_object_with_cleanup,
    pulpcore_bindings,
    file_bindings,
    bindings_cfg,
    monitor_task,
):
    """Create a domain owned by DOMAIN_OWNER_ORG_ID containing one orphan file content unit.

    Returns (content_url, owner_header, orphan_href).
    """
    owner_header = _identity_header(DOMAIN_OWNER_ORG_ID, f"orphan-owner-{uuid4().hex[:8]}")
    domain_name = f"orphan-{uuid4().hex[:12]}"

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

    content_url = urljoin(bindings_cfg.host, f"/api/pulp/{domain_name}/api/v3/content/file/files/")
    yield content_url, owner_header, orphan_href

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)


def test_domain_member_can_view_orphan_content(domain_with_orphan_content):
    """The domain-owning org can GET orphan content in its domain."""
    content_url, owner_header, orphan_href = domain_with_orphan_content

    response = requests.get(content_url, headers={"x-rh-identity": owner_header}, timeout=60)

    assert response.status_code == 200
    hrefs = [result["pulp_href"] for result in response.json()["results"]]
    assert orphan_href in hrefs


def test_unrelated_org_cannot_view_orphan_content(domain_with_orphan_content):
    """An org with no access to the domain cannot see its orphan content.

    ``DomainBasedPermission`` denies the endpoint outright (403); RBAC scoping instead
    returns 200 with the orphan content filtered out. Accept either shape.
    """
    content_url, _, orphan_href = domain_with_orphan_content
    stranger_header = _identity_header(UNRELATED_ORG_ID, f"stranger-{uuid4().hex[:8]}")

    response = requests.get(content_url, headers={"x-rh-identity": stranger_header}, timeout=60)

    if response.status_code == 200:
        hrefs = [result["pulp_href"] for result in response.json()["results"]]
        assert orphan_href not in hrefs
    else:
        assert response.status_code == 403
