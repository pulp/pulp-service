"""
Functional tests for the ad-hoc ``rpm/content-search/...`` endpoints -- the no-ContentView-
required counterparts to ``content-views/{pk}/search/rpm/...``.

The endpoints are called directly with ``requests`` and **every** 200 response is validated
against the server's own OpenAPI schema (``assert_matches_schema``), since generated clients
(e.g. Zest) fail on any disagreement between the two.

The expectations encode the behavior of Tang, which these endpoints replace for Content Sources:
see https://gist.github.com/dominikvagner/236ede2fd49bdd20aa328e1995688782.
"""

import json
import tempfile
from urllib.parse import parse_qs, urljoin, urlparse

import jsonschema
import pytest
import requests

from pulpcore.client.pulp_rpm import ContentAdvisoriesApi, ContentModulemdsApi, RpmCompsApi, RpmModulemd

from pulp_rpm.tests.functional.constants import (
    PULP_FIXTURES_BASE_URL,
    RPM_ADVISORY_COUNT,
    RPM_PACKAGE_COUNT,
    RPM_UNSIGNED_FIXTURE_URL,
)

RPM_WITH_MODULES_FIXTURE_URL = urljoin(PULP_FIXTURES_BASE_URL, "rpm-with-modules/")
SEARCH_PATH_PREFIX = "/api/pulp/{pulp_domain}/api/v3/rpm/content-search/"
SEARCH_PATH_TEMPLATE = SEARCH_PATH_PREFIX + "{endpoint}/"
SCHEMA_KEYWORDS_TO_DROP = ("nullable", "readOnly", "writeOnly", "description", "format", "example")


# OpenAPI contract helpers


def _to_json_schema(node, components):
    """Inline ``$ref``s and translate OpenAPI 3.0 ``nullable`` into JSON Schema."""
    if isinstance(node, list):
        return [_to_json_schema(item, components) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _to_json_schema(components[node["$ref"].rsplit("/", 1)[1]], components)
    converted = {}
    for key, value in node.items():
        if key == "properties":
            converted[key] = {name: _to_json_schema(schema, components) for name, schema in value.items()}
        elif key not in SCHEMA_KEYWORDS_TO_DROP:
            converted[key] = _to_json_schema(value, components)
    if node.get("nullable"):
        if "type" in converted:
            converted["type"] = [converted["type"], "null"]
        else:
            converted = {"anyOf": [converted, {"type": "null"}]}
    return converted


@pytest.fixture(scope="session")
def content_search_schemas(bindings_cfg):
    """Endpoint name -> JSON Schema of its 200 response, from the live OpenAPI document."""
    spec = requests.get(urljoin(bindings_cfg.host, "/api/pulp/api/v3/docs/api.json"), timeout=60).json()
    components = spec["components"]["schemas"]
    schemas = {}
    for path, operations in spec["paths"].items():
        if path.startswith(SEARCH_PATH_PREFIX):
            endpoint = path[len(SEARCH_PATH_PREFIX) :].rstrip("/")
            response = operations["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            schemas[endpoint] = _to_json_schema(response, components)
    return schemas


@pytest.fixture
def admin_auth(bindings_cfg):
    return (bindings_cfg.username, bindings_cfg.password)


@pytest.fixture
def search(bindings_cfg, admin_auth, content_search_schemas):
    """
    ``search(endpoint, versions, **params)`` -> response JSON.

    ``versions`` are repository objects (their latest version is searched) or version hrefs.
    A 200 response is always validated against the endpoint's OpenAPI schema; pass
    ``expected_status`` to assert an error status instead (the response is then returned as-is).
    """

    def _search(endpoint, versions, *, domain_name="default", auth=None, expected_status=200, **params):
        hrefs = [getattr(version, "latest_version_href", version) for version in versions]
        resp = requests.get(
            urljoin(bindings_cfg.host, SEARCH_PATH_TEMPLATE.format(pulp_domain=domain_name, endpoint=endpoint)),
            params={"repository_version": hrefs, **params},
            auth=auth or admin_auth,
            timeout=120,
        )
        assert resp.status_code == expected_status, resp.text
        body = resp.json()
        if expected_status == 200:
            jsonschema.validate(body, content_search_schemas[endpoint])
        return body

    return _search


def _names(body):
    return [result["name"] for result in body["results"]]


def _domains(body):
    return {result["pulp_href"].split("/")[3] for result in body["results"]}


def _walk_pages(search, endpoint, versions, limit, **params):
    """Concatenate every page of ``endpoint`` at ``limit``, asserting ``count`` never changes."""
    first = search(endpoint, versions, limit=limit, offset=0, **params)
    results = list(first["results"])
    for offset in range(limit, first["count"], limit):
        page = search(endpoint, versions, limit=limit, offset=offset, **params)
        assert page["count"] == first["count"]
        results += page["results"]
    return results


# Test data


def _comps_xml(groups, environments):
    group_xml = "".join(
        f"""
  <group>
    <id>{group_id}</id><name>{name}</name><description>{name}</description>
    <default>false</default><uservisible>true</uservisible>
    <packagelist>{"".join(f'<packagereq type="mandatory">{p}</packagereq>' for p in packages)}</packagelist>
  </group>"""
        for group_id, name, packages in groups
    )
    environment_xml = "".join(
        f"""
  <environment>
    <id>{env_id}</id><name>{name}</name><description>{name}</description>
    <grouplist><groupid>alpha</groupid></grouplist>
  </environment>"""
        for env_id, name in environments
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE comps PUBLIC "-//Red Hat, Inc.//DTD Comps info//EN" "comps.dtd">
<comps>{group_xml}{environment_xml}
</comps>"""


COMPS_A = _comps_xml(
    groups=[
        ("alpha", "Alpha Group", ["bear"]),
        ("base", "base system", ["camel"]),
        ("beta", "Beta Group", ["cat"]),
        ("devtools", "Development Tools", ["dog"]),
        ("shared", "Shared Group", ["duck"]),
    ],
    environments=[("workstation", "Workstation"), ("minimal", "minimal install"), ("shared-env", "Shared Env")],
)
COMPS_B = _comps_xml(
    groups=[("shared", "Shared Group", ["duck", "frog"]), ("zeta", "zeta group", ["zebra"])],
    environments=[("shared-env", "Shared Env"), ("virt", "Virtualization Host")],
)


def _advisory(advisory_id, *, type_, severity, issued, title=None, summary=None, cves=()):
    # The advisory upload format names the reference fields "id"/"type" (stored as ref_id/ref_type).
    references = [{"href": f"https://cve.example/{cve}", "id": cve, "title": cve, "type": "cve"} for cve in cves]
    if cves:
        references.append({"href": "https://bugzilla.example/1", "id": "1", "title": "bug", "type": "bugzilla"})
    advisory = {
        "id": advisory_id,
        "type": type_,
        "severity": severity,
        "issued": issued,
        "updated": issued,
        "title": title or f"{advisory_id} title",
        "summary": summary or f"{advisory_id} summary",
        "description": "",
        "solution": "",
        "release": "1",
        "version": "1",
        "reboot_suggested": False,
        "references": references,
        "pkglist": [
            {
                "packages": [
                    {
                        "arch": "noarch",
                        "epoch": "0",
                        "filename": "bear-4.1-1.noarch.rpm",
                        "name": "bear",
                        "release": "1",
                        "src": "http://www.fedoraproject.org",
                        "sum": "",
                        "sum_type": "",
                        "version": "4.1",
                    }
                ]
            }
        ],
    }
    return advisory


ADVISORIES_A = [
    _advisory(
        "TZ-SEC-1",
        type_="security",
        severity="Important",
        issued="2026-02-05 00:00:00",
        title="Kernel fix",
        summary="Fixes a flaw in widget",
        cves=("CVE-2026-1111", "CVE-2026-2222"),
    ),
    _advisory("TZ-SEC-2", type_="security", severity="Critical", issued="2026-02-06 00:00:00"),
    _advisory("TZ-SEC-3", type_="security", severity="Moderate", issued="2026-02-07 00:00:00"),
    _advisory("TZ-SEC-4", type_="security", severity="Low", issued="2026-02-07 00:00:00"),
    _advisory("TZ-LOW-1", type_="bugfix", severity="important", issued="2026-03-01 00:00:00"),
    _advisory("TZ-NONE-1", type_="bugfix", severity="None", issued="2026-03-02 00:00:00"),
    _advisory("TZ-EMPTY-1", type_="enhancement", severity="", issued="2026-03-03 00:00:00"),
    _advisory("TZ-NEW-1", type_="newpackage", severity="", issued="2026-04-05 00:00:00"),
    _advisory("TZ-ODD-1", type_="rhea-custom", severity="", issued="2026-05-05 00:00:00"),
]
ADVISORIES_B = [
    _advisory("TZ-B-1", type_="security", severity="Important", issued="2026-02-07 00:00:00"),
]
# Tang's default order: newest issue date first. Equal dates are broken by advisory id, in the
# same direction.
ERRATA_DEFAULT_ORDER = [
    "TZ-ODD-1",
    "TZ-NEW-1",
    "TZ-EMPTY-1",
    "TZ-NONE-1",
    "TZ-LOW-1",
    "TZ-SEC-4",
    "TZ-SEC-3",
    "TZ-B-1",
    "TZ-SEC-2",
    "TZ-SEC-1",
]


@pytest.fixture(scope="class")
def rpm_upload_apis(rpm_client):
    return RpmCompsApi(rpm_client), ContentAdvisoriesApi(rpm_client), ContentModulemdsApi(rpm_client)


@pytest.fixture(scope="class")
def comps_data(rpm_domain_factory, rpm_upload_apis, rpm_repository_api, monitor_task):
    """Two domains, each with a repository holding uploaded package groups and environments."""
    comps_api = rpm_upload_apis[0]
    repositories = []
    for xml in (COMPS_A, COMPS_B):
        domain, _remote, repository = rpm_domain_factory(url=None)
        with tempfile.NamedTemporaryFile("w", suffix=".xml") as comps_file:
            comps_file.write(xml)
            comps_file.flush()
            response = comps_api.rpm_comps_upload(
                file=comps_file.name, repository=repository.pulp_href, pulp_domain=domain.name
            )
        monitor_task(response.task)
        repositories.append((domain, rpm_repository_api.read(repository.pulp_href)))
    return repositories


@pytest.fixture(scope="class")
def errata_data(rpm_domain_factory, rpm_upload_apis, rpm_repository_api, monitor_task):
    """Two domains, each with a repository holding uploaded advisories."""
    advisories_api = rpm_upload_apis[1]
    repositories = []
    for advisories in (ADVISORIES_A, ADVISORIES_B):
        domain, _remote, repository = rpm_domain_factory(url=None)
        for advisory in advisories:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as advisory_file:
                json.dump(advisory, advisory_file)
                advisory_file.flush()
                response = advisories_api.create(
                    file=advisory_file.name, repository=repository.pulp_href, pulp_domain=domain.name
                )
            monitor_task(response.task)
        repositories.append((domain, rpm_repository_api.read(repository.pulp_href)))
    return repositories


@pytest.fixture(scope="class")
def module_data(rpm_domain_factory, rpm_upload_apis, rpm_repository_api, gen_object_with_cleanup, monitor_task):
    """
    Two domains, each with a synced ``rpm-with-modules`` repository (10 module versions in 7
    ``(name, stream)`` pairs); domain A's also holds ``emptymod:1``, a module without packages.
    """
    modulemds_api = rpm_upload_apis[2]
    domain_a, _remote, repository_a = rpm_domain_factory(url=RPM_WITH_MODULES_FIXTURE_URL)
    domain_b, _remote, repository_b = rpm_domain_factory(url=RPM_WITH_MODULES_FIXTURE_URL)
    empty_module = gen_object_with_cleanup(
        modulemds_api,
        RpmModulemd(
            name="emptymod",
            stream="1",
            version="1",
            context="deadbeef",
            arch="noarch",
            artifacts=[],
            dependencies=[],
            packages=[],
            snippet="emptymod",
            profiles=[],
            description="A module without packages",
        ),
        pulp_domain=domain_a.name,
    )
    response = rpm_repository_api.modify(repository_a.pulp_href, {"add_content_units": [empty_module.pulp_href]})
    monitor_task(response.task)
    return (domain_a, rpm_repository_api.read(repository_a.pulp_href)), (domain_b, repository_b)


@pytest.fixture(scope="class")
def package_data(rpm_domain_factory):
    """Two domains, each with a synced ``rpm-unsigned`` repository (35 packages, 31 names)."""
    return [rpm_domain_factory(url=RPM_UNSIGNED_FIXTURE_URL)[::2] for _ in range(2)]


# Tests


@pytest.mark.parallel
class TestRpmContentSearchPackages:
    def test_search_single_repository_version(self, setup_domain, search):
        domain, _remote, src, _dest = setup_domain()

        body = search("packages", [src], domain_name=domain.name)
        assert body["count"] == RPM_PACKAGE_COUNT

    def test_search_aggregates_across_domains(self, package_data, search):
        (domain_a, repo_a), (domain_b, repo_b) = package_data

        body = search("packages", [repo_a, repo_b], limit=1000)
        assert body["count"] == 2 * RPM_PACKAGE_COUNT
        assert _domains(body) == {domain_a.name, domain_b.name}

    def test_multiple_versions_in_one_domain(self, package_data, search):
        (_domain_a, repo_a), _ = package_data
        version_0 = repo_a.latest_version_href.replace("/versions/1/", "/versions/0/")

        body = search("packages", [version_0, repo_a])
        assert body["count"] == RPM_PACKAGE_COUNT

    def test_exact_name_filter(self, package_data, search):
        (_domain_a, repo_a), _ = package_data

        assert set(_names(search("packages", [repo_a], name="duck"))) == {"duck"}
        assert search("packages", [repo_a], name="duc")["count"] == 0

    @pytest.mark.parametrize("term", ["du", "DU", "Du"])
    def test_search_is_case_insensitive_name_prefix(self, package_data, search, term):
        (_domain_a, repo_a), _ = package_data

        body = search("packages", [repo_a], search=term)
        assert set(_names(body)) == {"duck"}
        assert body["count"] == 3

    def test_search_matches_prefix_only(self, package_data, search):
        (_domain_a, repo_a), _ = package_data

        assert set(_names(search("packages", [repo_a], search="d"))) == {"dog", "dolphin", "duck"}
        assert search("packages", [repo_a], search="uck")["count"] == 0

    def test_search_combines_with_exact_name(self, package_data, search):
        (_domain_a, repo_a), _ = package_data

        assert search("packages", [repo_a], name="duck", search="d")["count"] == 3
        assert search("packages", [repo_a], name="duck", search="x")["count"] == 0

    def test_offset_is_not_clamped(self, package_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = package_data

        for versions in ([repo_a], [repo_a, repo_b]):
            body = search("packages", versions, limit=1, offset=20000)
            assert body["results"] == []
            assert body["count"] == len(versions) * RPM_PACKAGE_COUNT
            assert parse_qs(urlparse(body["previous"]).query)["offset"] == ["19999"]

    def test_multi_domain_page_walk_is_stable(self, package_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = package_data
        everything = search("packages", [repo_a, repo_b], limit=1000)["results"]

        walked = _walk_pages(search, "packages", [repo_a, repo_b], limit=7)

        assert [result["pulp_href"] for result in walked] == [result["pulp_href"] for result in everything]
        assert len({result["pulp_href"] for result in walked}) == 2 * RPM_PACKAGE_COUNT

    def test_sorted_by_nevra(self, package_data, search):
        (_domain_a, repo_a), _ = package_data

        names = _names(search("packages", [repo_a], limit=1000))
        assert names == sorted(names)

    def test_missing_repository_version_param_is_400(self, search):
        search("packages", [], expected_status=400)

    def test_malformed_repository_version_href_is_400(self, search):
        search("packages", ["/api/pulp/does-not-exist/"], expected_status=400)


@pytest.mark.parallel
class TestRpmContentSearchPackageNames:
    def test_each_name_once(self, package_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = package_data

        body = search("package-names", [repo_a, repo_b], limit=3)
        assert _names(body) == ["bear", "camel", "cat"]
        assert body["count"] == 31

    def test_case_insensitive_prefix(self, package_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = package_data

        assert _names(search("package-names", [repo_a, repo_b], search="DU")) == ["duck"]
        assert _names(search("package-names", [repo_a, repo_b], search="d")) == ["dog", "dolphin", "duck"]

    def test_large_limit_and_paging(self, package_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = package_data

        everything = search("package-names", [repo_a, repo_b], limit=500)
        assert len(everything["results"]) == 31
        assert all(result["summary"] for result in everything["results"])
        walked = _walk_pages(search, "package-names", [repo_a, repo_b], limit=4)
        assert [result["name"] for result in walked] == _names(everything)


@pytest.mark.parallel
class TestRpmContentSearchPackageGroups:
    def test_case_insensitive_order(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        assert _names(search("package-groups", [repo_a, repo_b], limit=2)) == ["Alpha Group", "base system"]
        body = search("package-groups", [repo_a, repo_b])
        assert _names(body) == [
            "Alpha Group",
            "base system",
            "Beta Group",
            "Development Tools",
            "Shared Group",
            "zeta group",
        ]
        assert body["count"] == 6

    def test_order_is_the_same_for_one_and_many_domains(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        single = _names(search("package-groups", [repo_a]))
        multi = _names(search("package-groups", [repo_a, repo_b]))
        assert single == [name for name in multi if name in single]

    def test_packages_are_unioned_across_domains(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        body = search("package-groups", [repo_a, repo_b], search="shared")
        assert body["count"] == 1
        assert sorted(package["name"] for package in body["results"][0]["packages"]) == ["duck", "frog"]

    def test_search_is_case_insensitive_substring(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        assert _names(search("package-groups", [repo_a, repo_b], search="GROUP")) == [
            "Alpha Group",
            "Beta Group",
            "Shared Group",
            "zeta group",
        ]

    def test_offset(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        body = search("package-groups", [repo_a, repo_b], limit=2, offset=2)
        assert _names(body) == ["Beta Group", "Development Tools"]
        assert body["count"] == 6

    def test_missing_repository_version_param_is_400(self, search):
        search("package-groups", [], expected_status=400)


@pytest.mark.parallel
class TestRpmContentSearchEnvironments:
    def test_environments_endpoint(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        body = search("environments", [repo_a, repo_b])
        assert _names(body) == ["minimal install", "Shared Env", "Virtualization Host", "Workstation"]
        assert body["count"] == 4

    def test_search_and_limit(self, comps_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = comps_data

        assert _names(search("environments", [repo_a, repo_b], search="ENV")) == ["Shared Env"]
        assert _names(search("environments", [repo_a, repo_b], limit=1, offset=1)) == ["Shared Env"]

    def test_inaccessible_domain_silently_excluded(self, comps_data, search, gen_user):
        (domain_a, repo_a), (_domain_b, repo_b) = comps_data
        limited_user = gen_user(object_roles=[("core.domain_viewer", domain_a.pulp_href)])

        body = search("environments", [repo_a, repo_b], auth=(limited_user.username, limited_user.password))
        assert _names(body) == ["minimal install", "Shared Env", "Workstation"]
        assert _domains(body) == {domain_a.name}


@pytest.mark.parallel
class TestRpmContentSearchErrata:
    def _ids(self, body):
        return [result["id"] for result in body["results"]]

    def test_default_sort_is_newest_issued_first_with_tie_breaker(self, errata_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        assert self._ids(search("errata", [repo_a, repo_b])) == ERRATA_DEFAULT_ORDER
        assert self._ids(search("errata", [repo_a, repo_b], sort_by="-issued_date")) == ERRATA_DEFAULT_ORDER
        assert self._ids(search("errata", [repo_a, repo_b], sort_by="issued_date")) == ERRATA_DEFAULT_ORDER[::-1]

    @pytest.mark.parametrize("sort_by", ["-issued_date", "issued_date", "severity", "-type", "id"])
    def test_page_walk_is_stable(self, errata_data, search, sort_by):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data
        everything = self._ids(search("errata", [repo_a, repo_b], sort_by=sort_by))

        walked = _walk_pages(search, "errata", [repo_a, repo_b], limit=1, sort_by=sort_by)
        assert [result["id"] for result in walked] == everything

    @pytest.mark.parametrize(
        ("severity", "expected"),
        [
            ("Important", {"TZ-SEC-1", "TZ-B-1"}),
            ("Critical,Important", {"TZ-SEC-1", "TZ-SEC-2", "TZ-B-1"}),
            ("None", {"TZ-NONE-1"}),
            ("Unknown", {"TZ-LOW-1", "TZ-NONE-1", "TZ-EMPTY-1", "TZ-NEW-1", "TZ-ODD-1"}),
            ("important", {"TZ-LOW-1"}),
        ],
    )
    def test_severity_uses_pulp_values(self, errata_data, search, severity, expected):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        body = search("errata", [repo_a, repo_b], severity=severity)
        assert set(self._ids(body)) == expected
        assert body["count"] == len(expected)

    @pytest.mark.parametrize("types", ["other", "other,newpackage"])
    def test_type_other_includes_newpackage(self, errata_data, search, types):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        assert set(self._ids(search("errata", [repo_a, repo_b], type=types))) == {"TZ-NEW-1", "TZ-ODD-1"}

    def test_type_filter(self, errata_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        body = search("errata", [repo_a, repo_b], type="security")
        assert set(self._ids(body)) == {"TZ-SEC-1", "TZ-SEC-2", "TZ-SEC-3", "TZ-SEC-4", "TZ-B-1"}

    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("widget", {"TZ-SEC-1"}),  # summary
            ("Kernel", {"TZ-SEC-1"}),  # title
            ("tz-sec", {"TZ-SEC-1", "TZ-SEC-2", "TZ-SEC-3", "TZ-SEC-4"}),  # id
        ],
    )
    def test_search_matches_id_title_and_summary(self, errata_data, search, term, expected):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        assert set(self._ids(search("errata", [repo_a, repo_b], search=term))) == expected

    def test_cves_are_reference_objects(self, errata_data, search):
        (_domain_a, repo_a), _ = errata_data

        body = search("errata", [repo_a], search="widget")
        cves = body["results"][0]["cves"]
        assert sorted(cve["ref_id"] for cve in cves) == ["CVE-2026-1111", "CVE-2026-2222"]
        assert {cve["ref_type"] for cve in cves} == {"cve"}

    def test_nullable_fields_are_nullable_in_the_schema(self, content_search_schemas):
        # pulp_rpm stores these as NULL-able columns; a non-nullable schema breaks strict clients.
        errata = content_search_schemas["errata"]["properties"]["results"]["items"]["properties"]
        assert errata["updated_date"]["type"] == ["string", "null"]
        reference = errata["cves"]["items"]["properties"]
        assert reference["ref_id"]["type"] == ["string", "null"]
        assert reference["title"]["type"] == ["string", "null"]

    def test_offset_is_not_clamped(self, errata_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = errata_data

        body = search("errata", [repo_a, repo_b], limit=1, offset=20000)
        assert body["results"] == []
        assert parse_qs(urlparse(body["previous"]).query)["offset"] == ["19999"]

    def test_unsupported_sort_by_is_400(self, errata_data, search):
        (_domain_a, repo_a), _ = errata_data

        search("errata", [repo_a], sort_by="nope", expected_status=400)
        search("errata", [repo_a], sort_by="issued_date:desc", expected_status=400)

    def test_synced_advisories_across_domains(self, setup_domain, search):
        _domain_a, _remote_a, src_a, _dest_a = setup_domain()
        _domain_b, _remote_b, src_b, _dest_b = setup_domain()

        assert search("errata", [src_a, src_b])["count"] == 2 * RPM_ADVISORY_COUNT

    def test_missing_repository_version_param_is_400(self, search):
        search("errata", [], expected_status=400)


@pytest.mark.parallel
class TestRpmContentSearchModuleStreams:
    def _streams(self, body):
        return [(result["name"], result["stream"]) for result in body["results"]]

    def test_one_row_per_name_and_stream_with_lowest_version(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        body = search("module-streams", [repo_a, repo_b], search="duck")
        assert self._streams(body) == [("duck", "0"), ("duck", "4"), ("duck", "5"), ("duck", "6")]
        assert body["results"][0]["version"] == "20180704244205"
        assert body["count"] == 4

    def test_all_streams_including_modules_without_packages(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        body = search("module-streams", [repo_a, repo_b])
        assert body["count"] == 8
        assert ("emptymod", "1") in self._streams(body)
        emptymod = next(result for result in body["results"] if result["name"] == "emptymod")
        assert emptymod["packages"] == []

    def test_has_packages(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        with_packages = search("module-streams", [repo_a, repo_b], has_packages="true")
        assert with_packages["count"] == 7
        assert "emptymod" not in _names(with_packages)
        without_packages = search("module-streams", [repo_a, repo_b], has_packages="false")
        assert self._streams(without_packages) == [("emptymod", "1")]
        search("module-streams", [repo_a], has_packages="maybe", expected_status=400)

    def test_search_matches_module_name_only(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        assert search("module-streams", [repo_a, repo_b], search="0")["count"] == 0
        assert search("module-streams", [repo_a, repo_b], search="DUCK")["count"] == 4

    def test_limit_and_count_are_streams(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        body = search("module-streams", [repo_a, repo_b], limit=2)
        assert self._streams(body) == [("duck", "0"), ("duck", "4")]
        assert body["count"] == 8

    def test_rpm_names_filter_and_packages_field(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        body = search("module-streams", [repo_a, repo_b], rpm_names="frog")
        assert self._streams(body) == [("duck", "6")]
        assert body["results"][0]["packages"] == ["duck", "frog"]

    def test_sort_by(self, module_data, search):
        (_domain_a, repo_a), (_domain_b, repo_b) = module_data

        assert _names(search("module-streams", [repo_a, repo_b], sort_by="-name", limit=1)) == ["walrus"]
        search("module-streams", [repo_a], sort_by="bogus", expected_status=400)

    def test_empty_repository(self, setup_domain, search):
        domain, _remote, src, _dest = setup_domain()

        body = search("module-streams", [src], domain_name=domain.name)
        assert body == {"count": 0, "next": None, "previous": None, "results": []}

    def test_missing_repository_version_param_is_400(self, search):
        search("module-streams", [], expected_status=400)


@pytest.mark.parallel
class TestRpmContentSearchRBACExclusion:
    def test_inaccessible_domain_silently_excluded(self, setup_domain, search, gen_user):
        """A caller without read access to one of the referenced domains should see that
        domain's content silently excluded, not an error -- same behavior as ContentView
        search (see test_content_view_search.py::TestContentViewRBACExclusion)."""
        domain_a, _remote_a, src_a, _dest_a = setup_domain()
        _domain_b, _remote_b, src_b, _dest_b = setup_domain()
        limited_user = gen_user(object_roles=[("core.domain_viewer", domain_a.pulp_href)])

        body = search(
            "packages",
            [src_a, src_b],
            domain_name=domain_a.name,
            auth=(limited_user.username, limited_user.password),
        )
        assert body["count"] == RPM_PACKAGE_COUNT
        assert _domains(body) == {domain_a.name}
