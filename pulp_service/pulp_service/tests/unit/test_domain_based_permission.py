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

from django.test import override_settings

from pulp_service.app.authorization import DomainBasedPermission


def _encode_identity_header(org_id):
    identity = {"identity": {"internal": {"org_id": org_id}}}
    return b64encode(json.dumps(identity).encode()).decode()


def _make_request(
    method="GET",
    user=None,
    domain=None,
    view_name="pypi-metadata",
    org_id=None,
    path_info="/api/pypi/test/main/simple/",
):
    """Build a mock DRF request."""
    request = MagicMock()
    request.method = method
    request.user = user or _make_anonymous_user()
    request.pulp_domain = domain
    request.resolver_match = MagicMock()
    request.resolver_match.view_name = view_name
    request.path_info = path_info
    request.META = {"REQUEST_METHOD": method, "PATH_INFO": path_info}
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


def _make_pypi_view(content_guard=None):
    """Create a mock view that is an instance of PyPIMixin."""
    from pulp_python.app.pypi.views import PyPIMixin

    class FakePyPIView(PyPIMixin):
        pass

    view = FakePyPIView()
    view._distro = SimpleNamespace(content_guard=content_guard)
    return view


def _make_content_guard(permits=True):
    """Create a mock content guard. If permits=False, cast().permit() raises PermissionError"""
    guard = MagicMock()
    casted = MagicMock()
    guard.cast.return_value = casted
    if not permits:
        casted.permit.side_effect = PermissionError("Access denied.")
    return guard


def _make_pypi_view_with_error(error):
    """Create a mock PyPI view whose distribution property raises the given error."""
    from pulp_python.app.pypi.views import PyPIMixin

    class FakePyPIView(PyPIMixin):
        @property
        def distribution(self):
            raise error

    return FakePyPIView()


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

    def test_public_domain_pypi_view_never_checks_feature(self):
        permission = DomainBasedPermission()
        domain = _make_domain("public-something")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view(content_guard=_make_content_guard())

        assert permission.has_permission(request, view) is True

    def test_anonymous_get_pypi_view_non_lightwell_domain_allowed(self):
        """Domains other than "lightwell" keep the pre-existing behavior: any SAFE_METHOD
        request to a PyPI view is allowed, without any feature check."""
        permission = DomainBasedPermission()
        domain = _make_domain("some-other-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_get_pypi_view_non_lightwell_domain_allowed(self):
        permission = DomainBasedPermission()
        domain = _make_domain("some-other-domain")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_anonymous_get_pypi_view_no_domain_allowed(self):
        """No domain resolved on the request (shouldn't happen in practice for PyPI views,
        but matches the pre-existing default-allow behavior)."""
        permission = DomainBasedPermission()
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=None)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True


class TestContentGuardPyPICheck:
    """Verify the content-guard driven access checks on PyPI views. The check is
    domain-name agnostic: any distribution with content guard enforces it."""

    def test_no_guard_allows_anonymous(self):
        """Distributions without content guards allow anonymous SAFE_METHOD access."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_guard_permits_access(self):
        """Content guard permits -> access granted."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is True
        guard.cast.return_value.permit.assert_called_once_with(request)

    def test_guard_denies_access(self):
        """Content guard denies -> access denied."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=False)
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is False

    def test_anonymous_no_header_with_guard_denied(self):
        """Anonymous request with no identity header and no DomainOrg falls through
        to the content guard, which denies access."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=False)
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is False

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_domain_org_bypasses_guard(self, mock_domain_org):
        """Users with a DomainOrg association bypass the content guard entirely."""
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=False)
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is True
        guard.cast.return_value.permit.assert_not_called()

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_no_domain_org_falls_through_to_guard(self, mock_domain_org):
        """Without a DomainOrg association, the content guard is checked."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is True
        guard.cast.return_value.permit.assert_called_once()

    def test_guard_works_on_any_domain_name(self):
        """The content guard check is domain-name-agnostic, no hardcoded names."""
        for domain_name in ["lightwell", "my-domain", "domain-xyz", "domain-abc"]:
            permission = DomainBasedPermission()
            guard = _make_content_guard(permits=True)
            domain = _make_domain(domain_name)
            request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
            view = _make_pypi_view(content_guard=guard)

            assert permission.has_permission(request, view) is True


class TestContentGuardAccessLogging:
    """Verify that the content-guard PyPI access check logs its allow/deny decision
    and the reason for it (DomainOrg match or content guard outcome)."""

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_domain_org_grant_is_logged(self, mock_domain_org, caplog):
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=False)
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is True

        assert any("GRANTED via DomainOrg" in record.message and "12345" in record.message for record in caplog.records)

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_guard_grant_is_logged(self, mock_domain_org, caplog):
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is True

        assert any(
            "GRANTED via content guard" in record.message and "12345" in record.message for record in caplog.records
        )

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_guard_denial_is_logged(self, mock_domain_org, caplog):
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=False)
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        with caplog.at_level("INFO", logger="pulp_service.app.authorization"):
            assert permission.has_permission(request, view) is False

        assert any(
            "DENIED via content guard" in record.message and "12345" in record.message for record in caplog.records
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

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_readonly_group_member_pypi_view_still_requires_guard(self, mock_domain_org, mock_get_domain_pk):
        """Group membership must not bypass the content guard on PyPI views -- a member
        with no DomainOrg association is still denied if the guard denies."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        guard = _make_content_guard(permits=False)
        request = _make_request(
            method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain, org_id="1979710"
        )
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is False

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


class TestDistributionResolutionErrors:
    """Verify exception handling when view.distribution raises."""

    def test_http404_returns_true(self):
        """Http404 lets view handle 404 naturally -- doesn't deny access at permission layer."""
        from django.http import Http404

        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view_with_error(Http404("not found"))

        assert permission.has_permission(request, view) is True

    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_unexpected_error_fails_closed(self, mock_domain_org, mock_get_domain_pk):
        """Unexpected errors fail closed even if user would otherwise have domain access."""
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view_with_error(RuntimeError("db timeout"))

        assert permission.has_permission(request, view) is False

    def test_unexpected_error_is_logged(self, caplog):
        """Unexpected errors are logged with exception details."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain)
        view = _make_pypi_view_with_error(RuntimeError("db timeout"))

        with caplog.at_level("ERROR", logger="pulp_service.app.authorization"):
            permission.has_permission(request, view)

        assert any("Unexpected error resolving distribution" in r.message for r in caplog.records)


class TestContentGuardCastFailure:
    """Verify that guard.cast() failures fail closed instead of bubbling as 500s."""

    def test_cast_failure_returns_false(self):
        """If guard.cast() raises (orphaned FK, race condition), fail closed."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        guard.cast.side_effect = Exception("content guard subclass row deleted")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is False

    def test_cast_failure_is_logged(self, caplog):
        """cast() failures are logged with exception details."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        guard.cast.side_effect = Exception("content guard subclass row deleted")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        with caplog.at_level("ERROR", logger="pulp_service.app.authorization"):
            permission.has_permission(request, view)

        assert any("Failed to resolve content guard" in r.message for r in caplog.records)

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_cast_failure_not_reached_when_domain_org_matches(self, mock_domain_org):
        """DomainOrg bypass short-circuits before cast() is ever called."""
        mock_domain_org.filter.return_value.exists.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        guard.cast.side_effect = Exception("should not be called")
        request = _make_request(method="GET", user=_make_authenticated_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is True
        guard.cast.assert_not_called()


class TestPermitUnexpectedError:
    """Verify that unexpected errors from casted_guard.permit() fail closed instead of raising 500s."""

    def test_permit_unexpected_error_fails_closed(self):
        """If permit() raises something other than PermissionError, deny access."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        guard.cast.return_value.permit.side_effect = RuntimeError("unexpected guard failure")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        assert permission.has_permission(request, view) is False

    def test_permit_unexpected_error_is_logged(self, caplog):
        """Unexpected permit() errors are logged with exception details."""
        permission = DomainBasedPermission()
        domain = _make_domain("any-domain")
        guard = _make_content_guard(permits=True)
        guard.cast.return_value.permit.side_effect = RuntimeError("unexpected guard failure")
        request = _make_request(method="GET", user=_make_anonymous_user(), domain=domain, org_id="12345")
        view = _make_pypi_view(content_guard=guard)

        with caplog.at_level("ERROR", logger="pulp_service.app.authorization"):
            permission.has_permission(request, view)

        assert any("Unexpected error" in r.message and "guard permit" in r.message for r in caplog.records)


_MULTI_DOMAIN_POLICIES = {
    "lightwell": {
        "readonly_group": "Lightwell-ReadOnly",
        "subscription_feature": "lightwell-network",
        "subscription_endpoints": ["/api/v3/content/"],
    },
    "acme": {
        "readonly_group": "Acme-ReadOnly",
        "subscription_feature": "acme-feature",
        "subscription_endpoints": ["/api/v3/content/"],
    },
}


class TestGenericDomainAccessPolicies:
    """Verify DOMAIN_ACCESS_POLICIES supports multiple domains, that an empty
    config disables policy-based access, and that subscription endpoint path
    matching works correctly."""

    @override_settings(DOMAIN_ACCESS_POLICIES={})
    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_empty_policies_readonly_group_no_access(self, mock_domain_org, mock_get_domain_pk):
        """With no policies configured, readonly group membership grants nothing."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    @override_settings(DOMAIN_ACCESS_POLICIES=_MULTI_DOMAIN_POLICIES)
    def test_second_domain_readonly_group_grants_access(self):
        """A second domain's readonly_group policy grants read access."""
        permission = DomainBasedPermission()
        domain = _make_domain("acme")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True

    @override_settings(DOMAIN_ACCESS_POLICIES=_MULTI_DOMAIN_POLICIES)
    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_second_domain_write_denied(self, mock_domain_org, mock_get_domain_pk):
        """Readonly group on a second domain still denies write access."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("acme")
        request = _make_request(
            method="POST",
            user=_make_authenticated_user(in_readonly_group=True),
            domain=domain,
            view_name="repositories-list",
        )

        assert permission.has_permission(request, MagicMock()) is False

    @override_settings(DOMAIN_ACCESS_POLICIES={"acme": {"readonly_group": "Acme-ReadOnly"}})
    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_policy_scoped_to_own_domain(self, mock_domain_org, mock_get_domain_pk):
        """A policy for 'acme' grants nothing on 'other-domain'."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("other-domain")
        request = _make_request(method="GET", user=_make_authenticated_user(in_readonly_group=True), domain=domain)
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    @override_settings(
        DOMAIN_ACCESS_POLICIES={
            "lightwell": {
                "readonly_group": "",
                "subscription_feature": "lightwell-network",
                "subscription_endpoints": ["/api/v3/content/"],
            },
        }
    )
    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_subscription_path_no_match_skips_check(self, mock_domain_org, mock_get_domain_pk):
        """When request path doesn't match subscription_endpoints and no readonly_group,
        no policy-based access is granted."""
        mock_domain_org.filter.return_value.exists.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(
            method="GET",
            user=_make_authenticated_user(),
            domain=domain,
            path_info="/api/v3/repositories/",
            org_id="12345",
        )
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False

    @override_settings(
        DOMAIN_ACCESS_POLICIES={
            "lightwell": {
                "readonly_group": "",
                "subscription_feature": "lightwell-network",
                "subscription_endpoints": ["/api/v3/content/"],
            },
        }
    )
    @patch("pulp_service.app.authorization.FeatureContentGuard")
    def test_subscription_path_matches_and_entitled(self, mock_guard_cls):
        """When path matches subscription_endpoints and org has the feature, access granted."""
        mock_guard_cls.return_value.check_feature.return_value = True
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(
            method="GET",
            user=_make_authenticated_user(),
            domain=domain,
            path_info="/api/v3/content/rpm/packages/",
            org_id="12345",
        )
        view = _make_regular_view()

        assert permission.has_permission(request, view) is True
        mock_guard_cls.assert_called_once_with(features=["lightwell-network"])
        mock_guard_cls.return_value.check_feature.assert_called_once_with("12345")

    @override_settings(
        DOMAIN_ACCESS_POLICIES={
            "lightwell": {
                "readonly_group": "",
                "subscription_feature": "lightwell-network",
                "subscription_endpoints": ["/api/v3/content/"],
            },
        }
    )
    @patch("pulp_service.app.authorization.FeatureContentGuard")
    @patch("pulp_service.app.authorization.get_domain_pk", return_value=42)
    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_subscription_path_matches_but_not_entitled(self, mock_domain_org, mock_get_domain_pk, mock_guard_cls):
        """When path matches but org lacks the feature and no readonly_group, denied."""
        mock_domain_org.filter.return_value.exists.return_value = False
        mock_guard_cls.return_value.check_feature.return_value = False
        permission = DomainBasedPermission()
        domain = _make_domain("lightwell")
        request = _make_request(
            method="GET",
            user=_make_authenticated_user(),
            domain=domain,
            path_info="/api/v3/content/rpm/packages/",
            org_id="12345",
        )
        view = _make_regular_view()

        assert permission.has_permission(request, view) is False
