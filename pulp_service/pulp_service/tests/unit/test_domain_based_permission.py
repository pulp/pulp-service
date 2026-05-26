"""
Unit tests for DomainBasedPermission.has_permission().

These tests mock Django ORM and Pulp internals so they can run without
a live Pulp stack. They cover the safe-method bypass for PyPI views
and public domains, for both anonymous and authenticated users.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulp_service.app.authorization import DomainBasedPermission


def _make_request(method="GET", user=None, domain=None, view_name="pypi-metadata"):
    """Build a mock DRF request."""
    request = MagicMock()
    request.method = method
    request.user = user or _make_anonymous_user()
    request.pulp_domain = domain
    request.resolver_match = MagicMock()
    request.resolver_match.view_name = view_name
    request.META = {"REQUEST_METHOD": method, "PATH_INFO": "/api/pypi/test/main/simple/"}
    return request


def _make_anonymous_user():
    user = MagicMock()
    user.is_authenticated = False
    user.is_superuser = False
    return user


def _make_authenticated_user():
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = False
    user.groups.values_list.return_value = []
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
    """Verify that safe methods on PyPI views and public domains are allowed for all users."""

    def test_anonymous_get_pypi_view_allowed(self):
        permission = DomainBasedPermission()
        request = _make_request(method="GET", user=_make_anonymous_user())
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_get_pypi_view_allowed(self):
        permission = DomainBasedPermission()
        request = _make_request(method="GET", user=_make_authenticated_user())
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_anonymous_head_pypi_view_allowed(self):
        permission = DomainBasedPermission()
        request = _make_request(method="HEAD", user=_make_anonymous_user())
        view = _make_pypi_view()

        assert permission.has_permission(request, view) is True

    def test_authenticated_head_pypi_view_allowed(self):
        permission = DomainBasedPermission()
        request = _make_request(method="HEAD", user=_make_authenticated_user())
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
