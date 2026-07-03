"""
Unit tests for DomainBasedPermission.has_permission().

These tests mock Django ORM and Pulp internals so they can run without
a live Pulp stack. They cover the safe-method bypass for public domains and
for PyPI views on domains other than "lightwell", the lightwell-network
feature check specifically on the lightwell domain's PyPI views, the
DomainOrg bypass of that feature check, and unsafe-method handling.
"""

import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulp_service.app.authorization import DomainBasedPermission


def _encode_identity_header(org_id):
    identity = {"identity": {"internal": {"org_id": org_id}}}
    return b64encode(json.dumps(identity).encode()).decode()


def _make_request(method="GET", user=None, domain=None, view_name="pypi-metadata", org_id=None):
    """Build a mock DRF request."""
    request = MagicMock()
    request.method = method
    request.user = user or _make_anonymous_user()
    request.pulp_domain = domain
    request.resolver_match = MagicMock()
    request.resolver_match.view_name = view_name
    request.META = {"REQUEST_METHOD": method, "PATH_INFO": "/api/pypi/test/main/simple/"}
    if org_id is not None:
        request.META["HTTP_X_RH_IDENTITY"] = _encode_identity_header(org_id)
    return request


def _make_anonymous_user():
    user = MagicMock()
    user.is_authenticated = False
    user.is_superuser = False
    return user


def _make_authenticated_user(in_readonly_group=False):
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = False
    user.groups.values_list.return_value = []
    user.groups.filter.return_value.exists.return_value = in_readonly_group
    return user


def _make_domain(name):
    domain = SimpleNamespace(name=name, pk=42)
    return domain


def _make_pypi_view():
    """Create a mock view that is an instance of PyPIMixin."""
    from pulp_python.app.pypi.views import PyPIMixin

    class FakePyPIView(PyPIMixin):
        pass

    view = FakePyPIView()
    return view


def _make_regular_view():
    """Create a mock view that is NOT a PyPIMixin."""
    return MagicMock()


class TestSafeMethodBypass:
    """Verify that safe methods on public domains are allowed for all users, and PyPI views
    on public domains bypass the lightwell-network feature check entirely."""

    def test_anonymous_get_pypi_view_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_get_pypi_view_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_anonymous_head_pypi_view_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="HEAD", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_head_pypi_view_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="HEAD", user=_make_authenticated_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_anonymous_get_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_get_public_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    def test_public_domain_pypi_view_never_checks_feature(self, mock_feature_check):
        permission = DomainBasedPermission()
        domain = _make_domain("public-something")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_not_called()

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    def test_anonymous_get_pypi_view_non_lightwell_domain_allowed(self, mock_feature_check):
        """Domains other than "lightwell" keep the pre-existing behavior: any SAFE_METHOD
        request to a PyPI view is allowed, without any feature check."""
        permission = DomainBasedPermission()
        domain = _make_domain("some-other-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_not_called()

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    def test_authenticated_get_pypi_view_non_lightwell_domain_allowed(self, mock_feature_check):
        permission = DomainBasedPermission()
        domain = _make_domain("some-other-domain")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_not_called()

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    def test_anonymous_get_pypi_view_no_domain_allowed(self, mock_feature_check):
        """No domain resolved on the request (shouldn't happen in practice for PyPI views,
        but matches the pre-existing default-allow behavior)."""
        permission = DomainBasedPermission()
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=None)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_not_called()


class TestLightwellDomainPyPIFeatureCheck:
    """Verify the lightwell-network feature check, scoped specifically to the "lightwell"
    domain's PyPI views."""

    def test_anonymous_no_org_id_denied(self):
        """Anonymous requests carry no org_id, so there's nothing to check the feature for."""
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is False

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_authenticated_with_feature_allowed(self, mock_domain_org, mock_feature_check):
        mock_domain_org.filter.return_value.exists.return_value = False
        mock_feature_check.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="20368420")
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_called_once_with("20368420")

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_authenticated_without_feature_denied(self, mock_domain_org, mock_feature_check):
        mock_domain_org.filter.return_value.exists.return_value = False
        mock_feature_check.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="1979710")
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is False

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    def test_unauthenticated_with_org_id_and_feature_allowed(self, mock_feature_check):
        """An org_id can be present even without a fully authenticated user; the feature
        check still applies (and still gates access) in that case."""
        mock_feature_check.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="20368420")
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_domain_org_association_bypasses_feature_check(self, mock_domain_org, mock_feature_check):
        """Users with a DomainOrg association must not be denied by (or even trigger) the
        feature check."""
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="1979710")
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True
        mock_feature_check.assert_not_called()

    @patch("pulp_service.app.models.FeatureContentGuard._get_cached_result", return_value=None)
    @patch("pulp_service.app.models.FeatureContentGuard._check_for_feature", side_effect=PermissionError)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_feature_service_failure_fails_closed(self, mock_domain_org, mock_check_feature, mock_cache_result):
        """If the Features Service call fails, access must be denied, not silently allowed."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="1979710")
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is False


class TestLightwellPyPIAccessLogging:
    """
    Verify _has_pypi_read_access logs its allow/deny decision and the reason for it (DomainOrg
    match, missing org_id, or feature check outcome), so incidents can be diagnosed from logs
    alone -- see the discussion that prompted this in the "brand new user can read lightwell
    pypi" investigation.
    """

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_domain_org_grant_is_logged(self, mock_domain_org, caplog):
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="1979710")
        view = _make_pypi_view()

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is True

        assert any(
            "GRANTED via DomainOrg" in record.message and "1979710" in record.message
            for record in caplog.records
        )

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_missing_org_id_denial_is_logged(self, mock_domain_org, caplog):
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain)
        view = _make_pypi_view()

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is False

        assert any("DENIED: no org_id" in record.message for record in caplog.records)

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_feature_check_outcome_is_logged(self, mock_domain_org, mock_feature_check, caplog):
        mock_domain_org.filter.return_value.exists.return_value = False
        mock_feature_check.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="20368420")
        view = _make_pypi_view()

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is True

        assert any(
            "GRANTED via lightwell-network feature check" in record.message and "20368420" in record.message
            for record in caplog.records
        )

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature")
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_feature_check_denial_is_logged(self, mock_domain_org, mock_feature_check, caplog):
        mock_domain_org.filter.return_value.exists.return_value = False
        mock_feature_check.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="1979710")
        view = _make_pypi_view()

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is False

        assert any(
            "DENIED via lightwell-network feature check" in record.message and "1979710" in record.message
            for record in caplog.records
        )


class TestLightwellReadOnlyGroupAccess:
    """Verify the LIGHTWELL_READONLY_GROUP_NAME group grants read-only access to the
    lightwell domain's non-PyPI endpoints, independent of any DomainOrg association, and
    that it never grants write access or bypasses the PyPI feature check."""

    def test_readonly_group_member_get_non_pypi_lightwell_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_non_member_get_non_pypi_lightwell_denied(self, mock_domain_org, mock_get_domain_pk):
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=False), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_readonly_group_member_write_non_pypi_lightwell_denied(self, mock_domain_org, mock_get_domain_pk):
        """Group membership only grants SAFE_METHOD access; write requests must still go
        through the standard DomainOrg-based checks."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(
            method="POST",
            user=_make_authenticated_user(in_readonly_group=True),
            domain=domain,
            view_name="repositories-list",
        )

        assert permission.has_permission(request, MagicMock()) is False

    @patch("pulp_service.app.authorization.DomainBasedPermission._has_lightwell_network_feature", return_value=False)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_readonly_group_member_pypi_view_still_requires_feature(self, mock_domain_org, mock_feature_check):
        """Group membership must not bypass the lightwell-network feature check on PyPI
        views -- a member with no feature entitlement and no DomainOrg association is
        still denied."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(
            method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain, org_id="1979710"
        )
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is False
        mock_feature_check.assert_called_once_with("1979710")

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_readonly_group_membership_no_effect_on_other_domains(self, mock_domain_org, mock_get_domain_pk):
        """Membership in the lightwell read-only group grants no access to any domain other
        than lightwell."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("some-other-domain")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False


class TestSafeMethodDenied:
    """Verify that safe methods are denied when they should be."""

    def test_anonymous_get_private_domain_non_pypi_denied(self):
        permission = DomainBasedPermission()
        domain = _make_domain("my-private-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    def test_anonymous_get_no_domain_non_pypi_denied(self):
        permission = DomainBasedPermission()
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=None)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False


class TestUnsafeMethodsDenied:
    """Verify that unsafe methods still require domain access."""

    def test_anonymous_post_pypi_view_denied(self):
        permission = DomainBasedPermission()
        request = _make_request(method="POST", user=_make_anonymous_user())
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is False

    def test_anonymous_post_public_domain_denied(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-trusted-libraries")
        request = _make_request(method="POST", user=_make_anonymous_user(), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_authenticated_post_pypi_view_checks_domain_access(self, mock_domain_org, mock_get_domain_pk):
        permission = DomainBasedPermission()
        request = _make_request(
            method="POST",
            user=_make_authenticated_user(),
            view_name="simple-detail",
        )
        request.META["HTTP_X_RH_IDENTITY"] = ""
        view = _make_pypi_view()

        mock_domain_org.filter.return_value.exists.return_value = False

        assert permission.has_permission(request, view) is False


class TestSuperuserBypass:
    """Verify that superusers bypass all checks."""

    def test_superuser_always_allowed(self):
        permission = DomainBasedPermission()
        user = MagicMock()
        user.is_superuser = True
        request = _make_request(method="DELETE", user=user)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True
