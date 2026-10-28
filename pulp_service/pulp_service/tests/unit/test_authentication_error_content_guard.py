import logging
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.web_exceptions import HTTPForbidden, HTTPUnauthorized
from asgiref.sync import async_to_sync

from pulpcore.app.models import CompositeContentGuard, HeaderContentGuard
from pulpcore.content.handler import Handler

from pulp_service.app.access_policy import PulpServiceAccessPolicy
from pulp_service.exceptions import AuthenticationError


def _detail_guard(name, error=None):
    detail_guard = MagicMock()
    detail_guard.name = name
    if error is not None:
        detail_guard.permit.side_effect = error
    return detail_guard


def _composite(*detail_guards):
    composite = MagicMock(spec=CompositeContentGuard)
    composite.guards.all.return_value = [MagicMock(cast=MagicMock(return_value=guard)) for guard in detail_guards]
    return composite


def _distribution(detail_guard):
    distribution = MagicMock()
    distribution.content_guard.name = detail_guard.name
    distribution.content_guard.cast.return_value = detail_guard
    return distribution


class TestHeaderContentGuardAuthentication:
    def _make_guard(self, header_name="x-rh-identity"):
        guard = HeaderContentGuard()
        guard.header_name = header_name
        guard.header_value = ""
        guard.jq_filter = '""'
        return guard

    def test_missing_identity_header_raises_authentication_error(self):
        guard = self._make_guard(header_name="x-rh-identity")
        request = MagicMock()
        request.headers.get.return_value = None

        with pytest.raises(AuthenticationError, match="Authentication required"):
            guard.permit(request)

    def test_missing_non_identity_header_raises_permission_error(self):
        guard = self._make_guard(header_name="x-custom-header")
        request = MagicMock()
        request.headers.get.return_value = None

        with pytest.raises(PermissionError):
            guard.permit(request)

        with pytest.raises(PermissionError) as exc_info:
            guard.permit(request)

        assert not isinstance(exc_info.value, AuthenticationError)


class TestCompositeContentGuardAuthentication:
    def test_authentication_error_is_not_authorization_error(self):
        assert not issubclass(AuthenticationError, PermissionError)

    def test_authentication_error_does_not_short_circuit_a_later_success(self, monkeypatch):
        authentication_guard = _detail_guard(
            "identity",
            AuthenticationError("Authentication required."),
        )
        vpn_guard = _detail_guard("vpn")
        composite = _composite(authentication_guard, vpn_guard)
        monkeypatch.setattr("pulpcore.app.models.publication.get_url", lambda *_args, **_kwargs: "/guard/")

        CompositeContentGuard.permit(composite, MagicMock())

        authentication_guard.permit.assert_called_once()
        vpn_guard.permit.assert_called_once()

    def test_authentication_error_wins_when_all_guards_deny(self, monkeypatch):
        authentication_guard = _detail_guard(
            "identity",
            AuthenticationError("Authentication required."),
        )
        permission_guard = _detail_guard("feature", PermissionError("Access denied."))
        composite = _composite(authentication_guard, permission_guard)
        monkeypatch.setattr("pulpcore.app.models.publication.get_url", lambda *_args, **_kwargs: "/guard/")

        with pytest.raises(AuthenticationError, match="Authentication required"):
            CompositeContentGuard.permit(composite, MagicMock())

    def test_permission_error_remains_when_no_guard_requests_authentication(self, monkeypatch):
        first_guard = _detail_guard("first", PermissionError("Access denied."))
        second_guard = _detail_guard("second", PermissionError("Access denied."))
        composite = _composite(first_guard, second_guard)
        monkeypatch.setattr("pulpcore.app.models.publication.get_url", lambda *_args, **_kwargs: "/guard/")

        with pytest.raises(PermissionError) as exc_info:
            CompositeContentGuard.permit(composite, MagicMock())

        assert not isinstance(exc_info.value, AuthenticationError)


class _Cache:
    ADD_TRAILING_SLASH = False

    def __init__(self):
        self.writes = []

    async def get(self, *_args, **_kwargs):
        return None

    async def set(self, *args, **kwargs):
        self.writes.append((args, kwargs))


class TestHandlerAuthentication:
    def test_permit_maps_authentication_error_to_401(self, settings):
        detail_guard = _detail_guard("identity", AuthenticationError("Authentication required."))
        request = MagicMock(path="/lightwell/osv/record")
        settings.CONTENT_ORIGIN = "https://packages.stage.redhat.com"

        with pytest.raises(HTTPUnauthorized) as exc_info:
            Handler._permit(request, _distribution(detail_guard))

        assert exc_info.value.status == 401
        assert exc_info.value.headers["WWW-Authenticate"] == 'Basic realm="packages.stage.redhat.com"'

    def test_permit_derives_realm_from_content_origin(self, settings):
        detail_guard = _detail_guard("identity", AuthenticationError("Authentication required."))
        request = MagicMock(path="/lightwell/osv/record")
        settings.CONTENT_ORIGIN = "https://packages.redhat.com"

        with pytest.raises(HTTPUnauthorized) as exc_info:
            Handler._permit(request, _distribution(detail_guard))

        assert exc_info.value.headers["WWW-Authenticate"] == 'Basic realm="packages.redhat.com"'

    def test_permit_keeps_permission_error_as_403(self):
        detail_guard = _detail_guard("feature", PermissionError("Access denied."))
        request = MagicMock(path="/lightwell/osv/record")

        with pytest.raises(HTTPForbidden) as exc_info:
            Handler._permit(request, _distribution(detail_guard))

        assert exc_info.value.status == 403
        assert "WWW-Authenticate" not in exc_info.value.headers

    def test_auth_cached_marks_401_guard_as_present(self):
        request = MagicMock()
        request.match_info = {"path": "lightwell/osv/record"}
        cached = _Cache()
        distribution = MagicMock()

        with (
            patch.object(Handler, "_match_distribution", return_value=distribution),
            patch.object(Handler, "_permit", side_effect=HTTPUnauthorized()),
            pytest.raises(HTTPUnauthorized),
        ):
            async_to_sync(Handler.auth_cached)(request, cached, "base")

        assert cached.writes[0][0][0] == "DISTRO#GUARD#PRESENT"
        assert cached.writes[0][0][1] == "True"

    def test_auth_cached_does_not_write_after_unexpected_error(self):
        request = MagicMock()
        request.match_info = {"path": "lightwell/osv/record"}
        cached = _Cache()
        distribution = MagicMock()

        with (
            patch.object(Handler, "_match_distribution", return_value=distribution),
            patch.object(Handler, "_permit", side_effect=RuntimeError("unexpected")),
            pytest.raises(RuntimeError, match="unexpected"),
        ):
            async_to_sync(Handler.auth_cached)(request, cached, "base")

        assert cached.writes == []


class TestPermissionPolicyAuthentication:
    def test_access_policy_treats_authentication_error_as_denial(self, caplog):
        policy = PulpServiceAccessPolicy()
        guard = MagicMock()
        guard.cast.return_value.permit.side_effect = AuthenticationError("Authentication required.")

        with caplog.at_level(logging.WARNING):
            result = policy._evaluate_content_guard(guard, MagicMock(), org_id=None, user=None)

        assert result is False
        assert not any(
            "unexpected error evaluating content guard" in record.getMessage().lower() for record in caplog.records
        )
