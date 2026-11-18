"""
Unit tests for the Kessel client (pulp_service.app.kessel).

The gRPC stub and the RBAC v2 workspace lookup are mocked, so no network is needed.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from kessel.inventory.v1beta2.allowed_pb2 import ALLOWED_FALSE, ALLOWED_TRUE, ALLOWED_UNSPECIFIED
from kessel.inventory.v1beta2.check_response_pb2 import CheckResponse

from pulp_service.app import kessel

PERMISSION = "content_sources_repository_view"


@pytest.fixture
def stub():
    kessel._default_workspace_id.cache_clear()
    stub = MagicMock()
    with (
        patch.object(kessel, "_stub", return_value=stub),
        patch.object(kessel, "_credentials", return_value=None),
    ):
        yield stub
    kessel._default_workspace_id.cache_clear()


@pytest.fixture
def fetch_workspace():
    with patch.object(kessel, "fetch_default_workspace", return_value=SimpleNamespace(id="ws-1")) as fetch:
        yield fetch


def test_allowed_true_grants(stub, fetch_workspace):
    stub.Check.return_value = CheckResponse(allowed=ALLOWED_TRUE)

    assert kessel.check_permission("org1", "u1", PERMISSION) is True

    fetch_workspace.assert_called_once()
    assert fetch_workspace.call_args.args[1] == "org1"
    request = stub.Check.call_args.args[0]
    assert request.object.resource_type == "workspace"
    assert request.object.resource_id == "ws-1"
    assert request.object.reporter.type == "rbac"
    assert request.relation == PERMISSION
    assert request.subject.resource.resource_type == "principal"
    assert request.subject.resource.resource_id == "redhat/u1"
    assert request.subject.resource.reporter.type == "rbac"


@pytest.mark.usefixtures("fetch_workspace")
@pytest.mark.parametrize("allowed", [ALLOWED_FALSE, ALLOWED_UNSPECIFIED])
def test_allowed_false_and_unspecified_deny(stub, allowed):
    stub.Check.return_value = CheckResponse(allowed=allowed)

    assert kessel.check_permission("org1", "u1", PERMISSION) is False


def test_workspace_lookup_cached_per_org(stub, fetch_workspace):
    stub.Check.return_value = CheckResponse(allowed=ALLOWED_TRUE)

    kessel.check_permission("org1", "u1", PERMISSION)
    kessel.check_permission("org1", "u2", PERMISSION)

    fetch_workspace.assert_called_once()


def test_workspace_lookup_failure_not_cached(stub, fetch_workspace):
    stub.Check.return_value = CheckResponse(allowed=ALLOWED_TRUE)
    fetch_workspace.side_effect = [requests.HTTPError("boom"), SimpleNamespace(id="ws-1")]

    with pytest.raises(requests.HTTPError):
        kessel.check_permission("org1", "u1", PERMISSION)

    assert kessel.check_permission("org1", "u1", PERMISSION) is True
    assert fetch_workspace.call_count == 2


def test_token_fetch_has_timeout():
    """A hung SSO token endpoint must not block forever (the SDK passes no timeout itself)."""
    kessel._credentials.cache_clear()
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    response._content = b'{"access_token": "t", "token_type": "Bearer", "expires_in": 300}'

    def _send(_adapter, request, **_kwargs):
        response.request = request
        return response

    with (
        override_settings(
            KESSEL_CLIENT_ID="id",
            KESSEL_CLIENT_SECRET="secret",  # noqa: S106
            KESSEL_TOKEN_URL="https://sso.example/token",  # noqa: S106
        ),
        patch("requests.adapters.HTTPAdapter.send", autospec=True) as send,
    ):
        send.side_effect = _send
        assert kessel._credentials().get_token().access_token == "t"  # noqa: S105
    kessel._credentials.cache_clear()
    assert send.call_args.kwargs["timeout"] == kessel._TIMEOUT
