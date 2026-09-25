import importlib.util
import json
import stat
import threading
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "audit-content-guards.py"
SPEC = importlib.util.spec_from_file_location("audit_content_guards", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


class FakeResponse:
    def __init__(
        self, payload, url="https://packages.example.test/api", status_code=200
    ):
        self._payload = payload
        self.url = url
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return next(self.responses)


def test_pagination_follows_same_host_next_links():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [{"name": "first"}],
                    "next": "https://packages.example.test/page-2",
                }
            ),
            FakeResponse({"results": [{"name": "second"}], "next": None}),
        ]
    )
    progress = []
    client = audit.PulpClient(
        "https://packages.example.test", session, progress=progress.append
    )

    results = client.get_pages(
        "https://packages.example.test/page-1", params={"limit": 100}, label="test"
    )

    assert [item["name"] for item in results] == ["first", "second"]
    assert session.urls[0][1]["params"] == {"limit": 100}
    assert session.urls[1][1]["params"] is None
    assert progress == [
        "test: fetching page 1",
        "test: fetching page 2",
        "test: collected 2 records",
    ]


def test_pagination_rejects_cross_host_next_link():
    session = FakeSession(
        [FakeResponse({"results": [], "next": "https://other.example.test/page-2"})]
    )
    client = audit.PulpClient("https://packages.example.test", session)

    with pytest.raises(audit.AuditError, match="host mismatch"):
        client.get_pages("https://packages.example.test/page-1")


def test_distribution_collection_urls_have_trailing_slash():
    session = FakeSession([FakeResponse({"results": [], "next": None})])
    client = audit.PulpClient("https://packages.example.test", session)

    client.list_distributions("tenant-a", "distributions")

    assert session.urls[0][0].endswith("/api/v3/distributions/")


def test_openapi_discovery_returns_only_distribution_collections():
    list_operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/PaginatedList"}
                    }
                }
            }
        }
    }
    schema = {
        "paths": {
            "/pulp/{pulp_domain}/api/v3/distributions/": {"get": list_operation},
            "/pulp/{pulp_domain}/api/v3/distributions/rpm/rpm/": {
                "get": list_operation
            },
            "/pulp/{pulp_domain}/api/v3/distributions/container/container/": {
                "get": list_operation
            },
            "/pulp/{pulp_domain}/api/v3/distributions/rpm/rpm/{uuid}/": {"get": {}},
            "/pulp/{pulp_domain}/api/v3/distributions/rpm/rpm/search/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/SearchResponse"
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/pulp/{pulp_domain}/api/v3/distributions/rpm/rpm/health/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "array"}}
                            }
                        }
                    }
                }
            },
            "/pulp/{pulp_domain}/api/v3/repositories/rpm/rpm/": {"get": {}},
        }
    }

    assert audit.discover_distribution_endpoints(schema) == [
        {"name": "container/container", "path": "distributions/container/container"},
        {"name": "rpm/rpm", "path": "distributions/rpm/rpm"},
    ]


def test_public_domains_are_excluded_by_default():
    domains = [{"name": "public-copr"}, {"name": "tenant-a"}]

    assert audit.filter_domains(domains, include_public=False) == [{"name": "tenant-a"}]
    assert audit.filter_domains(domains, include_public=True) == domains


def test_effective_guard_prefers_explicit_guard():
    assert audit._guard_href("/guards/explicit/") == "/guards/explicit/"
    assert audit.classify_pulp_access("/guards/explicit/") == "pulp_guarded"
    assert audit.classify_pulp_access(None) == "pulp_unguarded"


def test_probe_url_supports_base_url_and_template_forms():
    assert (
        audit.build_probe_url(
            "https://packages.test/api/pulp-content", "tenant-a", "repo"
        )
        == "https://packages.test/api/pulp-content/tenant-a/repo/"
    )
    assert (
        audit.build_probe_url(
            "https://proxy.test/{domain}/{base_path}", "tenant-a", "repo"
        )
        == "https://proxy.test/tenant-a/repo"
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "probe_allowed"),
        (204, "probe_allowed"),
        (401, "probe_denied"),
        (403, "probe_denied"),
        (404, "probe_indeterminate"),
    ],
)
def test_probe_status_classification(status, expected):
    assert audit.classify_probe_status(status) == expected


def test_guard_type_does_not_expose_guard_value():
    href = "/api/pulp/tenant-a/api/v3/contentguards/service/feature/123/"
    assert audit.guard_type(href) == "feature"


def test_guard_detail_uses_longer_timeout_only_for_guard_retrieval():
    href = "/api/pulp/tenant-a/api/v3/contentguards/core/header/123/"
    session = FakeSession(
        [FakeResponse({"name": "ordinary"}), FakeResponse({"name": "guard"})]
    )
    client = audit.PulpClient(
        "https://packages.example.test", session, guard_detail_timeout=150
    )

    client.get_json("https://packages.example.test/api/ordinary")
    client.resolve_guard(href, {})

    assert session.urls[0][1]["timeout"] == audit.DEFAULT_TIMEOUT
    assert session.urls[1][1]["timeout"] == 150


def test_guard_detail_timeout_default_is_120_seconds():
    assert audit.DEFAULT_GUARD_DETAIL_TIMEOUT == 120.0


def test_cyclic_composite_guards_are_serializable(monkeypatch):
    guard_a = "/api/pulp/tenant-a/api/v3/contentguards/core/composite/a/"
    guard_b = "/api/pulp/tenant-a/api/v3/contentguards/core/composite/b/"
    payloads = {
        f"https://packages.example.test{guard_a}": {"name": "a", "guards": [guard_b]},
        f"https://packages.example.test{guard_b}": {"name": "b", "guards": [guard_a]},
    }
    client = audit.PulpClient("https://packages.example.test", FakeSession([]))
    monkeypatch.setattr(client, "get_json", lambda url, **kwargs: payloads[url])

    result = client.resolve_guard(guard_a, {})

    assert result["guards"][0]["guards"][0]["cycle"] is True
    json.dumps(result)


def test_probe_session_does_not_use_environment_authentication():
    session = audit.create_probe_session()

    assert session.auth is None
    assert session.trust_env is False

    session.close()


def test_probe_without_explicit_session_uses_safe_session(monkeypatch):
    observed = {}

    class FakeProbeSession:
        auth = None
        trust_env = False

        def __enter__(self):
            observed["session"] = self
            return self

        def __exit__(self, *args):
            observed["closed"] = True

        def head(self, *args, **kwargs):
            observed["auth"] = self.auth
            observed["trust_env"] = self.trust_env
            response = FakeResponse({})
            response.headers = {}
            response.close = lambda: None
            return response

    monkeypatch.setattr(audit, "create_probe_session", lambda: FakeProbeSession())
    result = audit.probe_endpoint(None, "https://probe.example.test/path", "probe", 1)

    assert result["classification"] == "probe_allowed"
    assert observed["auth"] is None
    assert observed["trust_env"] is False
    assert observed["closed"] is True


def test_authenticated_client_rejects_non_https_base_url():
    with pytest.raises(audit.AuditError, match="https"):
        audit.PulpClient("http://packages.example.test", FakeSession([]))


def test_authenticated_client_rejects_redirect_response():
    response = FakeResponse({}, url="https://packages.example.test/api")
    response.status_code = 302
    response.ok = True
    client = audit.PulpClient("https://packages.example.test", FakeSession([response]))

    with pytest.raises(audit.AuditError, match="HTTP 302"):
        client.get_json("https://packages.example.test/api")


def test_retryable_response_is_retried_with_shared_limiter(monkeypatch):
    session = FakeSession(
        [
            FakeResponse({}, status_code=503),
            FakeResponse({"ok": True}),
        ]
    )
    monkeypatch.setattr(audit, "_backoff_seconds", lambda attempt: 0)
    client = audit.PulpClient(
        "https://packages.example.test",
        session,
        request_limiter=audit.RequestRateLimiter(1000),
    )

    assert client.get_json("https://packages.example.test/api") == {"ok": True}
    assert len(session.urls) == 2


def test_audit_domain_reports_defaults_unused_guards_and_deduplicates_distributions():
    default_guard = "/api/pulp/tenant-a/api/v3/contentguards/core/header/default/"
    explicit_guard = "/api/pulp/tenant-a/api/v3/contentguards/core/composite/explicit/"

    class DomainClient:
        def list_domain_guards(self, domain_name):
            assert domain_name == "tenant-a"
            return [{"pulp_href": default_guard}, {"pulp_href": explicit_guard}]

        def list_distributions(self, domain_name, endpoint_path):
            assert domain_name == "tenant-a"
            distributions = [
                {
                    "pulp_href": "/distributions/1/",
                    "name": "defaulted",
                    "base_path": "defaulted",
                    "content_guard": None,
                },
                {
                    "pulp_href": "/distributions/2/",
                    "name": "explicit",
                    "base_path": "explicit",
                    "content_guard": explicit_guard,
                },
            ]
            return distributions if endpoint_path.endswith("rpm") else distributions[1:]

        def resolve_guard(self, href, cache):
            return {"pulp_href": href, "type": audit.guard_type(href)}

    result = audit.audit_domain(
        DomainClient(),
        {
            "name": "tenant-a",
            "pulp_href": "/domains/tenant-a/",
            "default_content_guard": default_guard,
        },
        [
            {"name": "rpm/rpm", "path": "distributions/rpm/rpm"},
            {"name": "generic", "path": "distributions"},
        ],
        [],
        10,
        {},
    )

    assert result["content_guard_count"] == 2
    assert result["default_guard"]["pulp_href"] == default_guard
    assert len(result["content_guards"]) == 2
    assert len(result["distributions"]) == 2
    assert result["distributions"][0]["pulp_classification"] == "pulp_guarded"
    assert result["distributions"][1]["explicit_guard_type"] == "composite"


def test_parallel_report_keeps_domain_order(monkeypatch):
    class Client:
        base_url = "https://packages.example.test"
        timeout = 1
        request_limiter = audit.RequestRateLimiter(1000)
        guard_cache_lock = threading.RLock()

        def list_domains(self):
            return [{"name": "domain-b"}, {"name": "domain-a"}]

        def list_distribution_endpoints(self):
            return [{"name": "file/file", "path": "distributions/file/file"}]

    def fake_worker(base_url, timeout, guard_detail_timeout, domain, *args, **kwargs):
        assert guard_detail_timeout == 180
        return {
            "name": domain["name"],
            "pulp_href": f"/domains/{domain['name']}/",
            "has_content_guard": True,
            "distributions": [],
        }

    monkeypatch.setattr(audit, "audit_domain_in_worker", fake_worker)
    report = audit.build_report(
        Client(), "stage", False, [], 1, workers=2, guard_detail_timeout=180
    )

    assert [domain["name"] for domain in report["domains"]] == ["domain-a", "domain-b"]
    assert report["metadata"]["workers"] == 2
    assert report["metadata"]["guard_detail_timeout"] == 180


def test_serial_report_uses_configured_guard_detail_timeout(monkeypatch):
    client = audit.PulpClient("https://packages.example.test", FakeSession([]))
    client.list_domains = lambda: [{"name": "tenant-a", "pulp_href": "/domains/a/"}]
    client.list_distribution_endpoints = lambda: [
        {"name": "rpm/rpm", "path": "distributions/rpm/rpm"}
    ]
    observed = []

    def fake_audit_domain(client, domain, *args):
        observed.append(client.guard_detail_timeout)
        return {
            "name": domain["name"],
            "pulp_href": domain["pulp_href"],
            "has_content_guard": True,
            "distributions": [],
        }

    monkeypatch.setattr(audit, "audit_domain", fake_audit_domain)
    report = audit.build_report(
        client, "stage", False, [], 1, workers=1, guard_detail_timeout=180
    )

    assert observed == [180]
    assert report["metadata"]["guard_detail_timeout"] == 180


def test_retry_manifest_is_atomic_checkpoint_and_retryable(tmp_path):
    path = tmp_path / "failed.json"
    operation = {
        "id": "sha256:operation",
        "kind": "audit_domain",
        "domain": {"name": "tenant-a", "pulp_href": "/domains/tenant-a/"},
        "state": "pending",
    }
    manifest = audit.RetryManifest(str(path), {"environment": "stage"}, [operation])
    manifest.write()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manifest.mark_failure(operation["id"], "HTTP 503")
    loaded, digest = audit._load_retry_manifest(str(path))
    assert digest
    assert loaded["operations"][0]["state"] == "failed"
    assert loaded["operations"][0]["error"] == "HTTP 503"

    manifest.mark_success(operation["id"])
    manifest.finish()
    assert json.loads(path.read_text())["state"] == "complete"


def test_retry_scope_normalizes_retired_generic_distribution_endpoint():
    old_scope = {"distribution_endpoints": ["distributions", "distributions/rpm/rpm"]}
    current_scope = {"distribution_endpoints": ["distributions/rpm/rpm"]}

    assert audit._scope_for_comparison(old_scope) == audit._scope_for_comparison(
        current_scope
    )
