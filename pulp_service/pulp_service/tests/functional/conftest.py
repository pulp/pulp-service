import uuid
import pytest

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
def service_content_guards_api_client(service_bindings):
    """Api for service content guards."""
    return service_bindings.ContentguardsFeatureApi


@pytest.fixture
def gen_group(pulpcore_bindings, gen_object_with_cleanup):
    """A fixture to create a group."""
    def _gen_group(name=None):
        name = name or str(uuid.uuid4())
        return gen_object_with_cleanup(
            pulpcore_bindings.GroupsApi, {"name": name}
        )
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
            python_bindings.RepositoriesPythonApi.api_client.default_headers.pop(
                "x-rh-identity", None
            )
        if hasattr(python_bindings, "DistributionsPypiApi"):
            python_bindings.DistributionsPypiApi.api_client.default_headers.pop(
                "x-rh-identity", None
            )
