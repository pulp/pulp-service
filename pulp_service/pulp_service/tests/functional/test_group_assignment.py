import json
from base64 import b64encode
from uuid import uuid4

import pytest


def _auth_header(identity):
    return b64encode(json.dumps(identity).encode("ascii"))


def _find_group(pulpcore_bindings, name):
    """Return the group with the given name via the API, or None."""
    groups = pulpcore_bindings.GroupsApi.list(name=name)
    return groups.results[0] if groups.count else None


def _clear_identity(pulpcore_bindings):
    """
    Drop the lingering x-rh-identity header so subsequent calls run as admin.

    Verification calls (listing groups/users) need admin; without this the stale
    header authenticates them as the non-admin identity under test, which for a
    service account has no permission to list groups (403).
    """
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)


@pytest.mark.usefixtures("cleanup_auth_headers")
def test_user_auto_assigned_to_org_group(pulpcore_bindings, anonymous_user):
    """A user authenticating with an org_id is auto-added to the rh-org-<org_id> group."""
    org_id = str(uuid4().int % 1_000_000)
    username = f"autogroup-{uuid4()}"
    group_name = f"rh-org-{org_id}"
    identity = {"identity": {"org_id": org_id, "internal": {"org_id": org_id}, "user": {"username": username}}}

    try:
        with anonymous_user:
            pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = _auth_header(identity)
            # Any authenticated call triggers the auth path that assigns the group.
            pulpcore_bindings.DomainsApi.list()

        _clear_identity(pulpcore_bindings)

        # The group is created on demand.
        group = _find_group(pulpcore_bindings, group_name)
        assert group is not None, f"Group {group_name} was not created"

        # The user is a member of the group. RHTermsBasedRegistryAuthentication
        # stores the username as "<org_id>|<username>".
        members = pulpcore_bindings.GroupsUsersApi.list(group_href=group.pulp_href)
        member_usernames = [u.username for u in members.results]
        assert f"{org_id}|{username}" in member_usernames, f"User not added to {group_name}"
    finally:
        _clear_identity(pulpcore_bindings)
        group = _find_group(pulpcore_bindings, group_name)
        if group is not None:
            pulpcore_bindings.GroupsApi.delete(group.pulp_href)


@pytest.mark.usefixtures("cleanup_auth_headers")
def test_service_account_auto_assigned_to_org_group(pulpcore_bindings, anonymous_user):
    """A service account (x509 identity) with an org_id is auto-added to the rh-org-<org_id> group."""
    org_id = str(uuid4().int % 1_000_000)
    subject_dn = f"CN=svc-{uuid4()},O=Red Hat"
    group_name = f"rh-org-{org_id}"
    # No user.username, so RHTermsBasedRegistryAuthentication falls through to
    # RHServiceAccountCertAuthentication, which resolves the username from subject_dn.
    identity = {"identity": {"x509": {"subject_dn": subject_dn}, "internal": {"org_id": org_id}}}

    try:
        with anonymous_user:
            pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = _auth_header(identity)
            # Any authenticated call triggers the auth path that assigns the group.
            pulpcore_bindings.DomainsApi.list()

        _clear_identity(pulpcore_bindings)

        # The group is created on demand.
        group = _find_group(pulpcore_bindings, group_name)
        assert group is not None, f"Group {group_name} was not created"

        # RHServiceAccountCertAuthentication stores the username as the subject_dn.
        members = pulpcore_bindings.GroupsUsersApi.list(group_href=group.pulp_href)
        member_usernames = [u.username for u in members.results]
        assert subject_dn in member_usernames, f"User not added to {group_name}"
    finally:
        _clear_identity(pulpcore_bindings)
        group = _find_group(pulpcore_bindings, group_name)
        if group is not None:
            pulpcore_bindings.GroupsApi.delete(group.pulp_href)


@pytest.mark.usefixtures("cleanup_auth_headers")
def test_user_without_org_id_skips_group_assignment(pulpcore_bindings, anonymous_user):
    """A user without an org_id authenticates successfully but joins no rh-org- group."""
    username = f"noorg-{uuid4()}"
    identity = {"identity": {"user": {"username": username}}}

    with anonymous_user:
        pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = _auth_header(identity)
        # Authentication still succeeds even without an org_id.
        domains = pulpcore_bindings.DomainsApi.list()
        assert domains is not None

    _clear_identity(pulpcore_bindings)

    # With no org_id the username is "|<username>".
    users = pulpcore_bindings.UsersApi.list(username=f"|{username}")
    assert users.count == 1, "Expected the authenticated user to exist"
    org_groups = [g.name for g in (users.results[0].groups or []) if g.name.startswith("rh-org-")]
    assert not org_groups, f"User without org_id should not be in any rh-org- group, found {org_groups}"


@pytest.mark.usefixtures("cleanup_auth_headers")
def test_multiple_authentications_idempotent(pulpcore_bindings, anonymous_user):
    """Repeated authentications do not create duplicate group memberships."""
    org_id = str(uuid4().int % 1_000_000)
    username = f"repeat-{uuid4()}"
    group_name = f"rh-org-{org_id}"
    identity = {"identity": {"org_id": org_id, "internal": {"org_id": org_id}, "user": {"username": username}}}

    try:
        with anonymous_user:
            pulpcore_bindings.DomainsApi.api_client.default_headers["x-rh-identity"] = _auth_header(identity)
            for _ in range(3):
                pulpcore_bindings.DomainsApi.list()

        _clear_identity(pulpcore_bindings)

        group = _find_group(pulpcore_bindings, group_name)
        assert group is not None, f"Group {group_name} was not created"

        members = pulpcore_bindings.GroupsUsersApi.list(group_href=group.pulp_href)
        combined = f"{org_id}|{username}"
        occurrences = [u.username for u in members.results].count(combined)
        assert occurrences == 1, f"User should be in {group_name} exactly once, found {occurrences}"
    finally:
        _clear_identity(pulpcore_bindings)
        group = _find_group(pulpcore_bindings, group_name)
        if group is not None:
            pulpcore_bindings.GroupsApi.delete(group.pulp_href)
