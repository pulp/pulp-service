"""Tests for PulpServiceAccessPolicy."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from django.contrib.auth.models import Group as AuthGroup
from django.http import Http404

from pulp_service.app.access_policy import PulpServiceAccessPolicy


class TestPulpServiceAccessPolicyLoads:
    """Verify the class can be imported and instantiated."""

    def test_class_exists_and_is_importable(self):
        policy = PulpServiceAccessPolicy()
        assert policy is not None

    def test_inherits_from_access_policy_from_db(self):
        from pulpcore.app.access_policy import AccessPolicyFromDB

        assert issubclass(PulpServiceAccessPolicy, AccessPolicyFromDB)


class TestSuperuserBypass:
    """Superusers should bypass all checks."""

    def test_superuser_allowed(self):
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
        view = SimpleNamespace()
        assert policy.has_permission(request, view) is True

    def test_non_superuser_falls_through(self):
        """A non-superuser with no domain context should fall through to RBAC."""
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=True),
            method="GET",
            pulp_domain=None,
        )
        view = SimpleNamespace()

        # super().has_permission() will fail without a real policy, so we mock it
        with patch.object(
            PulpServiceAccessPolicy.__bases__[0],
            "has_permission",
            return_value=False,
        ):
            assert policy.has_permission(request, view) is False


class TestPublicDomainAccess:
    """Safe methods on public-* domains should be allowed for anyone."""

    def test_get_on_public_domain_allowed(self):
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="public-test-repo", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False),
            method="GET",
            pulp_domain=domain,
        )
        view = SimpleNamespace()
        assert policy.has_permission(request, view) is True

    def test_head_on_public_domain_allowed(self):
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="public-my-packages", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False),
            method="HEAD",
            pulp_domain=domain,
        )
        view = SimpleNamespace()
        assert policy.has_permission(request, view) is True

    def test_get_on_public_domain_allowed_for_anonymous_user(self):
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="public-test-repo", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=False),
            method="GET",
            pulp_domain=domain,
        )
        view = SimpleNamespace()
        assert policy.has_permission(request, view) is True

    def test_head_on_public_domain_allowed_for_anonymous_user(self):
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="public-test-repo", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=False),
            method="HEAD",
            pulp_domain=domain,
        )
        view = SimpleNamespace()
        assert policy.has_permission(request, view) is True

    def test_post_on_public_domain_not_shortcircuited(self):
        """POST on a public domain should NOT be auto-allowed - it falls through to RBAC."""
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="public-test-repo", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=True),
            method="POST",
            pulp_domain=domain,
        )
        view = SimpleNamespace()

        with patch.object(
            PulpServiceAccessPolicy.__bases__[0],
            "has_permission",
            return_value=False,
        ):
            assert policy.has_permission(request, view) is False

    def test_non_public_domain_not_shortcircuited(self):
        """GET on a non-public domain should fall through to RBAC."""
        policy = PulpServiceAccessPolicy()
        domain = SimpleNamespace(name="my-private-domain", pk=1)
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=True),
            method="GET",
            pulp_domain=domain,
        )
        view = SimpleNamespace()

        with patch.object(
            PulpServiceAccessPolicy.__bases__[0],
            "has_permission",
            return_value=False,
        ):
            assert policy.has_permission(request, view) is False


class TestPyPIContentGuardDelegation:
    """PyPI views should delegate to content guard logic."""

    def test_non_pypi_view_returns_none(self):
        """Non-PyPI views should fall through (None) to RBAC."""
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")
        view = SimpleNamespace()  # Not a PyPIMixin instance
        domain = SimpleNamespace(name="some-domain", pk=1)

        result = policy._check_pypi_safe_method_access(request, view, domain)
        assert result is None

    def test_pypi_view_no_content_guard_allowed(self):
        """PyPI view with no content guard - allow."""
        from pulp_python.app.pypi.views import PyPIMixin

        policy = PulpServiceAccessPolicy()

        view = MagicMock(spec=PyPIMixin)
        view.distribution = MagicMock()
        view.distribution.content_guard = None

        result = policy._check_pypi_safe_method_access(
            SimpleNamespace(method="GET"),
            view,
            SimpleNamespace(name="test", pk=1),
        )
        assert result is True

    def test_pypi_view_distribution_404_allows_access(self):
        from pulp_python.app.pypi.views import PyPIMixin

        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")
        domain = SimpleNamespace(name="some-domain", pk=1)

        view = MagicMock(spec=PyPIMixin)
        type(view).distribution = PropertyMock(side_effect=Http404("not found"))

        result = policy._check_pypi_safe_method_access(request, view, domain)
        assert result is True

    def test_pypi_view_distribution_error_denies_and_logs(self, caplog):
        from pulp_python.app.pypi.views import PyPIMixin

        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")
        domain = SimpleNamespace(name="some-domain", pk=1)

        view = MagicMock(spec=PyPIMixin)
        type(view).distribution = PropertyMock(side_effect=RuntimeError("db failure"))

        with caplog.at_level(logging.WARNING):
            result = policy._check_pypi_safe_method_access(request, view, domain)

        assert result is False
        assert any("distribution" in r.getMessage().lower() for r in caplog.records)

    def test_content_guard_cast_error_denies_access(self, caplog):
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")

        guard = Mock()
        guard.cast.side_effect = RuntimeError("cast failed")

        with caplog.at_level(logging.WARNING):
            result = policy._evaluate_content_guard(guard, request, org_id=None, user=None)

        assert result is False
        assert any("content guard" in r.getMessage().lower() for r in caplog.records)

    def test_content_guard_permit_success_allows_access(self):
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")

        casted_guard = Mock()
        casted_guard.permit.return_value = None

        guard = Mock()
        guard.cast.return_value = casted_guard

        result = policy._evaluate_content_guard(guard, request, org_id=None, user=None)
        assert result is True
        casted_guard.permit.assert_called_once_with(request)

    def test_content_guard_permit_permission_error_denies(self):
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")

        casted_guard = Mock()
        casted_guard.permit.side_effect = PermissionError("denied")

        guard = Mock()
        guard.cast.return_value = casted_guard

        result = policy._evaluate_content_guard(guard, request, org_id=None, user=None)
        assert result is False
        casted_guard.permit.assert_called_once_with(request)

    def test_content_guard_permit_unexpected_error_denies_and_logs(self, caplog):
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(method="GET")

        casted_guard = Mock()
        casted_guard.permit.side_effect = RuntimeError("unexpected")

        guard = Mock()
        guard.cast.return_value = casted_guard

        with caplog.at_level(logging.WARNING):
            result = policy._evaluate_content_guard(guard, request, org_id=None, user=None)

        assert result is False
        assert any("content guard" in r.getMessage().lower() for r in caplog.records)

    def test_domain_access_short_circuits_before_content_guard(self):
        """DomainOrg access should grant access without evaluating the content guard."""
        from pulp_python.app.pypi.views import PyPIMixin

        policy = PulpServiceAccessPolicy()
        user = SimpleNamespace(is_authenticated=True)
        request = SimpleNamespace(method="GET", user=user, META={})
        domain = SimpleNamespace(name="org-domain", pk=1)

        view = MagicMock(spec=PyPIMixin)
        view.distribution = MagicMock()
        view.distribution.content_guard = MagicMock()

        with (
            patch.object(PulpServiceAccessPolicy, "_has_domain_access", return_value=True),
            patch.object(policy, "_evaluate_content_guard") as mock_eval,
        ):
            result = policy._check_pypi_safe_method_access(request, view, domain)

        assert result is True
        mock_eval.assert_not_called()

    def test_non_safe_method_skips_pypi_check(self):
        """POST on a PyPI view should skip _check_pypi_safe_method_access and defer to RBAC."""
        policy = PulpServiceAccessPolicy()
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False, is_authenticated=True),
            method="POST",
            pulp_domain=SimpleNamespace(name="some-domain", pk=1),
        )
        view = SimpleNamespace()

        with (
            patch.object(
                PulpServiceAccessPolicy.__bases__[0],
                "has_permission",
                return_value=True,
            ) as mock_super,
            patch.object(policy, "_check_pypi_safe_method_access") as mock_pypi_check,
        ):
            result = policy.has_permission(request, view)

        assert result is True
        mock_super.assert_called_once_with(request, view)
        mock_pypi_check.assert_not_called()


class TestScopeQueryset:
    """scope_queryset should add public domains for Domain model listings."""

    def test_non_domain_model_passes_through(self):
        """Non-Domain querysets should pass through unchanged."""
        policy = PulpServiceAccessPolicy()
        view = SimpleNamespace(request=SimpleNamespace())

        group_qs = AuthGroup.objects.all()

        with patch.object(
            PulpServiceAccessPolicy.__bases__[0],
            "scope_queryset",
            return_value=group_qs,
        ):
            result = policy.scope_queryset(view, group_qs)

        assert result is group_qs
