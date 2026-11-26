"""
Utilities for resolving a ContentView's Distributions into RepositoryVersions grouped by
domain, and for executing cross-domain "scatter/gather" queries against them.

These are the pieces the nested ``content-views/{pk}/search/...`` viewsets in
``content_view_viewsets.py`` compose to implement cross-domain search:

    resolutions = resolve_content_view_distributions(content_view, request.user)
    versions_by_domain = group_versions_by_domain(resolutions)
    page, total = scatter_gather(
        versions_by_domain,
        build_queryset=lambda versions: content_for_versions(Package, versions),
        order_by=(SortField("name"), SortField("pk", text=False)),
        limit=limit,
        offset=offset,
    )

Every domain is queried separately (inside its own ``with_domain`` context) and the per-domain
results are merged in Python, so the ordering applied in SQL and the ordering applied by the
Python merge must be identical. Database collations (e.g. ``en_US.UTF-8``) can't be reproduced in
Python, so text columns are ordered explicitly: case-insensitively first, then by exact value,
both compared by code point (``COLLATE "C"``) -- exactly what ``sort_key`` does in Python.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import F, Func
from django.db.models.functions import Collate, Lower

from pulpcore.app.contexts import with_domain
from pulpcore.app.models import RepositoryVersion
from pulpcore.plugin.viewsets import NamedModelViewSet

# Distribution/repository-version resolution statuses.
STATUS_OK = "ok"
STATUS_NO_DOMAIN_ACCESS = "no_domain_access"
STATUS_NO_VERSION = "no_version"


def user_can_view_domain(user, domain):
    """
    Returns True if ``user`` has read (view) access to ``domain``.

    This mirrors the same model/domain/object permission check used by
    ``pulpcore.app.global_access_conditions.has_domain_perms``, generalized to an arbitrary
    domain instead of only the current request's domain. This is what allows a ContentView to
    reference Distributions living in other domains while still enforcing RBAC, per-domain, at
    query time -- rather than at write time only.

    Resolves via pulpcore's standard ``ObjectRolePermissionBackend`` (already registered in
    ``AUTHENTICATION_BACKENDS``), so it reflects real object-level ``core.view_domain`` role
    assignments on the ``Domain`` object -- no custom authentication backend needed.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.has_perm("core.view_domain") or user.has_perm("core.view_domain", obj=domain)


@dataclass
class DistributionResolution:
    """The result of resolving a single Distribution linked to a ContentView."""

    distribution: Any
    domain: Any
    repository_version: Any | None
    status: str


def resolve_content_view_distributions(content_view, user):
    """
    Resolve every Distribution linked to ``content_view`` to its current RepositoryVersion,
    classifying each one by accessibility/staleness. Never raises: distributions whose domain
    is no longer accessible, or that no longer resolve to a version (deleted distribution or
    repository version), are returned with a status instead of causing an error -- callers
    (search endpoints, and the ContentView detail serializer) decide how to surface that.

    Args:
        content_view (pulp_service.app.models.ContentView): The content view to resolve.
        user: The requesting user, used for the per-domain accessibility check.

    Returns:
        list[DistributionResolution]
    """
    resolutions = []
    for distribution in content_view.distributions.select_related("pulp_domain").all():
        domain = distribution.pulp_domain
        if not user_can_view_domain(user, domain):
            resolutions.append(DistributionResolution(distribution, domain, None, STATUS_NO_DOMAIN_ACCESS))
            continue

        _repository, repository_version, _publication = distribution.cast().get_repository_publication_and_version()
        if repository_version is None:
            resolutions.append(DistributionResolution(distribution, domain, None, STATUS_NO_VERSION))
        else:
            resolutions.append(DistributionResolution(distribution, domain, repository_version, STATUS_OK))
    return resolutions


@dataclass
class RepositoryVersionResolution:
    """The result of resolving a single repository-version href/PRN passed directly in a request."""

    href: str
    domain: Any
    repository_version: Any | None
    status: str


def resolve_repository_versions(hrefs, user):
    """
    Resolve repository-version hrefs/PRNs -- potentially spanning multiple domains -- supplied
    directly in a request, enforcing per-domain read access. Never raises for an access problem:
    a well-formed href pointing at a domain the user can't read resolves with
    STATUS_NO_DOMAIN_ACCESS instead, so it's silently excluded from results the same way an
    inaccessible ContentView Distribution is (see resolve_content_view_distributions).

    This is the ad-hoc counterpart to resolve_content_view_distributions: instead of starting
    from a persisted ContentView's Distributions, it starts directly from hrefs supplied in the
    request, letting callers (e.g. image-builder) search across repository versions in their own
    domain and any shared domain (redhat, community, ...) without first provisioning a
    ContentView.

    Args:
        hrefs (list[str]): Repository-version hrefs or PRNs.
        user: The requesting user, used for the per-domain accessibility check.

    Returns:
        list[RepositoryVersionResolution]

    Raises:
        rest_framework.exceptions.ValidationError: if an href is malformed or doesn't resolve to
            any repository version (via NamedModelViewSet.get_resource) -- a client error, unlike
            the silently-excluded no-domain-access case.
    """
    resolutions = []
    for href in hrefs:
        version = NamedModelViewSet.get_resource(href, RepositoryVersion)
        domain = version.repository.pulp_domain
        if not user_can_view_domain(user, domain):
            resolutions.append(RepositoryVersionResolution(href, domain, None, STATUS_NO_DOMAIN_ACCESS))
        else:
            resolutions.append(RepositoryVersionResolution(href, domain, version, STATUS_OK))
    return resolutions


def group_versions_by_domain(resolutions):
    """
    Group the RepositoryVersions of "ok" resolutions by domain.

    This is the input scatter_gather (or a lighter Python-side merge) consumes -- lost-access
    and stale/deleted distributions (any non-"ok" status) are already excluded here, satisfying
    the "search silently excludes inaccessible distributions" requirement. Works equally on
    DistributionResolution or RepositoryVersionResolution lists (or any mix), since it only reads
    the status/domain/repository_version attributes common to both.

    Args:
        resolutions (list[DistributionResolution | RepositoryVersionResolution]):

    Returns:
        dict: Domain -> list[RepositoryVersion]
    """
    by_domain = defaultdict(list)
    for resolution in resolutions:
        if resolution.status == STATUS_OK:
            by_domain[resolution.domain].append(resolution.repository_version)
    return dict(by_domain)


def content_for_versions(model, versions):
    """
    Combine the content of one or more RepositoryVersions (all in the same domain).

    The content ids stay in the database (an ``unnest`` subquery over ``content_ids``) instead of
    being loaded into Python and sent back as a huge ``IN (...)`` list of bound parameters.
    """
    if not versions:
        return model.objects.none()
    if len(versions) == 1:
        return versions[0].get_content(model.objects)
    content_ids = (
        RepositoryVersion.objects.filter(pk__in=[version.pk for version in versions])
        .annotate(cid=Func(F("content_ids"), function="unnest"))
        .values_list("cid", flat=True)
    )
    return model.objects.filter(pk__in=content_ids)


@dataclass(frozen=True)
class SortField:
    """
    One column of a cross-domain sort order.

    ``text`` columns are ordered case-insensitively first and by exact value second, both by code
    point, so that SQL and the Python merge agree. Non-text columns (UUIDs, ints) are ordered by
    their natural value, which is identical in PostgreSQL and Python.
    """

    name: str
    text: bool = True


def db_ordering(fields: Sequence[SortField], descending: bool = False):
    """The ``order_by()`` expressions matching ``sort_key(fields)``."""
    parts = []
    for field in fields:
        if field.text:
            parts += [Collate(Lower(field.name), "C"), Collate(F(field.name), "C")]
        else:
            parts.append(F(field.name))
    return [part.desc(nulls_first=True) if descending else part.asc(nulls_last=True) for part in parts]


def _text_key(value):
    if value is None:
        return (True, "", "")
    return (False, value.lower(), value)


def _raw_key(value):
    return (value is None, value)


def sort_key(fields: Sequence[SortField]):
    """
    A ``key=`` function ordering a sequence of values (one per field, in ``fields`` order)
    exactly like ``db_ordering(fields)`` orders rows in PostgreSQL. NULLs sort last ascending and
    first descending (``reverse=True``), matching PostgreSQL's defaults.
    """
    key_functions = [_text_key if field.text else _raw_key for field in fields]

    def key(values):
        return tuple(function(value) for function, value in zip(key_functions, values, strict=True))

    return key


def attach_domain(objects, domain):
    """
    Cache ``domain`` as each object's ``pulp_domain``.

    Serializing ``pulp_href`` reads ``obj.pulp_domain.name``; without this that's one extra query
    per row, executed outside the domain's ``with_domain`` context.
    """
    for obj in objects:
        obj.pulp_domain = domain
    return objects


def _fetch_by_pk(versions_by_domain, build_queryset, refs):
    """Fetch full objects for ``(domain, pk)`` refs, one query per domain, in ``refs`` order."""
    pks_by_domain = defaultdict(list)
    for domain, pk in refs:
        pks_by_domain[domain].append(pk)
    objects = {}
    for domain, pks in pks_by_domain.items():
        with with_domain(domain):
            for obj in build_queryset(versions_by_domain[domain]).filter(pk__in=pks):
                objects[(domain, obj.pk)] = attach_domain([obj], domain)[0]
    # A row deleted between the two phases is simply left out of the page.
    return [objects[ref] for ref in refs if ref in objects]


def scatter_gather(  # noqa: PLR0913
    versions_by_domain: dict,
    build_queryset: Callable[[list], Any],
    *,
    order_by: Sequence[SortField],
    limit: int,
    offset: int = 0,
    descending: bool = False,
    count: bool = True,
):
    """
    Execute ``build_queryset(versions)`` once per domain, inside that domain's routing context
    (``with_domain``), and merge the results into a single page.

    For a single domain this is one query with native ``ORDER BY``/``LIMIT``/``OFFSET`` (plus one
    ``COUNT`` if requested). For multiple domains it works in two phases: first each domain returns
    only the sort keys and pk of its top ``offset + limit`` rows (sufficient, since a single domain
    can supply at most every row up to the end of the requested page); those small tuples are
    merged in Python. Then the full rows are fetched only for the pks on the requested page. This
    keeps large offsets cheap enough that no offset cap is needed.

    Args:
        versions_by_domain (dict): Domain -> list[RepositoryVersion], as returned by
            ``group_versions_by_domain``.
        build_queryset (callable): Given a list of RepositoryVersions (all belonging to the same
            domain), returns a filtered (unordered, unsliced) QuerySet of model instances.
            Prefetches may be applied; they only run for the rows on the returned page.
        order_by (Sequence[SortField]): The sort order. Should end with a unique column (e.g.
            ``SortField("pk", text=False)``) so that paging is stable.
        limit (int): Maximum number of rows to return.
        offset (int): Number of rows to skip.
        descending (bool): Whether to sort descending. Applies uniformly to all ``order_by``
            fields.
        count (bool): If True, also compute an exact total count across all domains.

    Returns:
        tuple: ``(page, total)``. ``page`` is a list of model instances of length <= ``limit``,
            with ``pulp_domain`` already cached. ``total`` is an int, or None if ``count=False``.
    """
    fields = tuple(order_by)
    ordering = db_ordering(fields, descending)
    domains = list(versions_by_domain.items())

    if not domains:
        return [], (0 if count else None)

    if len(domains) == 1:
        domain, versions = domains[0]
        with with_domain(domain):
            qs = build_queryset(versions).order_by(*ordering)
            total = qs.count() if count else None
            page = attach_domain(list(qs[offset : offset + limit]), domain)
        return page, total

    names = [field.name for field in fields]
    value_names = ["pk", *(name for name in names if name != "pk")]
    candidates = []
    total = 0 if count else None
    for domain, versions in domains:
        with with_domain(domain):
            qs = build_queryset(versions).prefetch_related(None).order_by(*ordering)
            if count:
                total += qs.count()
            for row in qs.values_list(*value_names)[: offset + limit]:
                values = dict(zip(value_names, row, strict=True))
                candidates.append((domain, values))

    key = sort_key(fields)
    candidates.sort(key=lambda candidate: key([candidate[1][name] for name in names]), reverse=descending)
    refs = [(domain, values["pk"]) for domain, values in candidates[offset : offset + limit]]
    return _fetch_by_pk(versions_by_domain, build_queryset, refs), total


def distinct_keys(versions_by_domain, build_queryset, key_fields: Sequence[SortField]):
    """
    Collect the distinct key tuples (e.g. ``(name, id)``) of every row matching
    ``build_queryset`` in each domain.

    Used by the typeahead-style searches, which deduplicate rows by key across domains: the union
    of these sets gives an exact ``count`` and, once sorted with ``sort_key(key_fields)``, the keys
    of any requested page.

    Returns:
        tuple: ``(ordered_keys, keys_by_domain)`` -- the sorted union of all keys, and
            Domain -> set of key tuples present in that domain.
    """
    names = [field.name for field in key_fields]
    keys_by_domain = {}
    for domain, versions in versions_by_domain.items():
        with with_domain(domain):
            qs = build_queryset(versions).prefetch_related(None).order_by()
            keys_by_domain[domain] = set(qs.values_list(*names).distinct())
    all_keys = set().union(*keys_by_domain.values())
    return sorted(all_keys, key=sort_key(key_fields)), keys_by_domain
