"""
Functional tests for the admin-readonly group on the /api/pulp/admin/tasks/ endpoint.

Members of the admin-readonly group (ADMIN_READONLY_GROUP setting) get read-only
(GET/HEAD/OPTIONS) access to the admin tasks API. Write methods are denied.
Admin (superuser) users retain full access.
"""

import json
from base64 import b64encode
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests
from django.conf import settings

ADMIN_READONLY_ORG_ID = "888888888"


def _identity_header(org_id, username):
    identity = {
        "identity": {
            "org_id": org_id,
            "internal": {"org_id": org_id},
            "user": {"username": username},
        }
    }
    return b64encode(json.dumps(identity).encode()).decode()


def _combined_username(org_id, username):
    return f"{org_id}|{username}"


@pytest.fixture
def admin_tasks_url(bindings_cfg):
    return urljoin(bindings_cfg.host, "/api/pulp/admin/tasks/")


@pytest.fixture
def status_url(bindings_cfg):
    return urljoin(bindings_cfg.host, "/api/pulp/api/v3/status/")


@pytest.fixture
def admin_auth(bindings_cfg):
    return (bindings_cfg.username, bindings_cfg.password)


@pytest.fixture
def admin_readonly_group(pulpcore_bindings, gen_object_with_cleanup):
    group_name = getattr(settings, "ADMIN_READONLY_GROUP", "admin-readonly")
    groups = pulpcore_bindings.GroupsApi.list(name=group_name)
    if groups.count > 0:
        return groups.results[0]
    return gen_object_with_cleanup(pulpcore_bindings.GroupsApi, {"name": group_name})


@pytest.fixture
def gen_admin_readonly_member(pulpcore_bindings, gen_object_with_cleanup, admin_readonly_group):
    def _gen_member(suffix):
        username = f"admin-ro-{suffix}-{uuid4()}"
        combined = _combined_username(ADMIN_READONLY_ORG_ID, username)
        gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": combined})
        gen_object_with_cleanup(
            pulpcore_bindings.GroupsUsersApi,
            group_href=admin_readonly_group.pulp_href,
            group_user={"username": combined},
        )
        return _identity_header(ADMIN_READONLY_ORG_ID, username)

    return _gen_member


class TestAdminTasksEndpoint:
    def test_admin_can_list_tasks(self, admin_tasks_url, admin_auth):
        resp = requests.get(admin_tasks_url, auth=admin_auth, timeout=30)
        assert resp.status_code == 200

    def test_readonly_member_can_get_tasks(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("get")}
        resp = requests.get(admin_tasks_url, headers=headers, timeout=30)
        assert resp.status_code == 200

    def test_readonly_member_can_head_tasks(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("head")}
        resp = requests.head(admin_tasks_url, headers=headers, timeout=30)
        assert resp.status_code in (200, 405)

    def test_readonly_member_can_options_tasks(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("options")}
        resp = requests.options(admin_tasks_url, headers=headers, timeout=30)
        assert resp.status_code in (200, 405)

    def test_readonly_member_post_denied(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("post")}
        resp = requests.post(admin_tasks_url, headers=headers, json={}, timeout=30)
        assert resp.status_code in (401, 403, 405)

    def test_readonly_member_put_denied(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("put")}
        resp = requests.put(admin_tasks_url, headers=headers, json={}, timeout=30)
        assert resp.status_code in (401, 403, 405)

    def test_readonly_member_patch_denied(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("patch")}
        resp = requests.patch(admin_tasks_url, headers=headers, json={}, timeout=30)
        assert resp.status_code in (401, 403, 405)

    def test_readonly_member_delete_denied(self, admin_tasks_url, gen_admin_readonly_member):
        headers = {"x-rh-identity": gen_admin_readonly_member("delete")}
        resp = requests.delete(admin_tasks_url, headers=headers, timeout=30)
        assert resp.status_code in (401, 403, 405)

    def test_unauthenticated_user_denied(self, admin_tasks_url):
        resp = requests.get(admin_tasks_url, timeout=30)
        assert resp.status_code in (401, 403)

    def test_non_member_denied(self, admin_tasks_url, pulpcore_bindings, gen_object_with_cleanup):
        username = f"non-member-{uuid4()}"
        combined = _combined_username(ADMIN_READONLY_ORG_ID, username)
        gen_object_with_cleanup(pulpcore_bindings.UsersApi, {"username": combined})
        headers = {"x-rh-identity": _identity_header(ADMIN_READONLY_ORG_ID, username)}
        resp = requests.get(admin_tasks_url, headers=headers, timeout=30)
        assert resp.status_code in (401, 403)


class TestStatusEndpoint:
    def test_admin_can_access_status(self, status_url, admin_auth):
        resp = requests.get(status_url, auth=admin_auth, timeout=30)
        assert resp.status_code == 200

    def test_unauthenticated_can_access_status(self, status_url):
        resp = requests.get(status_url, timeout=30)
        assert resp.status_code == 200
