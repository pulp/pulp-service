"""
Unit tests for content_view_util.py.

These mock Django ORM/pulpcore internals so they run without a live Pulp stack, mirroring the
style used by test_domain_based_permission.py.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulp_service.app.content_view_util import (
    STATUS_NO_DOMAIN_ACCESS,
    STATUS_NO_VERSION,
    STATUS_OK,
    group_versions_by_domain,
    resolve_content_view_distributions,
    scatter_gather,
    user_can_view_domain,
)


def _make_user(authenticated=True, superuser=False, has_perm_result=False):
    user = MagicMock()
    user.is_authenticated = authenticated
    user.is_superuser = superuser
    user.has_perm.return_value = has_perm_result
    return user


class TestUserCanViewDomain:
    def test_none_user_denied(self):
        assert user_can_view_domain(None, MagicMock()) is False

    def test_unauthenticated_user_denied(self):
        user = _make_user(authenticated=False)
        assert user_can_view_domain(user, MagicMock()) is False

    def test_superuser_always_allowed(self):
        user = _make_user(superuser=True)
        domain = MagicMock()
        assert user_can_view_domain(user, domain) is True
        user.has_perm.assert_not_called()

    def test_model_level_perm_allows(self):
        user = _make_user(has_perm_result=True)
        domain = MagicMock()
        assert user_can_view_domain(user, domain) is True

    def test_object_level_perm_checked_when_model_level_denied(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = False
        domain = MagicMock()

        def has_perm(_perm, obj=None):
            if obj is None:
                return False
            return obj is domain

        user.has_perm.side_effect = has_perm
        assert user_can_view_domain(user, domain) is True

    def test_denied_when_neither_perm_matches(self):
        user = _make_user(has_perm_result=False)
        domain = MagicMock()
        assert user_can_view_domain(user, domain) is False


def _make_domain(name):
    # A plain MagicMock (identity-hashable), unlike types.SimpleNamespace which defines __eq__
    # and is therefore unhashable -- these are used as dict keys below.
    domain = MagicMock()
    domain.name = name
    return domain


def _make_distribution(domain, repository_version, name="dist"):
    distribution = MagicMock()
    distribution.pulp_domain = domain
    distribution.name = name
    distribution.cast.return_value.get_repository_publication_and_version.return_value = (
        None,
        repository_version,
        None,
    )
    return distribution


class TestResolveContentViewDistributions:
    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    def test_excludes_domain_without_access(self, mock_can_view):
        domain = _make_domain("other-domain")
        distribution = _make_distribution(domain, repository_version=MagicMock())
        mock_can_view.return_value = False

        content_view = MagicMock()
        content_view.distributions.select_related.return_value.all.return_value = [distribution]

        user = _make_user()
        resolutions = resolve_content_view_distributions(content_view, user)

        assert len(resolutions) == 1
        assert resolutions[0].status == STATUS_NO_DOMAIN_ACCESS
        assert resolutions[0].repository_version is None

    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    def test_marks_no_version_when_unresolvable(self, mock_can_view):
        domain = _make_domain("my-domain")
        distribution = _make_distribution(domain, repository_version=None)
        mock_can_view.return_value = True

        content_view = MagicMock()
        content_view.distributions.select_related.return_value.all.return_value = [distribution]

        resolutions = resolve_content_view_distributions(content_view, _make_user())

        assert resolutions[0].status == STATUS_NO_VERSION
        assert resolutions[0].repository_version is None

    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    def test_resolves_ok_when_accessible_and_versioned(self, mock_can_view):
        domain = _make_domain("my-domain")
        version = MagicMock()
        distribution = _make_distribution(domain, repository_version=version)
        mock_can_view.return_value = True

        content_view = MagicMock()
        content_view.distributions.select_related.return_value.all.return_value = [distribution]

        resolutions = resolve_content_view_distributions(content_view, _make_user())

        assert resolutions[0].status == STATUS_OK
        assert resolutions[0].repository_version is version
        assert resolutions[0].domain is domain


class TestGroupVersionsByDomain:
    def test_groups_only_ok_resolutions(self):
        domain_a = _make_domain("a")
        domain_b = _make_domain("b")
        version_a1 = MagicMock()
        version_a2 = MagicMock()
        version_b1 = MagicMock()

        resolutions = [
            SimpleNamespace(distribution=MagicMock(), domain=domain_a, repository_version=version_a1, status=STATUS_OK),
            SimpleNamespace(distribution=MagicMock(), domain=domain_a, repository_version=version_a2, status=STATUS_OK),
            SimpleNamespace(distribution=MagicMock(), domain=domain_b, repository_version=version_b1, status=STATUS_OK),
            SimpleNamespace(
                distribution=MagicMock(), domain=domain_b, repository_version=None, status=STATUS_NO_VERSION
            ),
            SimpleNamespace(
                distribution=MagicMock(), domain=domain_a, repository_version=None, status=STATUS_NO_DOMAIN_ACCESS
            ),
        ]

        grouped = group_versions_by_domain(resolutions)

        assert grouped == {domain_a: [version_a1, version_a2], domain_b: [version_b1]}

    def test_empty_resolutions_yield_empty_dict(self):
        assert group_versions_by_domain([]) == {}


class _FakeQuerySet(list):
    """Minimal QuerySet stand-in supporting .count() and slicing, for scatter_gather tests."""

    def count(self):
        return len(self)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return _FakeQuerySet(list.__getitem__(self, item))
        return list.__getitem__(self, item)


class TestScatterGather:
    @patch("pulp_service.app.content_view_util.with_domain")
    def test_no_domains_returns_empty(self, mock_with_domain):
        page, total = scatter_gather({}, build_queryset=MagicMock(), order_by="name", limit=10)
        assert page == []
        assert total == 0
        mock_with_domain.assert_not_called()

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_single_domain_uses_native_pagination(self, mock_with_domain):
        mock_with_domain.return_value.__enter__.return_value = None
        mock_with_domain.return_value.__exit__.return_value = False

        domain = _make_domain("only")
        rows = _FakeQuerySet([{"name": f"pkg{i}"} for i in range(5)])
        build_queryset = MagicMock(return_value=rows)

        page, total = scatter_gather({domain: [MagicMock()]}, build_queryset, order_by="name", limit=2, offset=1)

        assert total == 5
        assert page == [{"name": "pkg1"}, {"name": "pkg2"}]
        mock_with_domain.assert_called_once_with(domain)

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_multi_domain_merges_and_sorts(self, mock_with_domain):
        mock_with_domain.return_value.__enter__.return_value = None
        mock_with_domain.return_value.__exit__.return_value = False

        domain_a = _make_domain("a")
        domain_b = _make_domain("b")

        def build_queryset(versions):
            # Each domain "returns" a fixed, pre-sorted row-set based on identity.
            return _FakeQuerySet(build_queryset.data[id(versions)])

        rows_a = [{"name": "banana"}, {"name": "date"}]
        rows_b = [{"name": "apple"}, {"name": "cherry"}]
        versions_a = [MagicMock()]
        versions_b = [MagicMock()]
        build_queryset.data = {id(versions_a): rows_a, id(versions_b): rows_b}

        page, total = scatter_gather(
            {domain_a: versions_a, domain_b: versions_b},
            build_queryset,
            order_by="name",
            limit=3,
            offset=0,
        )

        assert total == 4
        assert page == [{"name": "apple"}, {"name": "banana"}, {"name": "cherry"}]

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_count_false_skips_counting(self, mock_with_domain):
        mock_with_domain.return_value.__enter__.return_value = None
        mock_with_domain.return_value.__exit__.return_value = False

        domain = _make_domain("only")
        rows = _FakeQuerySet([{"name": "pkg"}])
        build_queryset = MagicMock(return_value=rows)

        page, total = scatter_gather({domain: [MagicMock()]}, build_queryset, order_by="name", limit=10, count=False)

        assert total is None
        assert page == [{"name": "pkg"}]
