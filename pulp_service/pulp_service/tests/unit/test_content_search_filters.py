"""
Unit tests for the query building blocks of the RPM content searches in content_view_viewsets.py:
filters, sort parsing, parameter parsing and the module-stream "lowest version per stream" merge.

These run without a live Pulp stack (the ORM is mocked/stubbed), like test_content_view.py.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from pulp_service.app.content_view_util import SortField
from pulp_service.app.content_view_viewsets import (
    ERRATA_DEFAULT_SORT,
    ERRATA_KNOWN_SEVERITIES,
    ERRATA_KNOWN_TYPES,
    ERRATA_SORT_FIELDS,
    MODULE_STREAM_DEFAULT_SORT,
    MODULE_STREAM_SORT_FIELDS,
    _bool_param,
    _keys_by_domain_for_page,
    _limit_param,
    _lowest_version_streams,
    _offset_param,
    _union_package_lists,
    bucket_filter,
    errata_filter,
    errata_order,
    module_stream_filter,
    module_stream_order,
    package_filter,
    parse_sort_by,
)


def _request(**params):
    return SimpleNamespace(query_params=params)


class TestErrataFilters:
    def test_severity_matches_requested_values_exactly(self):
        assert errata_filter(severities=["Important"]) == Q() & Q(severity__in=["Important"])

    def test_unknown_severity_adds_catch_all_for_values_outside_the_pulp_list(self):
        expected = Q(severity__in=["Critical", "Unknown"]) | ~Q(severity__in=ERRATA_KNOWN_SEVERITIES)
        assert errata_filter(severities=["Critical", "Unknown"]) == Q() & expected

    def test_pulp_severity_values_are_capitalized(self):
        assert ERRATA_KNOWN_SEVERITIES == ("Critical", "Important", "Moderate", "Low")

    def test_lowercase_or_other_values_get_no_catch_all(self):
        # Before: anything outside a lowercase list (e.g. "Important", "None") matched the whole
        # "other" bucket. Now only the literal value matches, as in Tang.
        assert bucket_filter("severity", ["None"], ERRATA_KNOWN_SEVERITIES, "Unknown") == Q(severity__in=["None"])

    def test_other_type_includes_newpackage(self):
        assert "newpackage" not in ERRATA_KNOWN_TYPES
        expected = Q(type__in=["other"]) | ~Q(type__in=("security", "bugfix", "enhancement"))
        assert errata_filter(types=["other"]) == Q() & expected

    def test_search_matches_id_title_and_summary(self):
        expected = Q(id__icontains="widget") | Q(title__icontains="widget") | Q(summary__icontains="widget")
        assert errata_filter(search="widget") == Q() & expected

    def test_no_filters_is_empty(self):
        assert errata_filter() == Q()


class TestPackageAndModuleStreamFilters:
    def test_package_search_is_case_insensitive_prefix(self):
        assert package_filter(search="du") == Q() & Q(name__istartswith="du")

    def test_package_name_is_exact_and_combines_with_search(self):
        assert package_filter(name="duck", search="d") == Q() & Q(name="duck") & Q(name__istartswith="d")

    def test_module_stream_search_matches_name_only(self):
        assert module_stream_filter(search="0") == Q() & Q(name__icontains="0")

    def test_module_stream_rpm_names(self):
        assert module_stream_filter(rpm_names=["duck"]) == Q() & Q(packages__name__in=["duck"])

    @pytest.mark.parametrize(("has_packages", "isnull"), [(True, False), (False, True)])
    def test_module_stream_has_packages(self, has_packages, isnull):
        assert module_stream_filter(has_packages=has_packages) == Q() & Q(packages__isnull=isnull)

    def test_module_stream_has_packages_omitted_returns_everything(self):
        assert module_stream_filter(has_packages=None) == Q()


class TestSortBy:
    def test_errata_default_is_newest_issued_first(self):
        assert parse_sort_by(None, ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT) == ("issued_date", True)

    def test_ascending_and_descending(self):
        assert parse_sort_by("severity", ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT) == ("severity", False)
        assert parse_sort_by("-title", ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT) == ("title", True)

    @pytest.mark.parametrize("value", ["nope", "issued_date:desc", "--id"])
    def test_unsupported_field_is_rejected(self, value):
        with pytest.raises(ValidationError):
            parse_sort_by(value, ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT)

    def test_errata_order_has_tie_breakers(self):
        assert errata_order("issued_date") == (
            SortField("issued_date"),
            SortField("id"),
            SortField("pk", text=False),
        )
        assert errata_order("id") == (SortField("id"), SortField("pk", text=False))

    def test_module_stream_order_has_tie_breakers(self):
        assert parse_sort_by(None, MODULE_STREAM_SORT_FIELDS, MODULE_STREAM_DEFAULT_SORT) == ("name", False)
        assert module_stream_order("version") == (SortField("version"), SortField("name"), SortField("stream"))
        assert module_stream_order("name") == (SortField("name"), SortField("stream"))


class TestParameterParsing:
    def test_offset_is_not_capped(self):
        assert _offset_param(_request(offset="20000")) == 20000

    def test_offset_defaults_and_floors(self):
        assert _offset_param(_request()) == 0
        assert _offset_param(_request(offset="-5")) == 0
        assert _offset_param(_request(offset="abc")) == 0

    def test_limit_is_bounded(self):
        assert _limit_param(_request(limit="5000"), 100, 1000) == 1000
        assert _limit_param(_request(limit="0"), 100, 1000) == 1
        assert _limit_param(_request(), 100, 1000) == 100

    @pytest.mark.parametrize(
        ("value", "expected"), [(None, None), ("", None), ("true", True), ("True", True), ("1", True), ("false", False)]
    )
    def test_bool_param(self, value, expected):
        params = {} if value is None else {"has_packages": value}
        assert _bool_param(_request(**params), "has_packages") is expected

    def test_bool_param_rejects_garbage(self):
        with pytest.raises(ValidationError):
            _bool_param(_request(has_packages="maybe"), "has_packages")


class TestMergeHelpers:
    def test_union_package_lists_keeps_first_and_adds_new_names(self):
        existing = [{"name": "a"}, {"name": "b"}]
        new = [{"name": "b"}, {"name": "c"}]
        assert _union_package_lists(existing, new) == [{"name": "a"}, {"name": "b"}, {"name": "c"}]

    def test_keys_by_domain_for_page_only_returns_domains_holding_page_keys(self):
        domain_a, domain_b, domain_c = MagicMock(), MagicMock(), MagicMock()
        keys_by_domain = {domain_a: {("a",), ("b",)}, domain_b: {("c",)}, domain_c: {("b",), ("d",)}}
        assert _keys_by_domain_for_page([("b",), ("d",)], keys_by_domain) == {
            domain_a: {("b",)},
            domain_c: {("b",), ("d",)},
        }


class _FakeModuleQuerySet:
    """Stand-in for the ``order_by().distinct().values_list()`` chain in _lowest_version_streams."""

    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *fields):
        return self

    def distinct(self, *fields):
        return self

    def values_list(self, *fields):
        return self.rows


class TestLowestVersionStreams:
    @patch("pulp_service.app.content_view_viewsets.with_domain")
    def test_keeps_lowest_version_per_name_stream_across_domains(self, mock_with_domain):
        mock_with_domain.return_value.__enter__.return_value = None
        mock_with_domain.return_value.__exit__.return_value = False
        domain_a, domain_b = MagicMock(), MagicMock()
        versions_a, versions_b = [MagicMock()], [MagicMock()]
        # Each domain already returns one row per (name, stream) (DISTINCT ON in SQL).
        rows = {
            id(versions_a): [
                ("pk-a1", "duck", "0", "20180730233102", "c0ffee42", "noarch"),
                ("pk-a2", "duck", "4", "20190619105003", "c0ffee42", "noarch"),
                ("pk-a3", "walrus", "0.71", "20180707144203", "c0ffee42", "noarch"),
            ],
            id(versions_b): [
                ("pk-b1", "duck", "0", "20180704244205", "c0ffee42", "noarch"),
                ("pk-b2", "duck", "4", "20190619105003", "c0ffee42", "noarch"),
                ("pk-b3", "kangaroo", "0", "20180730223407", "c0ffee42", "noarch"),
            ],
        }

        streams = _lowest_version_streams(
            {domain_a: versions_a, domain_b: versions_b},
            lambda versions: _FakeModuleQuerySet(rows[id(versions)]),
        )

        by_key = {(stream["name"], stream["stream"]): stream for stream in streams}
        assert set(by_key) == {("duck", "0"), ("duck", "4"), ("walrus", "0.71"), ("kangaroo", "0")}
        assert by_key[("duck", "0")]["pk"] == "pk-b1"  # lower version lives in domain B
        assert by_key[("duck", "0")]["domain"] is domain_b
        assert by_key[("duck", "4")]["pk"] == "pk-a2"  # equal versions: first domain wins
