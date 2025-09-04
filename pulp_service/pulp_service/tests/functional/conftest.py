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
