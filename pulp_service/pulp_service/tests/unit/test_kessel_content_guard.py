"""
Unit tests for KesselContentGuard.permit(): identity parsing and fail-closed behavior.

The Kessel client (check_permission) is mocked; its own behavior is covered in test_kessel_client.py.
"""

import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pulp_service.app.models import KesselContentGuard
from pulp_service.exceptions import AuthenticationError

PERMISSION = "content_sources_repository_view"
_CHECK = "pulp_service.app.models.check_permission"


def _guard():
    # pulp_domain must be explicit: its default (get_domain_pk) queries the DB.
    return KesselContentGuard(name="test", permission=PERMISSION, pulp_domain=None)


def _request(identity):
    header = b64encode(json.dumps({"identity": identity}).encode()).decode()
    return SimpleNamespace(headers={"x-rh-identity": header})


USER = {"type": "User", "org_id": "org1", "user": {"user_id": "u1"}}


def test_user_allowed():
    with patch(_CHECK, return_value=True) as check:
        assert _guard().permit(_request(USER)) is None
    check.assert_called_once_with("org1", "u1", PERMISSION)


def test_service_account_uses_service_account_user_id():
    identity = {"type": "ServiceAccount", "org_id": "org1", "service_account": {"user_id": "sa1"}}
    with patch(_CHECK, return_value=True) as check:
        _guard().permit(_request(identity))
    check.assert_called_once_with("org1", "sa1", PERMISSION)


def test_internal_org_id_fallback():
    identity = {"type": "User", "internal": {"org_id": "org2"}, "user": {"user_id": "u1"}}
    with patch(_CHECK, return_value=True) as check:
        _guard().permit(_request(identity))
    check.assert_called_once_with("org2", "u1", PERMISSION)


def test_denied_raises_permission_error():
    with patch(_CHECK, return_value=False), pytest.raises(PermissionError):
        _guard().permit(_request(USER))


def test_missing_header_raises_authentication_error():
    with patch(_CHECK) as check, pytest.raises(AuthenticationError):
        _guard().permit(SimpleNamespace(headers={}))
    check.assert_not_called()


@pytest.mark.parametrize(
    "header",
    [
        "not-base64!!",
        b64encode(b"not json").decode(),
        b64encode(json.dumps(["not", "a", "dict"]).encode()).decode(),
        b64encode(json.dumps({"identity": {"type": "System", "org_id": "org1"}}).encode()).decode(),
        b64encode(json.dumps({"identity": {"type": "User", "user": {"user_id": "u1"}}}).encode()).decode(),
        b64encode(json.dumps({"identity": {"type": "User", "org_id": "org1", "user": {}}}).encode()).decode(),
    ],
    ids=["non-base64", "non-json", "non-dict", "system-type", "missing-org-id", "missing-user-id"],
)
def test_bad_header_raises_permission_error(header):
    with patch(_CHECK) as check, pytest.raises(PermissionError):
        _guard().permit(SimpleNamespace(headers={"x-rh-identity": header}))
    check.assert_not_called()


def test_kessel_error_fails_closed():
    with patch(_CHECK, side_effect=RuntimeError("kessel down")), pytest.raises(PermissionError):
        _guard().permit(_request(USER))
