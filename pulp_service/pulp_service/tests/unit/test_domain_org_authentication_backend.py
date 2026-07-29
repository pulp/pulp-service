"""
Unit tests for DomainOrgAuthenticationBackend.has_perm().

These mock DomainOrg's ORM manager so they can run without a live Pulp stack. They
cover the object-level ``core.view_domain`` permission check via direct user or
group DomainOrg association, and the various short-circuit cases (wrong
permission name, no object, non-Domain object, unauthenticated user).
"""

from unittest.mock import MagicMock, patch

from pulpcore.plugin.models import Domain

from pulp_service.app.authorization import DomainOrgAuthenticationBackend


def _make_domain(pk=42):
    """A real (unsaved) Domain instance, so isinstance(obj, Domain) holds."""
    return Domain(pk=pk, name="some-domain")


def _make_authenticated_user(group_pks=None):
    user = MagicMock()
    user.is_authenticated = True
    user.groups.values_list.return_value = group_pks or []
    return user


def _make_anonymous_user():
    user = MagicMock()
    user.is_authenticated = False
    return user


class TestDomainOrgAuthenticationBackend:
    def test_wrong_permission_name_denied(self):
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user()
        domain = _make_domain()

        assert backend.has_perm(user, "core.change_domain", obj=domain) is False

    def test_no_object_denied(self):
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user()

        assert backend.has_perm(user, "core.view_domain", obj=None) is False

    def test_non_domain_object_denied(self):
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user()

        assert backend.has_perm(user, "core.view_domain", obj=object()) is False

    def test_unauthenticated_user_denied(self):
        backend = DomainOrgAuthenticationBackend()
        user = _make_anonymous_user()
        domain = _make_domain()

        assert backend.has_perm(user, "core.view_domain", obj=domain) is False

    def test_no_user_denied(self):
        backend = DomainOrgAuthenticationBackend()
        domain = _make_domain()

        assert backend.has_perm(None, "core.view_domain", obj=domain) is False

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_direct_user_association_grants(self, mock_domain_org):
        mock_domain_org.filter.return_value.exists.return_value = True
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user()
        domain = _make_domain()

        assert backend.has_perm(user, "core.view_domain", obj=domain) is True
        mock_domain_org.filter.assert_called_once()

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_group_association_grants(self, mock_domain_org):
        mock_domain_org.filter.return_value.exists.return_value = True
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user(group_pks=[1, 2])
        domain = _make_domain()

        assert backend.has_perm(user, "core.view_domain", obj=domain) is True

    @patch("pulp_service.app.authorization.DomainOrg.objects")
    def test_no_association_denied(self, mock_domain_org):
        mock_domain_org.filter.return_value.exists.return_value = False
        backend = DomainOrgAuthenticationBackend()
        user = _make_authenticated_user()
        domain = _make_domain()

        assert backend.has_perm(user, "core.view_domain", obj=domain) is False
