"""
Unit tests for content_view_util.py.

These mock Django ORM/pulpcore internals so they run without a live Pulp stack, mirroring the
style used by test_domain_based_permission.py.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pulpcore.app.models import RepositoryVersion

from pulp_service.app.content_view_util import (
    STATUS_NO_DOMAIN_ACCESS,
    STATUS_NO_VERSION,
    STATUS_OK,
    SortField,
    content_for_versions,
    db_ordering,
    distinct_keys,
    group_versions_by_domain,
    resolve_content_view_distributions,
    resolve_repository_versions,
    scatter_gather,
    sort_key,
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


def _make_version(domain):
    version = MagicMock()
    version.repository.pulp_domain = domain
    return version


class TestResolveRepositoryVersions:
    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    @patch("pulp_service.app.content_view_util.NamedModelViewSet.get_resource")
    def test_resolves_ok_when_accessible(self, mock_get_resource, mock_can_view):
        domain = _make_domain("my-domain")
        version = _make_version(domain)
        mock_get_resource.return_value = version
        mock_can_view.return_value = True

        resolutions = resolve_repository_versions(["/pulp/my-domain/api/v3/.../"], _make_user())

        assert len(resolutions) == 1
        assert resolutions[0].status == STATUS_OK
        assert resolutions[0].repository_version is version
        assert resolutions[0].domain is domain

    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    @patch("pulp_service.app.content_view_util.NamedModelViewSet.get_resource")
    def test_excludes_domain_without_access(self, mock_get_resource, mock_can_view):
        domain = _make_domain("other-domain")
        mock_get_resource.return_value = _make_version(domain)
        mock_can_view.return_value = False

        resolutions = resolve_repository_versions(["/pulp/other-domain/api/v3/.../"], _make_user())

        assert resolutions[0].status == STATUS_NO_DOMAIN_ACCESS
        assert resolutions[0].repository_version is None
        assert resolutions[0].domain is domain

    @patch("pulp_service.app.content_view_util.user_can_view_domain")
    @patch("pulp_service.app.content_view_util.NamedModelViewSet.get_resource")
    def test_resolves_each_href_independently(self, mock_get_resource, mock_can_view):
        domain_a = _make_domain("a")
        domain_b = _make_domain("b")
        version_a = _make_version(domain_a)
        version_b = _make_version(domain_b)
        mock_get_resource.side_effect = [version_a, version_b]
        mock_can_view.side_effect = [True, False]

        resolutions = resolve_repository_versions(["href-a", "href-b"], _make_user())

        assert [r.status for r in resolutions] == [STATUS_OK, STATUS_NO_DOMAIN_ACCESS]
        assert resolutions[0].repository_version is version_a
        assert resolutions[1].repository_version is None
        mock_get_resource.assert_any_call("href-a", RepositoryVersion)
        mock_get_resource.assert_any_call("href-b", RepositoryVersion)


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


def _row(pk, name):
    return SimpleNamespace(pk=pk, name=name)


class _FakeQuerySet(list):
    """
    Minimal QuerySet stand-in for scatter_gather/distinct_keys tests.

    Rows are given already sorted the way the database would sort them for ascending order (which,
    with ``db_ordering``, is exactly ``sort_key`` order); ``order_by`` with descending expressions
    just reverses them. Every ``filter(pk__in=...)`` call is recorded in ``filter_calls``.
    """

    def __init__(self, rows=(), filter_calls=None):
        super().__init__(rows)
        self.filter_calls = [] if filter_calls is None else filter_calls

    def _clone(self, rows):
        return _FakeQuerySet(rows, self.filter_calls)

    def count(self):
        return len(self)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return self._clone(list.__getitem__(self, item))
        return list.__getitem__(self, item)

    def order_by(self, *expressions):
        if expressions and all(getattr(expression, "descending", False) for expression in expressions):
            return self._clone(reversed(self))
        return self

    def prefetch_related(self, *lookups):
        return self

    def values_list(self, *names):
        return self._clone(tuple(getattr(row, name) for name in names) for row in self)

    def distinct(self):
        return self._clone(dict.fromkeys(self))

    def filter(self, pk__in):
        self.filter_calls.append(list(pk__in))
        return self._clone(row for row in self if row.pk in pk__in)


def _passthrough_with_domain(mock_with_domain):
    mock_with_domain.return_value.__enter__.return_value = None
    mock_with_domain.return_value.__exit__.return_value = False


NAME_ORDER = (SortField("name"), SortField("pk", text=False))


def _builder(rows_by_versions):
    """A build_queryset returning each versions-list's fixed rows, sharing one filter-call log."""
    filter_calls = []

    def build_queryset(versions):
        return _FakeQuerySet(rows_by_versions[id(versions)], filter_calls)

    build_queryset.filter_calls = filter_calls
    return build_queryset


class TestSortKey:
    def test_text_is_case_insensitive_then_exact(self):
        names = ["Development Tools", "base system", "Beta Group", "Alpha Group", "beta group"]
        key = sort_key((SortField("name"),))
        assert sorted(names, key=lambda name: key([name])) == [
            "Alpha Group",
            "base system",
            "Beta Group",
            "beta group",
            "Development Tools",
        ]

    def test_nulls_last_ascending_first_descending(self):
        key = sort_key((SortField("updated_date"),))
        values = ["2026-01-02", None, "2026-01-01"]
        assert sorted(values, key=lambda value: key([value])) == ["2026-01-01", "2026-01-02", None]
        assert sorted(values, key=lambda value: key([value]), reverse=True) == [None, "2026-01-02", "2026-01-01"]

    def test_raw_fields_use_natural_order(self):
        key = sort_key((SortField("name"), SortField("pk", text=False)))
        rows = [("a", 3), ("A", 1), ("a", 2)]
        assert sorted(rows, key=key) == [("A", 1), ("a", 2), ("a", 3)]


class TestDbOrdering:
    def test_text_field_orders_by_lowered_then_exact_value_in_c_collation(self):
        expressions = db_ordering((SortField("name"),))
        assert len(expressions) == 2
        assert all(not expression.descending and expression.nulls_last for expression in expressions)
        lowered, exact = (expression.expression for expression in expressions)
        assert lowered.collation == "C" and lowered.source_expressions[0].function == "LOWER"
        assert exact.collation == "C"

    def test_raw_field_and_descending(self):
        expressions = db_ordering((SortField("pk", text=False),), descending=True)
        assert len(expressions) == 1
        assert expressions[0].descending and expressions[0].nulls_first
        assert expressions[0].expression.name == "pk"


class TestContentForVersions:
    def test_single_version_uses_get_content(self):
        model = MagicMock()
        version = MagicMock()
        assert content_for_versions(model, [version]) is version.get_content.return_value
        version.get_content.assert_called_once_with(model.objects)

    def test_no_versions_is_empty(self):
        model = MagicMock()
        assert content_for_versions(model, []) is model.objects.none.return_value

    @patch("pulp_service.app.content_view_util.RepositoryVersion")
    def test_multiple_versions_use_a_database_side_subquery(self, mock_repository_version):
        model = MagicMock()
        versions = [SimpleNamespace(pk="v1"), SimpleNamespace(pk="v2")]
        subquery = mock_repository_version.objects.filter.return_value.annotate.return_value.values_list.return_value

        content_for_versions(model, versions)

        mock_repository_version.objects.filter.assert_called_once_with(pk__in=["v1", "v2"])
        model.objects.filter.assert_called_once_with(pk__in=subquery)


class TestScatterGather:
    @patch("pulp_service.app.content_view_util.with_domain")
    def test_no_domains_returns_empty(self, mock_with_domain):
        page, total = scatter_gather({}, build_queryset=MagicMock(), order_by=NAME_ORDER, limit=10)
        assert page == []
        assert total == 0
        mock_with_domain.assert_not_called()

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_single_domain_uses_native_pagination(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain = _make_domain("only")
        versions = [MagicMock()]
        build_queryset = _builder({id(versions): [_row(i, f"pkg{i}") for i in range(5)]})

        page, total = scatter_gather({domain: versions}, build_queryset, order_by=NAME_ORDER, limit=2, offset=1)

        assert total == 5
        assert [row.name for row in page] == ["pkg1", "pkg2"]
        assert all(row.pulp_domain is domain for row in page)
        mock_with_domain.assert_called_once_with(domain)

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_single_domain_descending_reverses_order(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain = _make_domain("only")
        versions = [MagicMock()]
        build_queryset = _builder({id(versions): [_row(i, f"pkg{i}") for i in range(5)]})

        page, total = scatter_gather(
            {domain: versions}, build_queryset, order_by=NAME_ORDER, limit=2, offset=1, descending=True
        )

        assert total == 5
        assert [row.name for row in page] == ["pkg3", "pkg2"]

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_offset_is_not_capped(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain = _make_domain("only")
        versions = [MagicMock()]
        build_queryset = _builder({id(versions): [_row(i, f"pkg{i}") for i in range(3)]})

        page, total = scatter_gather({domain: versions}, build_queryset, order_by=NAME_ORDER, limit=1, offset=20000)

        assert page == []
        assert total == 3

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_multi_domain_merges_mixed_case_like_the_database(self, mock_with_domain):
        # Regression test: the merge used to sort by Python code point (uppercase first) while each
        # domain's rows came back in database collation order, so pages skipped/repeated rows.
        _passthrough_with_domain(mock_with_domain)
        domain_a, domain_b = _make_domain("a"), _make_domain("b")
        versions_a, versions_b = [MagicMock()], [MagicMock()]
        build_queryset = _builder(
            {
                id(versions_a): [_row(1, "apple"), _row(2, "Banana"), _row(3, "date")],
                id(versions_b): [_row(4, "Avocado"), _row(5, "cherry"), _row(6, "Elderberry")],
            }
        )
        versions_by_domain = {domain_a: versions_a, domain_b: versions_b}
        expected = ["apple", "Avocado", "Banana", "cherry", "date", "Elderberry"]

        page, total = scatter_gather(versions_by_domain, build_queryset, order_by=NAME_ORDER, limit=100)
        assert total == 6
        assert [row.name for row in page] == expected
        assert [row.pulp_domain for row in page] == [domain_a, domain_b, domain_a, domain_b, domain_a, domain_b]

        for limit in range(1, 6):
            walked = []
            for offset in range(0, total, limit):
                page, _ = scatter_gather(
                    versions_by_domain, build_queryset, order_by=NAME_ORDER, limit=limit, offset=offset
                )
                walked += [row.name for row in page]
            assert walked == expected, f"page walk with limit={limit}"

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_multi_domain_descending(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain_a, domain_b = _make_domain("a"), _make_domain("b")
        versions_a, versions_b = [MagicMock()], [MagicMock()]
        build_queryset = _builder(
            {
                id(versions_a): [_row(1, "apple"), _row(2, "Banana")],
                id(versions_b): [_row(3, "Avocado"), _row(4, "cherry")],
            }
        )

        page, _ = scatter_gather(
            {domain_a: versions_a, domain_b: versions_b},
            build_queryset,
            order_by=NAME_ORDER,
            limit=3,
            descending=True,
        )

        assert [row.name for row in page] == ["cherry", "Banana", "Avocado"]

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_multi_domain_fetches_full_rows_only_for_the_page(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain_a, domain_b = _make_domain("a"), _make_domain("b")
        versions_a, versions_b = [MagicMock()], [MagicMock()]
        build_queryset = _builder(
            {
                id(versions_a): [_row(1, "a1"), _row(2, "a2"), _row(3, "a3")],
                id(versions_b): [_row(4, "b1"), _row(5, "b2")],
            }
        )

        page, total = scatter_gather(
            {domain_a: versions_a, domain_b: versions_b}, build_queryset, order_by=NAME_ORDER, limit=2, offset=2
        )

        assert total == 5
        assert [row.name for row in page] == ["a3", "b1"]
        assert sorted(build_queryset.filter_calls) == [[3], [4]]

    @patch("pulp_service.app.content_view_util.with_domain")
    def test_count_false_skips_counting(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain = _make_domain("only")
        versions = [MagicMock()]
        build_queryset = _builder({id(versions): [_row(1, "pkg")]})

        page, total = scatter_gather({domain: versions}, build_queryset, order_by=NAME_ORDER, limit=10, count=False)

        assert total is None
        assert [row.name for row in page] == ["pkg"]


class TestDistinctKeys:
    @patch("pulp_service.app.content_view_util.with_domain")
    def test_unions_and_sorts_keys_across_domains(self, mock_with_domain):
        _passthrough_with_domain(mock_with_domain)
        domain_a, domain_b = _make_domain("a"), _make_domain("b")
        versions_a, versions_b = [MagicMock()], [MagicMock()]
        build_queryset = _builder(
            {
                id(versions_a): [_row(1, "base system"), _row(2, "Alpha Group"), _row(3, "base system")],
                id(versions_b): [_row(4, "Development Tools"), _row(5, "Alpha Group")],
            }
        )

        ordered, keys_by_domain = distinct_keys(
            {domain_a: versions_a, domain_b: versions_b}, build_queryset, (SortField("name"),)
        )

        assert ordered == [("Alpha Group",), ("base system",), ("Development Tools",)]
        assert keys_by_domain == {
            domain_a: {("base system",), ("Alpha Group",)},
            domain_b: {("Development Tools",), ("Alpha Group",)},
        }
