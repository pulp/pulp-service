"""
Ad-hoc, non-persistent RPM content search across repository versions passed directly as hrefs in
the request -- potentially spanning multiple domains.

This is the lightweight counterpart to the ContentView-backed ``content-views/{pk}/search/rpm/...``
endpoints in ``content_view_viewsets.py``: it reuses the same per-domain RBAC check and
scatter_gather query engine (via ``content_view_util.py``), but skips the ContentView/
Distribution persistence layer entirely. It exists for callers that want to search a one-off set
of repository versions -- e.g. image-builder searching for packages across its own domain's
latest snapshot plus the shared ``redhat``/``community`` domain snapshots -- without first
creating and maintaining a named ContentView resource.
"""

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from pulpcore.plugin.viewsets import NamedModelViewSet

from pulp_service.app.content_view_models import ContentViewSearchScope
from pulp_service.app.content_view_serializers import (
    ContentViewErrataSerializer,
    ContentViewModuleStreamSerializer,
    ContentViewPackageGroupSerializer,
    ContentViewPackageSerializer,
)
from pulp_service.app.content_view_util import group_versions_by_domain, resolve_repository_versions
from pulp_service.app.content_view_viewsets import (
    ERRATA_SEVERITY_PARAMETER,
    ERRATA_TYPE_PARAMETER,
    LIMIT_PARAMETER,
    OFFSET_PARAMETER,
    RPM_NAMES_PARAMETER,
    SEARCH_PARAMETER,
    SORT_BY_PARAMETER,
    errata_search,
    module_stream_search,
    package_group_search,
    paginated_package_search,
    paginated_response,
)

REPOSITORY_VERSION_PARAMETER = OpenApiParameter(
    name="repository_version",
    description=(
        "Href or PRN of a repository version to search. Repeat the parameter to search multiple "
        "repository versions in a single request; they may span any domain the caller has read "
        "access to (e.g. the caller's own domain plus shared domains such as redhat or "
        "community). At least one is required. A version in a domain the caller can't read is "
        "silently excluded from results rather than causing an error."
    ),
    required=True,
    type=str,
    many=True,
)


class RpmContentSearchViewSet(NamedModelViewSet):
    """Shared plumbing for the ad-hoc ``rpm/content-search/...`` endpoints."""

    # A dedicated, unmanaged placeholder -- not a real RPM content model. See
    # ContentViewSearchScope's docstring for why sharing e.g. Package's own queryset here would
    # be unsafe (it would make pulpcore's get_viewset_for_model ambiguous for that model).
    queryset = ContentViewSearchScope.objects.none()

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {"action": ["list"], "principal": "authenticated", "effect": "allow"},
        ],
    }

    def _versions_by_domain(self, request):
        hrefs = request.query_params.getlist("repository_version")
        if not hrefs:
            raise ValidationError({"repository_version": ["At least one repository_version href is required."]})
        resolutions = resolve_repository_versions(hrefs, request.user)
        return group_versions_by_domain(resolutions)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            REPOSITORY_VERSION_PARAMETER,
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
class RpmContentSearchPackagesViewSet(RpmContentSearchViewSet):
    """
    Ad-hoc, cross-domain RPM package search: exact name filter, fixed NEVRA sort, paginated.

    Same query behavior as the ContentView-backed ``search/rpm/packages/list`` endpoint, but the
    repository versions to search come directly from ``repository_version`` query parameters
    instead of a persisted ContentView's Distributions.
    """

    endpoint_name = "rpm/content-search/packages"
    serializer_class = ContentViewPackageSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain(request)
        page, total, limit, offset = paginated_package_search(request, versions_by_domain)
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            REPOSITORY_VERSION_PARAMETER,
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
        ],
    )
)
class RpmContentSearchPackageGroupsViewSet(RpmContentSearchViewSet):
    """
    Ad-hoc, cross-domain RPM package-group search: substring match, with the ``packages`` list of
    same-keyed groups found in multiple repository versions unioned together.

    Same query behavior as the ContentView-backed ``search/rpm/package-groups`` endpoint, but the
    repository versions to search come directly from ``repository_version`` query parameters
    instead of a persisted ContentView's Distributions.
    """

    endpoint_name = "rpm/content-search/package-groups"
    serializer_class = ContentViewPackageGroupSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain(request)
        page = package_group_search(request, versions_by_domain)
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})


@extend_schema_view(
    list=extend_schema(
        parameters=[
            REPOSITORY_VERSION_PARAMETER,
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
            OFFSET_PARAMETER,
            SORT_BY_PARAMETER,
            ERRATA_TYPE_PARAMETER,
            ERRATA_SEVERITY_PARAMETER,
        ],
    )
)
class RpmContentSearchErrataViewSet(RpmContentSearchViewSet):
    """
    Ad-hoc, cross-domain errata (advisory) search: filter/sort/paginate, with CVE references
    included.

    Same query behavior as the ContentView-backed ``search/rpm/errata`` endpoint, but the
    repository versions to search come directly from ``repository_version`` query parameters
    instead of a persisted ContentView's Distributions.
    """

    endpoint_name = "rpm/content-search/errata"
    serializer_class = ContentViewErrataSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain(request)
        page, total, limit, offset = errata_search(request, versions_by_domain)
        serializer = self.get_serializer(page, many=True)
        return paginated_response(request, serializer.data, total, limit, offset)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            REPOSITORY_VERSION_PARAMETER,
            SEARCH_PARAMETER,
            LIMIT_PARAMETER,
            SORT_BY_PARAMETER,
            RPM_NAMES_PARAMETER,
        ],
    )
)
class RpmContentSearchModuleStreamsViewSet(RpmContentSearchViewSet):
    """
    Ad-hoc, cross-domain module stream search: name/stream substring match, optional RPM name
    filter, capped.

    Same query behavior as the ContentView-backed ``search/rpm/module-streams`` endpoint, but the
    repository versions to search come directly from ``repository_version`` query parameters
    instead of a persisted ContentView's Distributions.
    """

    endpoint_name = "rpm/content-search/module-streams"
    serializer_class = ContentViewModuleStreamSerializer

    def list(self, request, *args, **kwargs):
        versions_by_domain = self._versions_by_domain(request)
        page = module_stream_search(request, versions_by_domain)
        serializer = self.get_serializer(page, many=True)
        return Response({"results": serializer.data})
