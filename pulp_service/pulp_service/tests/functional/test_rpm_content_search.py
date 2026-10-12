"""
Functional tests for the ad-hoc ``rpm/content-search/packages/`` endpoint -- the
no-ContentView-required counterpart to ``content-views/{pk}/search/rpm/packages/list``.

No client bindings exist for this endpoint (it isn't tied to a generated resource), so these
hit it directly with ``requests``, mirroring the pattern used in test_rbac_endpoint_access.py.
"""

from urllib.parse import urljoin

import pytest
import requests

from pulp_rpm.tests.functional.constants import RPM_PACKAGE_COUNT


@pytest.fixture
def admin_auth(bindings_cfg):
    return (bindings_cfg.username, bindings_cfg.password)


def _search_url(bindings_cfg, domain):
    return urljoin(bindings_cfg.host, f"/api/pulp/{domain.name}/api/v3/rpm/content-search/packages/")


@pytest.mark.parallel
class TestRpmContentSearchPackages:
    def test_search_single_repository_version(self, setup_domain, bindings_cfg, admin_auth):
        domain, _remote, src, _dest = setup_domain()

        resp = requests.get(
            _search_url(bindings_cfg, domain),
            params={"repository_version": src.latest_version_href},
            auth=admin_auth,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == RPM_PACKAGE_COUNT

    def test_search_aggregates_across_domains(self, setup_domain, bindings_cfg, admin_auth):
        domain_a, _remote_a, src_a, _dest_a = setup_domain()
        domain_b, _remote_b, src_b, _dest_b = setup_domain()

        resp = requests.get(
            _search_url(bindings_cfg, domain_a),
            params={"repository_version": [src_a.latest_version_href, src_b.latest_version_href]},
            auth=admin_auth,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 2 * RPM_PACKAGE_COUNT
        domains_seen = {result["pulp_href"].split("/")[3] for result in body["results"]}
        assert domains_seen == {domain_a.name, domain_b.name}

    def test_name_filter(self, setup_domain, bindings_cfg, admin_auth, rpm_package_api):
        domain, _remote, src, _dest = setup_domain()
        a_package = rpm_package_api.list(repository_version=src.latest_version_href, limit=1).results[0]

        resp = requests.get(
            _search_url(bindings_cfg, domain),
            params={"repository_version": src.latest_version_href, "name": a_package.name},
            auth=admin_auth,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert results
        assert all(result["name"] == a_package.name for result in results)

    def test_pagination(self, setup_domain, bindings_cfg, admin_auth):
        domain, _remote, src, _dest = setup_domain()

        page = requests.get(
            _search_url(bindings_cfg, domain),
            params={"repository_version": src.latest_version_href, "limit": 1, "offset": 1},
            auth=admin_auth,
            timeout=60,
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["results"]) == 1
        assert body["count"] == RPM_PACKAGE_COUNT

    def test_missing_repository_version_param_is_400(self, setup_domain, bindings_cfg, admin_auth):
        domain, _remote, _src, _dest = setup_domain(sync=False)

        resp = requests.get(_search_url(bindings_cfg, domain), auth=admin_auth, timeout=60)
        assert resp.status_code == 400, resp.text

    def test_malformed_repository_version_href_is_400(self, setup_domain, bindings_cfg, admin_auth):
        domain, _remote, _src, _dest = setup_domain(sync=False)

        resp = requests.get(
            _search_url(bindings_cfg, domain),
            params={"repository_version": urljoin(bindings_cfg.host, "/api/pulp/does-not-exist/")},
            auth=admin_auth,
            timeout=60,
        )
        assert resp.status_code == 400, resp.text


@pytest.mark.parallel
class TestRpmContentSearchRBACExclusion:
    def test_inaccessible_domain_silently_excluded(self, setup_domain, bindings_cfg, gen_user):
        """A caller without read access to one of the referenced domains should see that
        domain's content silently excluded, not an error -- same behavior as ContentView
        search (see test_content_view_search.py::TestContentViewRBACExclusion)."""
        domain_a, _remote_a, src_a, _dest_a = setup_domain()
        _domain_b, _remote_b, src_b, _dest_b = setup_domain()

        limited_user = gen_user(object_roles=[("core.domain_viewer", domain_a.pulp_href)])

        resp = requests.get(
            _search_url(bindings_cfg, domain_a),
            params={"repository_version": [src_a.latest_version_href, src_b.latest_version_href]},
            auth=(limited_user.username, limited_user.password),
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == RPM_PACKAGE_COUNT
        domains_seen = {result["pulp_href"].split("/")[3] for result in body["results"]}
        assert domains_seen == {domain_a.name}
