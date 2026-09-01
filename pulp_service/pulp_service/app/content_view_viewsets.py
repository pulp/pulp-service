"""
The ContentView resource (``content-views/``) and its nested, read-only RPM search endpoints
(``content-views/{content_view_pk}/search/rpm/...``).

Each search viewset resolves the parent ContentView's Distributions (across whatever domains
they live in) to their current RepositoryVersions via ``content_view_util.py``, then queries
RPM content across those versions -- either with the generic ``scatter_gather`` helper (for the
two paginated, offset/limit endpoints) or a lighter Python-side merge (for the three typeahead-
style endpoints, which never compute a total count, since they're meant for autocomplete-style
usage rather than exhaustive pagination).
"""

import functools
import operator

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from pulpcore.app.contexts import with_domain
from pulpcore.app.viewsets.base import NAME_FILTER_OPTIONS
from pulpcore.app.viewsets.custom_filters import LabelFilter
from pulpcore.filters import BaseFilterSet
from pulpcore.plugin.viewsets import LabelsMixin, NamedModelViewSet, RolesMixin

from pulp_rpm.app.models import Modulemd, Package, PackageEnvironment, PackageGroup, UpdateRecord
from pulp_rpm.app.models.advisory import UpdateReference

from pulp_service.app.authorization import DomainBasedPermission
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
    group_versions_by_domain,
    resolve_content_view_distributions,
    scatter_gather,
)

KNOWN_ERRATA_TYPES = {"security", "bugfix", "enhancement", "newpackage"}
KNOWN_ERRATA_SEVERITIES = {"critical", "important", "moderate", "low", "none"}
ERRATA_SORT_FIELDS = {"id", "updated_date", "issued_date", "severity", "type", "title"}
MODULE_STREAM_SORT_FIELDS = {"name", "stream", "version", "context", "arch"}
MODULE_STREAMS_HARD_CAP = 5000

SEARCH_PARAMETER = OpenApiParameter(
    name="search",
    description="Case-insensitive substring/prefix search term.",
    required=False,
    type=str,
)
LIMIT_PARAMETER = OpenApiParameter(
    name="limit",
    description="Maximum number of results to return.",
    required=False,
    type=int,
)
OFFSET_PARAMETER = OpenApiParameter(
    name="offset",
    description="Number of results to skip.",
    required=False,
    type=int,
)
SORT_BY_PARAMETER = OpenApiParameter(
    name="sort_by",
    description="Field to sort by; prefix with '-' for descending order.",
    required=False,
    type=str,
)


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
    permission_classes = [DomainBasedPermission]
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


def content_for_versions(model, versions):
    """Combine the content of one or more RepositoryVersions (all in the same domain)."""
    if not versions:
        return model.objects.none()
    if len(versions) == 1:
        return versions[0].get_content(model.objects)
    pks = set()
    for version in versions:
        pks.update(version.content_ids)
    return model.objects.filter(pk__in=pks)


def _int_param(request, name, default, minimum=0, maximum=None):
    try:
        value = int(request.query_params[name])
    except (KeyError, ValueError, TypeError):
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _list_param(request, name):
    value = request.query_params.get(name)
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _catch_all_filter(field, requested, known):
    """Build a ``Q`` for a type/severity-style filter with an "other" catch-all bucket."""
    known_requested = [v for v in requested if v in known]
    other_requested = any(v not in known for v in requested)
    condition = Q(**{f"{field}__in": known_requested}) if known_requested else Q(pk__in=[])
    if other_requested:
        condition = condition | ~Q(**{f"{field}__in": known})
    return condition


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


class ContentViewSearchViewSet(NamedModelViewSet):
    """Shared plumbing for all ``content-views/{content_view_pk}/search/rpm/...`` viewsets."""

    # A dedicated, unmanaged placeholder -- deliberately NOT a real RPM content model. See
    # ContentViewSearchScope's docstring for why sharing e.g. Package/UpdateRecord's own
    # queryset here would be unsafe.
    queryset = ContentViewSearchScope.objects.none()
    parent_viewset = ContentViewViewSet
    parent_lookup_kwargs = {"content_view_pk": "content_view__pk"}
    permission_classes = [DomainBasedPermission]

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


@extend_schema_view(
    list=extend_schema(
        parameters=[
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
        ],
    )
)
class RpmContentViewPackageSearchViewSet(ContentViewSearchViewSet):
    """Typeahead package search: prefix match, deduplicated by name."""

    endpoint_name = "search/rpm/packages"
    serializer_class = ContentViewPackageSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        search = request.query_params.get("search", "")
        limit = _int_param(request, "limit", default=25, minimum=1, maximum=100)

        def build_queryset(versions):
            qs = content_for_versions(Package, versions)
            if search:
                qs = qs.filter(name__istartswith=search)
            return qs.order_by("name").distinct("name")

        rows = []
        seen_names = set()
        for domain, versions in versions_by_domain.items():
            with with_domain(domain):
                # Fetching each domain's own top `limit` (already name-deduplicated, ascending)
                # rows is provably sufficient, not just a heuristic: in a k-way merge of sorted
                # lists, any element ranked <= `limit` globally must also be ranked <= `limit`
                # within every per-domain list it appears in (otherwise that one domain alone
                # would already contribute more than `limit` distinct names smaller than it).
                for package in build_queryset(versions)[:limit]:
                    if package.name in seen_names:
                        continue
                    seen_names.add(package.name)
                    rows.append(package)
        rows.sort(key=lambda p: p.name)
        page = rows[:limit]
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})


@extend_schema_view(
    list=extend_schema(
        parameters=[
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
        ],
    )
)
class RpmContentViewPackageGroupSearchViewSet(ContentViewSearchViewSet):
    """
    Typeahead package-group search: substring match, with the ``packages`` list of same-keyed
    groups found in multiple domains/repository versions unioned together.
    """

    endpoint_name = "search/rpm/package-groups"
    serializer_class = ContentViewPackageGroupSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        search = request.query_params.get("search", "")
        limit = _int_param(request, "limit", default=25, minimum=1, maximum=100)

        def build_queryset(versions):
            qs = content_for_versions(PackageGroup, versions)
            if search:
                qs = qs.filter(name__icontains=search)
            return qs.order_by("name", "id")

        merged = {}
        order = []
        for domain, versions in versions_by_domain.items():
            with with_domain(domain):
                base_qs = build_queryset(versions)
                # This queryset intentionally isn't DB-deduplicated (unlike the packages/
                # environments typeahead endpoints above) because duplicate (name, id) rows --
                # from multiple repository versions linked in this domain -- need their
                # `packages` lists unioned below, not discarded. So identify the true top
                # `limit` *distinct* keys cheaply via DISTINCT ON first (same exact k-way-merge
                # bound as above: any key ranked <= `limit` globally is ranked <= `limit` within
                # every domain's own distinct, sorted key list), then re-fetch every row -- not
                # just the first -- matching those specific keys to compute a correct union.
                top_keys = list(base_qs.distinct("name", "id").values_list("name", "id")[:limit])
                if not top_keys:
                    continue
                key_filter = functools.reduce(operator.or_, (Q(name=name, id=id_) for name, id_ in top_keys))
                for group in base_qs.filter(key_filter):
                    key = (group.name, group.id)
                    if key not in merged:
                        merged[key] = group
                        order.append(key)
                    else:
                        merged[key].packages = _union_package_lists(merged[key].packages, group.packages)
        order.sort(key=lambda key: key[0])
        page = [merged[key] for key in order[:limit]]
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})


@extend_schema_view(
    list=extend_schema(
        parameters=[
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
        ],
    )
)
class RpmContentViewEnvironmentSearchViewSet(ContentViewSearchViewSet):
    """Typeahead package-environment search: substring match, deduplicated by (name, id)."""

    endpoint_name = "search/rpm/environments"
    serializer_class = ContentViewPackageEnvironmentSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        search = request.query_params.get("search", "")
        limit = _int_param(request, "limit", default=25, minimum=1, maximum=100)

        def build_queryset(versions):
            qs = content_for_versions(PackageEnvironment, versions)
            if search:
                qs = qs.filter(name__icontains=search)
            return qs.order_by("name", "id").distinct("name", "id")

        merged = {}
        order = []
        for domain, versions in versions_by_domain.items():
            with with_domain(domain):
                # See RpmContentViewPackageSearchViewSet.list for why `limit` (not a heuristic
                # multiplier) is the exact bound needed here: this queryset is already
                # deduplicated by (name, id) and sorted per domain.
                for environment in build_queryset(versions)[:limit]:
                    key = (environment.name, environment.id)
                    if key not in merged:
                        merged[key] = environment
                        order.append(key)
        order.sort(key=lambda key: key[0])
        page = [merged[key] for key in order[:limit]]
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})


@extend_schema_view(
    list=extend_schema(
        parameters=[
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
            OFFSET_PARAMETER,
            SORT_BY_PARAMETER,
            OpenApiParameter(
                name="type",
                description=(
                    "Comma-separated list of errata types to include "
                    f"(known values: {sorted(KNOWN_ERRATA_TYPES)}; anything else matches "
                    "as an 'other' catch-all bucket)."
                ),
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="severity",
                description=(
                    "Comma-separated list of errata severities to include "
                    f"(known values: {sorted(KNOWN_ERRATA_SEVERITIES)}; anything else matches "
                    "as an 'other' catch-all bucket)."
                ),
                required=False,
                type=str,
            ),
        ],
    )
)
class RpmContentViewErrataViewSet(ContentViewSearchViewSet):
    """Full errata (advisory) search: filter/sort/paginate, with CVE references included."""

    endpoint_name = "search/rpm/errata"
    serializer_class = ContentViewErrataSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        search = request.query_params.get("search", "")
        types = _list_param(request, "type")
        severities = _list_param(request, "severity")
        limit = _int_param(request, "limit", default=100, minimum=1, maximum=1000)
        offset = _int_param(request, "offset", default=0, minimum=0)

        sort_by = request.query_params.get("sort_by", "id")
        descending = sort_by.startswith("-")
        sort_field = sort_by[1:] if descending else sort_by
        if sort_field not in ERRATA_SORT_FIELDS:
            sort_field = "id"

        def build_queryset(versions):
            qs = content_for_versions(UpdateRecord, versions).prefetch_related(
                Prefetch("references", queryset=UpdateReference.objects.filter(ref_type="cve"))
            )
            if search:
                qs = qs.filter(Q(id__icontains=search) | Q(title__icontains=search))
            if types:
                qs = qs.filter(_catch_all_filter("type", types, KNOWN_ERRATA_TYPES))
            if severities:
                qs = qs.filter(_catch_all_filter("severity", severities, KNOWN_ERRATA_SEVERITIES))
            return qs.order_by(sort_field)

        page, total = scatter_gather(
            versions_by_domain,
            build_queryset,
            order_by=(sort_field,),
            limit=limit,
            offset=offset,
            descending=descending,
        )
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
            SORT_BY_PARAMETER,
            OpenApiParameter(
                name="rpm_names",
                description="Comma-separated list of RPM package names; only module streams "
                "containing at least one of these packages are returned.",
                required=False,
                type=str,
            ),
        ],
    )
)
class RpmContentViewModuleStreamsViewSet(ContentViewSearchViewSet):
    """Module stream search: name/stream substring match, optional RPM name filter, capped."""

    endpoint_name = "search/rpm/module-streams"
    serializer_class = ContentViewModuleStreamSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        search = request.query_params.get("search", "")
        rpm_names = _list_param(request, "rpm_names")
        limit = _int_param(request, "limit", default=100, minimum=1, maximum=MODULE_STREAMS_HARD_CAP)

        sort_by = request.query_params.get("sort_by", "name")
        descending = sort_by.startswith("-")
        sort_field = sort_by[1:] if descending else sort_by
        if sort_field not in MODULE_STREAM_SORT_FIELDS:
            sort_field = "name"

        def build_queryset(versions):
            qs = content_for_versions(Modulemd, versions)
            if search:
                qs = qs.filter(Q(name__icontains=search) | Q(stream__icontains=search))
            if rpm_names:
                qs = qs.filter(packages__name__in=rpm_names).distinct()
            return qs.order_by(sort_field)

        rows = []
        for domain, versions in versions_by_domain.items():
            with with_domain(domain):
                rows.extend(build_queryset(versions)[:limit])
        rows.sort(key=lambda m: getattr(m, sort_field), reverse=descending)
        page = rows[:limit]
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})


@extend_schema_view(
    list=extend_schema(
        parameters=[
            LIMIT_PARAMETER,
            OFFSET_PARAMETER,
            OpenApiParameter(
                name="name",
                description="Exact package name to filter by.",
                required=False,
                type=str,
            ),
        ],
    )
)
class RpmContentViewPackageListViewSet(ContentViewSearchViewSet):
    """Full package listing: exact name filter, fixed NEVRA sort, paginated."""

    endpoint_name = "search/rpm/packages/list"
    serializer_class = ContentViewPackageSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain()
        name = request.query_params.get("name", "")
        limit = _int_param(request, "limit", default=100, minimum=1, maximum=1000)
        offset = _int_param(request, "offset", default=0, minimum=0)

        def build_queryset(versions):
            qs = content_for_versions(Package, versions)
            if name:
                qs = qs.filter(name=name)
            return qs.order_by("name", "version", "release", "arch")

        page, total = scatter_gather(
            versions_by_domain,
            build_queryset,
            order_by=("name", "version", "release", "arch"),
            limit=limit,
            offset=offset,
        )
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)
