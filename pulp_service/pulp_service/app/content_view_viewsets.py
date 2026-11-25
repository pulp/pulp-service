"""
The ContentView resource (``content-views/``) and its nested, read-only RPM search endpoints
(``content-views/{content_view_pk}/search/rpm/...``), plus the search functions shared with the
ad-hoc ``rpm/content-search/...`` endpoints (``content_search_viewsets.py``).

Each search resolves a set of RepositoryVersions grouped by domain (from a ContentView's
Distributions, or from ``repository_version`` query parameters) and queries RPM content across
them via ``content_view_util.py``: ``scatter_gather`` for the row-per-content listings (packages,
errata) and ``distinct_keys`` for the searches that deduplicate rows by a key across domains
(package names, package groups, environments, module streams). Every list endpoint returns the
standard ``count``/``next``/``previous``/``results`` envelope.
"""

import functools
import operator

from django.db.models import Prefetch, Q
from django.db.models.functions import Collate
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination

from pulpcore.app.contexts import with_domain
from pulpcore.app.viewsets.base import NAME_FILTER_OPTIONS
from pulpcore.app.viewsets.custom_filters import LabelFilter
from pulpcore.filters import BaseFilterSet
from pulpcore.plugin.viewsets import LabelsMixin, NamedModelViewSet, RolesMixin

from pulp_rpm.app.models import Modulemd, Package, PackageEnvironment, PackageGroup, UpdateRecord
from pulp_rpm.app.models.advisory import UpdateReference

from pulp_service.app.content_view_models import ContentView, ContentViewSearchScope
from pulp_service.app.content_view_serializers import (
    ContentViewErrataSerializer,
    ContentViewModuleStreamSerializer,
    ContentViewPackageEnvironmentSerializer,
    ContentViewPackageGroupSerializer,
    ContentViewPackageSerializer,
    ContentViewSerializer,
)
from pulp_service.app.content_view_util import (
    SortField,
    attach_domain,
    content_for_versions,
    distinct_keys,
    group_versions_by_domain,
    resolve_content_view_distributions,
    scatter_gather,
    sort_key,
)

# Errata type/severity filters follow Tang's semantics: requested values match exactly
# (case-sensitive), and the special value "other"/"Unknown" additionally matches every value
# outside the known list.
ERRATA_KNOWN_TYPES = ("security", "bugfix", "enhancement")
ERRATA_OTHER_TYPE = "other"
ERRATA_KNOWN_SEVERITIES = ("Critical", "Important", "Moderate", "Low")
ERRATA_UNKNOWN_SEVERITY = "Unknown"
ERRATA_SORT_FIELDS = ("id", "issued_date", "severity", "title", "type", "updated_date")
ERRATA_DEFAULT_SORT = "-issued_date"
MODULE_STREAM_SORT_FIELDS = ("arch", "context", "name", "stream", "version")
MODULE_STREAM_DEFAULT_SORT = "name"

LIST_DEFAULT_LIMIT = 100
LIST_MAX_LIMIT = 1000
TYPEAHEAD_DEFAULT_LIMIT = 100
TYPEAHEAD_MAX_LIMIT = 1000
MODULE_STREAMS_MAX_LIMIT = 5000

PACKAGE_ORDER = (
    SortField("name"),
    SortField("version"),
    SortField("release"),
    SortField("arch"),
    SortField("pk", text=False),
)
# Only the columns the result serializers read, so large Package/Modulemd columns (file lists,
# changelogs, dependency lists, module YAML) are never loaded. The base Content columns are always
# included: pulpcore reads pulp_type on every instance, and a deferred column costs one query per row.
CONTENT_BASE_FIELDS = ("pulp_type", "pulp_domain")
PACKAGE_RESULT_FIELDS = (
    *CONTENT_BASE_FIELDS,
    "name",
    "epoch",
    "version",
    "release",
    "arch",
    "summary",
    "description",
    "checksum_type",
    "pkgId",
    "url",
    "location_href",
    "is_modular",
)
PACKAGE_GROUP_RESULT_FIELDS = (*CONTENT_BASE_FIELDS, "id", "name", "description", "packages")
PACKAGE_ENVIRONMENT_RESULT_FIELDS = (*CONTENT_BASE_FIELDS, "id", "name", "description", "group_ids")
MODULE_STREAM_RESULT_FIELDS = (
    *CONTENT_BASE_FIELDS,
    "name",
    "stream",
    "version",
    "context",
    "arch",
    "description",
    "profiles",
)
COMPS_KEY = (SortField("name"), SortField("id"))


def _limit_parameter(default, maximum):
    return OpenApiParameter(
        name="limit",
        description=f"Maximum number of results to return (default {default}, maximum {maximum}).",
        required=False,
        type=int,
    )


def _sort_by_parameter(fields, default):
    return OpenApiParameter(
        name="sort_by",
        description=(
            f"Field to sort by; prefix with '-' for descending order (default '{default}'). Ties are "
            "broken deterministically, so paging is stable."
        ),
        required=False,
        type=str,
        enum=[*fields, *(f"-{field}" for field in fields)],
        default=default,
    )


OFFSET_PARAMETER = OpenApiParameter(
    name="offset",
    description="Number of results to skip.",
    required=False,
    type=int,
)
PACKAGE_NAME_PARAMETER = OpenApiParameter(
    name="name",
    description="Exact package name to filter by.",
    required=False,
    type=str,
)
PACKAGE_PREFIX_SEARCH_PARAMETER = OpenApiParameter(
    name="search",
    description="Case-insensitive prefix of the package name (e.g. 'du' matches 'duck' and 'DUMMY').",
    required=False,
    type=str,
)
NAME_SEARCH_PARAMETER = OpenApiParameter(
    name="search",
    description="Case-insensitive substring of the name.",
    required=False,
    type=str,
)
ERRATA_SEARCH_PARAMETER = OpenApiParameter(
    name="search",
    description="Case-insensitive substring of the advisory id, title or summary.",
    required=False,
    type=str,
)
ERRATA_TYPE_PARAMETER = OpenApiParameter(
    name="type",
    description=(
        "Comma-separated list of errata types to include, matched exactly. The special value "
        f"'{ERRATA_OTHER_TYPE}' also matches every type other than {', '.join(ERRATA_KNOWN_TYPES)} "
        "(e.g. 'newpackage')."
    ),
    required=False,
    type=str,
)
ERRATA_SEVERITY_PARAMETER = OpenApiParameter(
    name="severity",
    description=(
        "Comma-separated list of errata severities to include, matched exactly (case-sensitive; "
        f"Pulp values are {', '.join(ERRATA_KNOWN_SEVERITIES)}). The special value "
        f"'{ERRATA_UNKNOWN_SEVERITY}' also matches every severity outside that list (including "
        "empty and 'None')."
    ),
    required=False,
    type=str,
)
RPM_NAMES_PARAMETER = OpenApiParameter(
    name="rpm_names",
    description="Comma-separated list of RPM package names; only module streams "
    "containing at least one of these packages are returned.",
    required=False,
    type=str,
)
HAS_PACKAGES_PARAMETER = OpenApiParameter(
    name="has_packages",
    description=(
        "If true, only module streams that contain packages are returned; if false, only module "
        "streams without packages. If omitted, all module streams are returned."
    ),
    required=False,
    type=bool,
)

PACKAGE_LIST_PARAMETERS = [
    PACKAGE_NAME_PARAMETER,
    PACKAGE_PREFIX_SEARCH_PARAMETER,
    _limit_parameter(LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT),
    OFFSET_PARAMETER,
]
PACKAGE_NAME_SEARCH_PARAMETERS = [
    PACKAGE_PREFIX_SEARCH_PARAMETER,
    _limit_parameter(TYPEAHEAD_DEFAULT_LIMIT, TYPEAHEAD_MAX_LIMIT),
    OFFSET_PARAMETER,
]
COMPS_SEARCH_PARAMETERS = [
    NAME_SEARCH_PARAMETER,
    _limit_parameter(TYPEAHEAD_DEFAULT_LIMIT, TYPEAHEAD_MAX_LIMIT),
    OFFSET_PARAMETER,
]
ERRATA_SEARCH_PARAMETERS = [
    ERRATA_SEARCH_PARAMETER,
    ERRATA_TYPE_PARAMETER,
    ERRATA_SEVERITY_PARAMETER,
    _sort_by_parameter(ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT),
    _limit_parameter(LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT),
    OFFSET_PARAMETER,
]
MODULE_STREAM_SEARCH_PARAMETERS = [
    NAME_SEARCH_PARAMETER,
    RPM_NAMES_PARAMETER,
    HAS_PACKAGES_PARAMETER,
    _sort_by_parameter(MODULE_STREAM_SORT_FIELDS, MODULE_STREAM_DEFAULT_SORT),
    _limit_parameter(MODULE_STREAMS_MAX_LIMIT, MODULE_STREAMS_MAX_LIMIT),
    OFFSET_PARAMETER,
]


class ContentViewFilter(BaseFilterSet):
    """FilterSet for ContentView."""

    pulp_label_select = LabelFilter()

    class Meta:
        model = ContentView
        fields = {"name": NAME_FILTER_OPTIONS}


class ContentViewViewSet(
    NamedModelViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    RolesMixin,
    LabelsMixin,
):
    """
    ViewSet for ContentView.

    A ContentView is a named, persistable scope composed of Distributions -- potentially
    spanning multiple domains -- that can be searched across (see the nested
    ``content-views/{content_view_pk}/search/rpm/...`` endpoints below for the actual search
    operations; this viewset only provides the standard CRUD lifecycle for the resource itself).
    """

    queryset = ContentView.objects.all()
    endpoint_name = "content-views"
    router_lookup = "content_view"
    serializer_class = ContentViewSerializer
    filterset_class = ContentViewFilter
    ordering = "-pulp_created"
    queryset_filtering_required_permission = "service.view_contentview"

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_perms:service.add_contentview",
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:service.view_contentview",
            },
            {
                "action": ["update", "partial_update", "set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:service.change_contentview",
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:service.delete_contentview",
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:service.manage_roles_contentview",
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "service.contentview_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

    LOCKED_ROLES = {
        "service.contentview_creator": ["service.add_contentview"],
        "service.contentview_owner": [
            "service.view_contentview",
            "service.change_contentview",
            "service.delete_contentview",
            "service.manage_roles_contentview",
        ],
        "service.contentview_viewer": ["service.view_contentview"],
    }


def _int_param(request, name, default, minimum=0, maximum=None):
    try:
        value = int(request.query_params[name])
    except (KeyError, ValueError, TypeError):
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _limit_param(request, default, maximum):
    return _int_param(request, "limit", default=default, minimum=1, maximum=maximum)


def _offset_param(request):
    return _int_param(request, "offset", default=0, minimum=0)


def _list_param(request, name):
    value = request.query_params.get(name)
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _bool_param(request, name):
    value = request.query_params.get(name)
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ValidationError({name: [f"Must be 'true' or 'false', got {value!r}."]})


def parse_sort_by(value, allowed, default):
    """
    Parse a ``sort_by`` value (``field`` or ``-field``) into ``(field, descending)``.

    Raises:
        rest_framework.exceptions.ValidationError: for a field outside ``allowed``.
    """
    value = value or default
    descending = value.startswith("-")
    field = value[1:] if descending else value
    if field not in allowed:
        raise ValidationError(
            {
                "sort_by": [
                    f"Unsupported sort field {field!r}. Allowed: {', '.join(allowed)} "
                    "(prefix with '-' for descending order)."
                ]
            }
        )
    return field, descending


def paginated_response(request, results, count, limit, offset):
    paginator = LimitOffsetPagination()
    paginator.request = request
    paginator.limit = limit
    paginator.offset = offset
    paginator.count = count
    return paginator.get_paginated_response(results)


def _union_package_lists(existing, new):
    seen = {pkg.get("name") for pkg in existing}
    merged = list(existing)
    for pkg in new:
        if pkg.get("name") not in seen:
            seen.add(pkg.get("name"))
            merged.append(pkg)
    return merged


def _keys_by_domain_for_page(page_keys, keys_by_domain):
    """Domain -> the keys of ``page_keys`` present in that domain (domain order preserved)."""
    wanted = set(page_keys)
    result = {}
    for domain, keys in keys_by_domain.items():
        present = keys & wanted
        if present:
            result[domain] = present
    return result


def _key_filter(names, keys):
    return functools.reduce(operator.or_, (Q(**dict(zip(names, key, strict=True))) for key in keys))


def bucket_filter(field, requested, known, catch_all):
    """
    ``Q`` matching ``field`` exactly against ``requested``; if ``catch_all`` is requested, also
    matching every value outside ``known`` (Tang's "other"/"Unknown" bucket).
    """
    condition = Q(**{f"{field}__in": requested})
    if catch_all in requested:
        condition |= ~Q(**{f"{field}__in": known})
    return condition


def package_filter(name="", search=""):
    condition = Q()
    if name:
        condition &= Q(name=name)
    if search:
        condition &= Q(name__istartswith=search)
    return condition


def errata_filter(search="", types=(), severities=()):
    condition = Q()
    if search:
        condition &= Q(id__icontains=search) | Q(title__icontains=search) | Q(summary__icontains=search)
    if types:
        condition &= bucket_filter("type", types, ERRATA_KNOWN_TYPES, ERRATA_OTHER_TYPE)
    if severities:
        condition &= bucket_filter("severity", severities, ERRATA_KNOWN_SEVERITIES, ERRATA_UNKNOWN_SEVERITY)
    return condition


def module_stream_filter(search="", rpm_names=(), has_packages=None):
    condition = Q()
    if search:
        condition &= Q(name__icontains=search)
    if rpm_names:
        condition &= Q(packages__name__in=rpm_names)
    if has_packages is not None:
        condition &= Q(packages__isnull=not has_packages)
    return condition


def errata_order(sort_field):
    """The errata sort order: ``sort_field``, then advisory id and pk as tie-breakers."""
    tie_breakers = [] if sort_field == "id" else [SortField("id")]
    return (SortField(sort_field), *tie_breakers, SortField("pk", text=False))


def module_stream_order(sort_field):
    """The module stream sort order: ``sort_field``, then name and stream as tie-breakers."""
    names = [sort_field, *(name for name in ("name", "stream") if name != sort_field)]
    return tuple(SortField(name) for name in names)


def paginated_package_search(request, versions_by_domain):
    """
    NEVRA-sorted, paginated Package search across ``versions_by_domain``, with an exact ``name``
    filter and a case-insensitive name-prefix ``search``.

    Shared by the ContentView-backed ``search/rpm/packages/list`` endpoint and the ad-hoc
    ``rpm/content-search/packages`` endpoint (content_search_viewsets.py).

    Returns:
        tuple: ``(page, total, limit, offset)``, ready for ``paginated_response``.
    """
    condition = package_filter(request.query_params.get("name", ""), request.query_params.get("search", ""))
    limit = _limit_param(request, LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT)
    offset = _offset_param(request)

    def build_queryset(versions):
        return content_for_versions(Package, versions).filter(condition).only(*PACKAGE_RESULT_FIELDS)

    page, total = scatter_gather(versions_by_domain, build_queryset, order_by=PACKAGE_ORDER, limit=limit, offset=offset)
    return page, total, limit, offset


def package_name_search(request, versions_by_domain):
    """
    Package name typeahead across ``versions_by_domain``: case-insensitive name prefix, one result
    per distinct name (represented by one of its packages), sorted by name.

    Shared by the ad-hoc ``rpm/content-search/package-names`` endpoint and the ContentView-backed
    ``search/rpm/packages`` endpoint.

    Returns:
        tuple: ``(page, total, limit, offset)`` -- ``page`` is a list of Package instances, one
            per name; ``total`` is the number of distinct matching names.
    """
    search = request.query_params.get("search", "")
    limit = _limit_param(request, TYPEAHEAD_DEFAULT_LIMIT, TYPEAHEAD_MAX_LIMIT)
    offset = _offset_param(request)

    def build_queryset(versions):
        return content_for_versions(Package, versions).filter(package_filter(search=search))

    ordered, keys_by_domain = distinct_keys(versions_by_domain, build_queryset, (SortField("name"),))
    page_keys = ordered[offset : offset + limit]
    found = {}
    for domain, keys in _keys_by_domain_for_page(page_keys, keys_by_domain).items():
        names = [name for (name,) in keys if name not in found]
        if not names:
            continue
        with with_domain(domain):
            representatives = (
                build_queryset(versions_by_domain[domain])
                .filter(name__in=names)
                .only(*PACKAGE_RESULT_FIELDS)
                .order_by("name", "pk")
                .distinct("name")
            )
            for package in representatives:
                found[package.name] = attach_domain([package], domain)[0]
    page = [found[name] for (name,) in page_keys if name in found]
    return page, len(ordered), limit, offset


def _comps_search(request, versions_by_domain, model, result_fields, merge=None):
    """
    Shared engine for the package-group and environment searches: case-insensitive name
    substring, deduplicated by ``(name, id)`` across domains/repository versions, sorted by name.

    ``merge(existing, duplicate)``, if given, folds a duplicate-keyed row into the one kept.

    Returns:
        tuple: ``(page, total, limit, offset)``.
    """
    search = request.query_params.get("search", "")
    limit = _limit_param(request, TYPEAHEAD_DEFAULT_LIMIT, TYPEAHEAD_MAX_LIMIT)
    offset = _offset_param(request)

    def build_queryset(versions):
        qs = content_for_versions(model, versions).only(*result_fields)
        return qs.filter(name__icontains=search) if search else qs

    ordered, keys_by_domain = distinct_keys(versions_by_domain, build_queryset, COMPS_KEY)
    page_keys = ordered[offset : offset + limit]
    names = [field.name for field in COMPS_KEY]
    merged = {}
    for domain, keys in _keys_by_domain_for_page(page_keys, keys_by_domain).items():
        with with_domain(domain):
            rows = build_queryset(versions_by_domain[domain]).filter(_key_filter(names, keys)).order_by("pk")
            for row in rows:
                key = (row.name, row.id)
                if key not in merged:
                    merged[key] = attach_domain([row], domain)[0]
                elif merge is not None:
                    merge(merged[key], row)
    page = [merged[key] for key in page_keys if key in merged]
    return page, len(ordered), limit, offset


def _merge_package_groups(existing, duplicate):
    existing.packages = _union_package_lists(existing.packages, duplicate.packages)


def package_group_search(request, versions_by_domain):
    """
    Package-group search across ``versions_by_domain``: case-insensitive name substring, with the
    ``packages`` lists of same-keyed ``(name, id)`` groups unioned together.

    Shared by the ContentView-backed ``search/rpm/package-groups`` endpoint and the ad-hoc
    ``rpm/content-search/package-groups`` endpoint (content_search_viewsets.py).

    Returns:
        tuple: ``(page, total, limit, offset)``.
    """
    return _comps_search(
        request, versions_by_domain, PackageGroup, PACKAGE_GROUP_RESULT_FIELDS, merge=_merge_package_groups
    )


def environment_search(request, versions_by_domain):
    """
    Package-environment search across ``versions_by_domain``: case-insensitive name substring,
    deduplicated by ``(name, id)``.

    Shared by the ContentView-backed ``search/rpm/environments`` endpoint and the ad-hoc
    ``rpm/content-search/environments`` endpoint (content_search_viewsets.py).

    Returns:
        tuple: ``(page, total, limit, offset)``.
    """
    return _comps_search(request, versions_by_domain, PackageEnvironment, PACKAGE_ENVIRONMENT_RESULT_FIELDS)


def errata_search(request, versions_by_domain):
    """
    Errata (advisory) search across ``versions_by_domain``: filter/sort/paginate, with CVE
    references included.

    Shared by the ContentView-backed ``search/rpm/errata`` endpoint and the ad-hoc
    ``rpm/content-search/errata`` endpoint (content_search_viewsets.py).

    Returns:
        tuple: ``(page, total, limit, offset)``, ready for ``paginated_response``.
    """
    condition = errata_filter(
        request.query_params.get("search", ""), _list_param(request, "type"), _list_param(request, "severity")
    )
    sort_field, descending = parse_sort_by(request.query_params.get("sort_by"), ERRATA_SORT_FIELDS, ERRATA_DEFAULT_SORT)
    limit = _limit_param(request, LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT)
    offset = _offset_param(request)

    def build_queryset(versions):
        return (
            content_for_versions(UpdateRecord, versions)
            .filter(condition)
            .prefetch_related(Prefetch("references", queryset=UpdateReference.objects.filter(ref_type="cve")))
        )

    page, total = scatter_gather(
        versions_by_domain,
        build_queryset,
        order_by=errata_order(sort_field),
        limit=limit,
        offset=offset,
        descending=descending,
    )
    return page, total, limit, offset


def _lowest_version_streams(versions_by_domain, build_queryset):
    """
    One representative per ``(name, stream)``: the row with the lowest ``version`` (compared as
    text, like Tang), across all domains. Ties keep the first domain's row.

    Returns:
        list[dict]: ``{"domain", "pk", "name", "stream", "version", "context", "arch"}`` rows.
    """
    columns = ("pk", "name", "stream", "version", "context", "arch")
    streams = {}
    for domain, versions in versions_by_domain.items():
        with with_domain(domain):
            rows = (
                build_queryset(versions)
                .order_by("name", "stream", Collate("version", "C"), "pk")
                .distinct("name", "stream")
                .values_list(*columns)
            )
            for row in rows:
                stream = dict(zip(columns, row, strict=True), domain=domain)
                key = (stream["name"], stream["stream"])
                if key not in streams or stream["version"] < streams[key]["version"]:
                    streams[key] = stream
    return list(streams.values())


def module_stream_search(request, versions_by_domain):
    """
    Module stream search across ``versions_by_domain``: one row per ``(name, stream)`` -- the
    lowest version -- with a case-insensitive module-name substring ``search``, an optional RPM
    name filter and an optional ``has_packages`` filter. ``limit``/``count`` count streams.

    Shared by the ContentView-backed ``search/rpm/module-streams`` endpoint and the ad-hoc
    ``rpm/content-search/module-streams`` endpoint (content_search_viewsets.py).

    Returns:
        tuple: ``(page, total, limit, offset)``.
    """
    condition = module_stream_filter(
        request.query_params.get("search", ""), _list_param(request, "rpm_names"), _bool_param(request, "has_packages")
    )
    sort_field, descending = parse_sort_by(
        request.query_params.get("sort_by"), MODULE_STREAM_SORT_FIELDS, MODULE_STREAM_DEFAULT_SORT
    )
    limit = _limit_param(request, MODULE_STREAMS_MAX_LIMIT, MODULE_STREAMS_MAX_LIMIT)
    offset = _offset_param(request)

    def build_queryset(versions):
        return content_for_versions(Modulemd, versions).filter(condition)

    streams = _lowest_version_streams(versions_by_domain, build_queryset)
    order = module_stream_order(sort_field)
    key = sort_key(order)
    streams.sort(key=lambda stream: key([stream[field.name] for field in order]), reverse=descending)
    page_streams = streams[offset : offset + limit]

    pks_by_domain = {}
    for stream in page_streams:
        pks_by_domain.setdefault(stream["domain"], []).append(stream["pk"])
    found = {}
    for domain, pks in pks_by_domain.items():
        with with_domain(domain):
            modules = (
                Modulemd.objects.filter(pk__in=pks)
                .only(*MODULE_STREAM_RESULT_FIELDS)
                .prefetch_related(Prefetch("packages", queryset=Package.objects.only(*CONTENT_BASE_FIELDS, "name")))
            )
            for module in modules:
                found[module.pk] = attach_domain([module], domain)[0]
    page = [found[stream["pk"]] for stream in page_streams if stream["pk"] in found]
    return page, len(streams), limit, offset


class ContentViewSearchViewSet(NamedModelViewSet):
    """Shared plumbing for all ``content-views/{content_view_pk}/search/rpm/...`` viewsets."""

    # A dedicated, unmanaged placeholder -- deliberately NOT a real RPM content model. See
    # ContentViewSearchScope's docstring for why sharing e.g. Package/UpdateRecord's own
    # queryset here would be unsafe.
    queryset = ContentViewSearchScope.objects.none()
    parent_viewset = ContentViewViewSet
    parent_lookup_kwargs = {"content_view_pk": "content_view__pk"}

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {"action": ["list"], "principal": "authenticated", "effect": "allow"},
        ],
    }

    def _get_content_view(self):
        """
        Re-resolve the parent ContentView through ContentViewViewSet's own RBAC-scoped queryset.

        The base ``initial()``/``get_parent_field_and_object()`` machinery only checks that a
        ContentView with this pk exists at all (unfiltered), so we deliberately re-check here
        against the permission-scoped queryset instead of trusting that ambient check.
        """
        parent_viewset = ContentViewViewSet()
        parent_viewset.request = self.request
        parent_viewset.kwargs = {}
        parent_viewset.format_kwarg = None
        scoped_queryset = parent_viewset.get_queryset()
        return get_object_or_404(scoped_queryset, pk=self.kwargs["content_view_pk"])

    def _versions_by_domain(self):
        content_view = self._get_content_view()
        resolutions = resolve_content_view_distributions(content_view, self.request.user)
        return group_versions_by_domain(resolutions)


@extend_schema_view(list=extend_schema(parameters=PACKAGE_NAME_SEARCH_PARAMETERS))
class RpmContentViewPackageSearchViewSet(ContentViewSearchViewSet):
    """Typeahead package search: case-insensitive name prefix, one result per package name."""

    endpoint_name = "search/rpm/packages"
    serializer_class = ContentViewPackageSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = package_name_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(list=extend_schema(parameters=COMPS_SEARCH_PARAMETERS))
class RpmContentViewPackageGroupSearchViewSet(ContentViewSearchViewSet):
    """
    Package-group search: case-insensitive name substring, with the ``packages`` list of
    same-keyed groups found in multiple domains/repository versions unioned together.
    """

    endpoint_name = "search/rpm/package-groups"
    serializer_class = ContentViewPackageGroupSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = package_group_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(list=extend_schema(parameters=COMPS_SEARCH_PARAMETERS))
class RpmContentViewEnvironmentSearchViewSet(ContentViewSearchViewSet):
    """Package-environment search: case-insensitive name substring, deduplicated by (name, id)."""

    endpoint_name = "search/rpm/environments"
    serializer_class = ContentViewPackageEnvironmentSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = environment_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(list=extend_schema(parameters=ERRATA_SEARCH_PARAMETERS))
class RpmContentViewErrataViewSet(ContentViewSearchViewSet):
    """Errata (advisory) search: filter/sort/paginate, with CVE references included."""

    endpoint_name = "search/rpm/errata"
    serializer_class = ContentViewErrataSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = errata_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(list=extend_schema(parameters=MODULE_STREAM_SEARCH_PARAMETERS))
class RpmContentViewModuleStreamsViewSet(ContentViewSearchViewSet):
    """Module stream search: one row per (name, stream), optional RPM-name/has-packages filters."""

    endpoint_name = "search/rpm/module-streams"
    serializer_class = ContentViewModuleStreamSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = module_stream_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(list=extend_schema(parameters=PACKAGE_LIST_PARAMETERS))
class RpmContentViewPackageListViewSet(ContentViewSearchViewSet):
    """Full package listing: exact name and name-prefix filters, fixed NEVRA sort, paginated."""

    endpoint_name = "search/rpm/packages/list"
    serializer_class = ContentViewPackageSerializer

    def list(self, request, *args, **kwargs):
        page, total, limit, offset = paginated_package_search(request, self._versions_by_domain())
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)
