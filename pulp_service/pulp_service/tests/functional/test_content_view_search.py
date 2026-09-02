"""
Functional tests for the ContentView resource: CRUD, cross-domain RPM search across all 6
nested endpoints, RBAC-based domain exclusion, and errata filter/sort/pagination.

These exercise the six ``content-views/{pk}/search/rpm/...`` endpoints against two separate
domains, verifying results aggregate across both, pagination/filtering/sorting behave correctly,
and that a user who loses access to one domain silently has that domain's content excluded from
results rather than erroring out.
"""

import uuid

import pytest

from pulp_rpm.tests.functional.constants import RPM_ADVISORY_COUNT, RPM_PACKAGE_COUNT


@pytest.fixture
def two_domain_content_views(setup_domain, rpm_distribution_factory, service_bindings, gen_object_with_cleanup):
    """Two domains, each with a synced repo + distribution, composed into one ContentView."""
    domain_a, _remote_a, src_a, _dest_a = setup_domain()
    domain_b, _remote_b, src_b, _dest_b = setup_domain()

    dist_a = rpm_distribution_factory(repository=src_a.pulp_href, pulp_domain=domain_a.name)
    dist_b = rpm_distribution_factory(repository=src_b.pulp_href, pulp_domain=domain_b.name)

    content_view = gen_object_with_cleanup(
        service_bindings.ContentViewsApi,
        {"name": str(uuid.uuid4()), "distributions": [dist_a.pulp_href, dist_b.pulp_href]},
        pulp_domain=domain_a.name,
    )

    return domain_a, domain_b, dist_a, dist_b, content_view


@pytest.mark.parallel
class TestContentViewCRUD:
    def test_create_read_list_delete(
        self, setup_domain, rpm_distribution_factory, gen_object_with_cleanup, service_bindings
    ):
        domain, _remote, src, _dest = setup_domain()
        dist = rpm_distribution_factory(repository=src.pulp_href, pulp_domain=domain.name)

        name = str(uuid.uuid4())
        content_view = gen_object_with_cleanup(
            service_bindings.ContentViewsApi,
            {"name": name, "description": "test content view", "distributions": [dist.pulp_href]},
            pulp_domain=domain.name,
        )
        assert content_view.name == name
        assert content_view.description == "test content view"
        assert len(content_view.distributions) == 1
        assert content_view.distributions_status[0]["status"] == "ok"

        fetched = service_bindings.ContentViewsApi.read(content_view.pulp_href)
        assert fetched.pulp_href == content_view.pulp_href

        listed = service_bindings.ContentViewsApi.list(name=name, pulp_domain=domain.name)
        assert listed.count == 1
        assert listed.results[0].pulp_href == content_view.pulp_href

    def test_update_distributions(
        self, setup_domain, rpm_distribution_factory, gen_object_with_cleanup, service_bindings
    ):
        domain, _remote, src, _dest = setup_domain()
        dist = rpm_distribution_factory(repository=src.pulp_href, pulp_domain=domain.name)

        content_view = gen_object_with_cleanup(
            service_bindings.ContentViewsApi, {"name": str(uuid.uuid4())}, pulp_domain=domain.name
        )
        assert content_view.distributions == []

        updated = service_bindings.ContentViewsApi.partial_update(
            content_view.pulp_href, {"distributions": [dist.pulp_href]}
        )
        assert updated.distributions == [dist.pulp_href]


@pytest.mark.parallel
class TestContentViewCrossDomainSearch:
    """All six search endpoints should aggregate results across both domains."""

    def test_packages_list_search(self, two_domain_content_views, service_bindings):
        _domain_a, domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmPackagesListApi.list(
            service_content_view_href=content_view.pulp_href
        )
        assert result.count == 2 * RPM_PACKAGE_COUNT
        domains_seen = {href.split("/")[3] for href in (r.pulp_href for r in result.results)}
        assert domain_b.name in domains_seen

    def test_packages_typeahead_search_is_limited(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmPackagesApi.list(
            service_content_view_href=content_view.pulp_href, limit=5
        )
        assert len(result.results) <= 5

    def test_errata_search(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmErrataApi.list(service_content_view_href=content_view.pulp_href)
        assert result.count == 2 * RPM_ADVISORY_COUNT

    def test_package_groups_search(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmPackageGroupsApi.list(
            service_content_view_href=content_view.pulp_href
        )
        assert isinstance(result.results, list)

    def test_environments_search(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmEnvironmentsApi.list(
            service_content_view_href=content_view.pulp_href
        )
        assert isinstance(result.results, list)

    def test_module_streams_search(self, two_domain_content_views, service_bindings):
        # The default fixture repo used by `setup_domain` has no module streams; this just
        # verifies the endpoint responds correctly (empty results) rather than erroring.
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        result = service_bindings.ContentViewsSearchRpmModuleStreamsApi.list(
            service_content_view_href=content_view.pulp_href
        )
        assert result.results == []


@pytest.mark.parallel
class TestContentViewErrataFilters:
    def test_pagination(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        all_errata = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href
        )
        assert len(all_errata.results) == 2 * RPM_ADVISORY_COUNT

        page = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href, limit=1, offset=1
        )
        assert len(page.results) == 1
        assert page.count == 2 * RPM_ADVISORY_COUNT

    def test_sort_by_descending(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        all_errata = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href
        )
        all_ids = [erratum.id for erratum in all_errata.results]

        sorted_desc = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href, sort_by="-id"
        )
        sorted_ids = [erratum.id for erratum in sorted_desc.results]
        assert sorted_ids == sorted(all_ids, reverse=True)

    def test_type_filter(self, two_domain_content_views, service_bindings):
        _domain_a, _domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        all_errata = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href
        )
        known_type = all_errata.results[0].type

        filtered = service_bindings.ContentViewsSearchRpmErrataApi.list(
            service_content_view_href=content_view.pulp_href, type=known_type
        )
        assert filtered.count > 0
        assert all(erratum.type == known_type for erratum in filtered.results)


@pytest.mark.parallel
class TestContentViewRBACExclusion:
    def test_losing_domain_access_excludes_its_content(self, two_domain_content_views, service_bindings, gen_user):
        """A user without access to one domain should see that domain's content silently excluded."""
        domain_a, domain_b, _dist_a, _dist_b, content_view = two_domain_content_views

        # A user who can see the ContentView (it lives in domain_a) but has no visibility into
        # domain_b at all. `domain_roles` grants a role scoped to every object of that type
        # *within* the given domain; `object_roles` grants a role on one specific object
        # regardless of domain (Domain objects aren't themselves scoped to a domain the way
        # ContentViews are, so domain_b's view access must be granted object-scoped).
        limited_user = gen_user(
            domain_roles=[("service.contentview_viewer", domain_a.pulp_href)],
            object_roles=[("core.domain_viewer", domain_a.pulp_href)],
        )

        with limited_user:
            fetched = service_bindings.ContentViewsApi.read(content_view.pulp_href)
            statuses = {entry["domain"]: entry["status"] for entry in fetched.distributions_status}
            assert statuses[domain_a.name] == "ok"
            assert statuses[domain_b.name] == "no_domain_access"

            result = service_bindings.ContentViewsSearchRpmPackagesListApi.list(
                service_content_view_href=content_view.pulp_href
            )
            assert result.count == RPM_PACKAGE_COUNT
            domains_seen = {href.split("/")[3] for href in (r.pulp_href for r in result.results)}
            assert domains_seen == {domain_a.name}

            errata_result = service_bindings.ContentViewsSearchRpmErrataApi.list(
                service_content_view_href=content_view.pulp_href
            )
            assert errata_result.count == RPM_ADVISORY_COUNT
