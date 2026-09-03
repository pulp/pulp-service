import uuid
from types import SimpleNamespace
from urllib.parse import urljoin

import pytest
import requests

from pulpcore.tests.functional.utils import BindingsNamespace

# Bindings API Fixtures


@pytest.fixture(scope="session")
def service_bindings(_api_client_set, bindings_cfg):
    """
    A namespace providing preconfigured pulp_service api clients.
    """
    from pulpcore.client import pulp_service as service_bindings_module

    api_client = service_bindings_module.ApiClient(bindings_cfg)
    _api_client_set.add(api_client)
    yield BindingsNamespace(service_bindings_module, api_client)
    _api_client_set.remove(api_client)


@pytest.fixture(scope="session")
def vuln_report_service_api(service_bindings):
    """Vulnerability Report API fixture."""
    return service_bindings.VulnReportServiceApi


@pytest.fixture(scope="session")
def pypi_yank_monitor_api(service_bindings):
    """PyPI Yank Monitor API fixture."""
    return service_bindings.PypiYankMonitorApi


@pytest.fixture(scope="session")
def service_content_guards_api_client(service_bindings):
    """Api for service content guards."""
    return service_bindings.ContentguardsFeatureApi


@pytest.fixture
def template_domain_s3(pulpcore_bindings, gen_object_with_cleanup):
    """Ensure the 'template-domain-s3' domain that CreateDomainView copies storage from exists.

    Real environments create this out of band; on a bare test stack we create it with
    FileSystem storage (not real S3) so the copied storage_class works locally. Created as
    plain admin with no x-rh-identity, so the post_create_domain dual-write no-ops for it.
    """
    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    existing = pulpcore_bindings.DomainsApi.list(name="template-domain-s3")
    if existing.count:
        return existing.results[0]
    return gen_object_with_cleanup(
        pulpcore_bindings.DomainsApi,
        {
            "name": "template-domain-s3",
            "storage_class": "pulpcore.app.models.storage.FileSystem",
            "storage_settings": {"MEDIA_ROOT": "/var/lib/pulp/media/"},
        },
    )


def _teardown_service_domains(created, pulpcore_bindings, bindings_cfg, monitor_task):
    """Delete domains created by create_service_domain, monitoring each delete task.

    Cleanup runs as admin (bindings default), regardless of the auth used to create. Each
    delete task is monitored to completion so domains are actually gone before the next test
    lists, otherwise same-org (rh-org-<org_id>) leaks inflate later count assertions.

    Collect per-domain failures and re-raise instead of swallowing them: a silent cleanup
    failure leaks the domain and inflates later same-org list/count assertions, making the
    suite order-dependent. Every domain is attempted, then the errors are re-raised so the
    leak is visible.
    """
    admin_auth = (bindings_cfg.username, bindings_cfg.password)

    def _delete_href(href):
        resp = requests.delete(urljoin(bindings_cfg.host, href), auth=admin_auth, timeout=30)
        if resp.status_code == 202:
            monitor_task(resp.json()["task"])

    def _cleanup_domain(href, name):
        # Repository.pulp_domain / Distribution.pulp_domain are protected FKs, so a domain
        # cannot be deleted while it holds content. Tests create that content via the
        # class-scoped gen_object_with_cleanup (which cleans only at module end), so delete
        # the domain's distributions and repositories here first, else the domain delete task
        # fails with ProtectedError and the domain leaks into later same-org list assertions.
        for api in (pulpcore_bindings.DistributionsApi, pulpcore_bindings.RepositoriesApi):
            for obj in api.list(pulp_domain=name).results:
                _delete_href(obj.pulp_href)
        _delete_href(href)

    pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)
    cleanup_errors = []
    for href, name in created:
        try:
            _cleanup_domain(href, name)
        except Exception as exc:
            cleanup_errors.append((name, exc))
    if cleanup_errors:
        raise RuntimeError(f"create_service_domain cleanup failed for domains: {cleanup_errors}")


@pytest.fixture
def create_service_domain(pulpcore_bindings, bindings_cfg, template_domain_s3, monitor_task):
    """Create a domain via the self-service ``POST /api/pulp/create-domain/`` endpoint.

    After PULP-2120 the default permission class is ``PulpServiceAccessPolicy``, so a
    non-admin user (X-RH-IDENTITY header or basic auth) can no longer create a domain via
    the generic ``DomainsApi`` (that needs ``core.add_domain``). The self-service endpoint
    is the supported path and drives the ``post_create_domain`` dual-write.

    Pass ``identity_header`` for header auth or ``auth=(user, password)`` for basic auth.
    Returns a ``SimpleNamespace`` with ``name`` and ``pulp_href``; registers admin-auth
    cleanup for the created domains.
    """
    created = []

    def _create(name=None, *, identity_header=None, auth=None, group_name=None):
        name = name or str(uuid.uuid4())
        headers = {}
        if identity_header is not None:
            headers["x-rh-identity"] = (
                identity_header.decode() if isinstance(identity_header, bytes) else identity_header
            )
        body = {"name": name}
        if group_name:
            body["group_name"] = group_name
        resp = requests.post(
            urljoin(bindings_cfg.host, "/api/pulp/create-domain/"),
            headers=headers or None,
            json=body,
            auth=auth,
            timeout=30,
        )
        assert resp.status_code == 201, f"create-domain failed: {resp.status_code} {resp.text}"
        domain = resp.json()
        created.append((domain["pulp_href"], domain["name"]))
        return SimpleNamespace(name=domain["name"], pulp_href=domain["pulp_href"])

    yield _create

    _teardown_service_domains(created, pulpcore_bindings, bindings_cfg, monitor_task)


@pytest.fixture
def gen_group(pulpcore_bindings, gen_object_with_cleanup):
    """A fixture to create a group."""

    def _gen_group(name=None):
        name = name or str(uuid.uuid4())
        return gen_object_with_cleanup(pulpcore_bindings.GroupsApi, {"name": name})

    return _gen_group


@pytest.fixture()
def cleanup_auth_headers(request, pulpcore_bindings):
    """
    Automatically clean up x-rh-identity headers before each test.

    This prevents authentication headers from leaking between tests
    and affecting other test results.
    """
    # Clean up after the test runs
    if hasattr(pulpcore_bindings, "DomainsApi"):
        pulpcore_bindings.DomainsApi.api_client.default_headers.pop("x-rh-identity", None)

    # Try to clean up file_bindings if it was used in the test
    if "file_bindings" in request.fixturenames:
        file_bindings = request.getfixturevalue("file_bindings")
        if hasattr(file_bindings, "RepositoriesFileApi"):
            file_bindings.RepositoriesFileApi.api_client.default_headers.pop("x-rh-identity", None)

    # Try to clean up python_bindings if it was used in the test
    if "python_bindings" in request.fixturenames:
        python_bindings = request.getfixturevalue("python_bindings")
        if hasattr(python_bindings, "RepositoriesPythonApi"):
            python_bindings.RepositoriesPythonApi.api_client.default_headers.pop("x-rh-identity", None)
        if hasattr(python_bindings, "DistributionsPypiApi"):
            python_bindings.DistributionsPypiApi.api_client.default_headers.pop("x-rh-identity", None)
