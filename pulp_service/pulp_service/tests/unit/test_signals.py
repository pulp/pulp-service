import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pulpcore.app.models import HeaderContentGuard

from pulp_service.app.signals import post_create_domain


def _identity_request(identity):
    request = MagicMock()
    request.headers = {"x-rh-identity": b64encode(json.dumps(identity).encode()).decode()}
    return request


def test_domain_creation_provisions_identity_header_guard():
    domain = SimpleNamespace()

    with patch("pulp_service.app.signals.HeaderContentGuard.objects.create") as create:
        post_create_domain(sender=None, instance=domain, created=True)

    create.assert_called_once_with(
        name="x-rh-identity",
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=domain,
    )


def test_domain_update_does_not_provision_identity_header_guard():
    with patch("pulp_service.app.signals.HeaderContentGuard.objects.create") as create:
        post_create_domain(sender=None, instance=SimpleNamespace(), created=False)

    create.assert_not_called()


def test_identity_header_guard_ignores_identity_payload():
    guard = HeaderContentGuard(
        name="x-rh-identity",
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=None,
    )

    guard.permit(_identity_request({"identity": {"org_id": "1"}}))
    guard.permit(_identity_request({"identity": {"org_id": "different"}}))


def test_identity_header_guard_denies_missing_header():
    guard = HeaderContentGuard(
        name="x-rh-identity",
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=None,
    )
    request = MagicMock()
    request.headers = {}

    with pytest.raises(PermissionError):
        guard.permit(request)
