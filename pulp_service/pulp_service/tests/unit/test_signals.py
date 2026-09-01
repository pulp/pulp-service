import json
from base64 import b64encode
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pulpcore.app.models import HeaderContentGuard

from pulp_service.app import authorization as auth
from pulp_service.app.signals import post_create_domain


def _identity_request(identity):
    request = MagicMock()
    request.headers = {"x-rh-identity": b64encode(json.dumps(identity).encode()).decode()}
    return request


@pytest.fixture
def domain_creation_context():
    tokens = [
        auth.org_id_var.set("1"),
        auth.user_id_var.set("2"),
        auth.group_var.set(None),
    ]
    yield
    auth.group_var.reset(tokens[2])
    auth.user_id_var.reset(tokens[1])
    auth.org_id_var.reset(tokens[0])


@patch("pulp_service.app.signals.transaction.atomic", return_value=nullcontext())
@patch("pulp_service.app.signals._assign_domain_roles")
@patch("pulp_service.app.signals.Group.objects.get_or_create")
@patch("pulp_service.app.signals.Group.objects.filter")
@patch("pulp_service.app.signals.DomainOrg.objects.create")
@patch("pulp_service.app.signals.get_user_model")
@patch("pulp_service.app.signals.HeaderContentGuard.objects.create")
def test_domain_creation_provisions_identity_header_guard(
    create,
    get_user_model,
    domain_org_create,
    group_filter,
    group_get_or_create,
    assign_domain_roles,
    _atomic,
    domain_creation_context,
):
    domain = SimpleNamespace(name="tenant", save=MagicMock())
    user = SimpleNamespace(groups=MagicMock())
    get_user_model.return_value.objects.get.return_value = user
    group_qs = MagicMock()
    group_qs.exclude.return_value.first.return_value = None
    group_filter.return_value = group_qs
    do = SimpleNamespace(domains=MagicMock())
    domain_org_create.return_value = do
    org_group = SimpleNamespace()
    group_get_or_create.return_value = (org_group, True)

    post_create_domain(sender=None, instance=domain, created=True)

    create.assert_called_once_with(
        name="x-rh-identity",
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=domain,
    )
    assert domain.default_content_guard == create.return_value
    domain.save.assert_called_once_with(update_fields=["default_content_guard"])


@patch("pulp_service.app.signals.transaction.atomic", return_value=nullcontext())
@patch("pulp_service.app.signals._assign_domain_roles")
@patch("pulp_service.app.signals.Group.objects.get_or_create")
@patch("pulp_service.app.signals.Group.objects.filter")
@patch("pulp_service.app.signals.DomainOrg.objects.create")
@patch("pulp_service.app.signals.get_user_model")
@patch("pulp_service.app.signals.HeaderContentGuard.objects.create")
def test_public_domain_creation_skips_identity_header_guard(
    create,
    get_user_model,
    domain_org_create,
    group_filter,
    group_get_or_create,
    assign_domain_roles,
    _atomic,
    domain_creation_context,
):
    domain = SimpleNamespace(name="public-tenant", save=MagicMock())
    user = SimpleNamespace(groups=MagicMock())
    get_user_model.return_value.objects.get.return_value = user
    group_qs = MagicMock()
    group_qs.exclude.return_value.first.return_value = None
    group_filter.return_value = group_qs
    do = SimpleNamespace(domains=MagicMock())
    domain_org_create.return_value = do
    group_get_or_create.return_value = (SimpleNamespace(), True)

    post_create_domain(sender=None, instance=domain, created=True)

    create.assert_not_called()
    domain.save.assert_not_called()


@patch("pulp_service.app.signals.HeaderContentGuard.objects.create")
def test_domain_update_does_not_provision_identity_header_guard(create):
    post_create_domain(sender=None, instance=SimpleNamespace(name="tenant"), created=False)

    create.assert_not_called()


@patch("pulp_service.app.signals.transaction.atomic", return_value=nullcontext())
@patch("pulp_service.app.signals.HeaderContentGuard.objects.create")
def test_domain_without_creator_provisions_identity_header_guard(create, _atomic):
    domain = SimpleNamespace(name="automation", save=MagicMock())

    post_create_domain(sender=None, instance=domain, created=True)

    create.assert_called_once_with(
        name="x-rh-identity",
        header_name="x-rh-identity",
        header_value="",
        jq_filter='""',
        pulp_domain=domain,
    )
    assert domain.default_content_guard == create.return_value
    domain.save.assert_called_once_with(update_fields=["default_content_guard"])


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
