import base64
import os

import pytest
import requests

from pulpcore.client.pulp_rpm import RpmRepositorySyncURL
from pulpcore.client.pulp_service import ServiceEnvVarHeaderContentGuard

from pulp_service.tests.functional.constants import (
    ENVVAR_HEADER_GUARD_ENV_VAR,
    ENVVAR_HEADER_GUARD_HEADER_NAME,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(ENVVAR_HEADER_GUARD_ENV_VAR),
    reason=f"{ENVVAR_HEADER_GUARD_ENV_VAR} must be set in the pulp container environment",
)


@pytest.fixture
def configure_envvar_guarded_content(  # noqa: PLR0913
    add_to_cleanup,
    monitor_task,
    rpm_repository_api,
    rpm_repository_factory,
    rpm_rpmremote_factory,
    rpm_distribution_factory,
    service_envvar_header_content_guards_api_client,
):
    def _configure_guarded_content(name="envvar-guard-test"):
        remote = rpm_rpmremote_factory()
        repo = rpm_repository_factory(autopublish=True, metadata_checksum_type="sha512")

        content_guard = service_envvar_header_content_guards_api_client.create(
            service_env_var_header_content_guard=ServiceEnvVarHeaderContentGuard(
                name=name,
                header_name=ENVVAR_HEADER_GUARD_HEADER_NAME,
                env_var=ENVVAR_HEADER_GUARD_ENV_VAR,
            )
        )
        add_to_cleanup(service_envvar_header_content_guards_api_client, content_guard.pulp_href)

        distribution = rpm_distribution_factory(
            repository=repo.pulp_href,
            content_guard=content_guard.pulp_href,
        )

        repository_sync_data = RpmRepositorySyncURL(remote=remote.pulp_href)
        sync_response = rpm_repository_api.sync(repo.pulp_href, repository_sync_data)
        monitor_task(sync_response.task)

        return content_guard, distribution

    return _configure_guarded_content


def test_api_create_envvar_header_content_guard(service_envvar_header_content_guards_api_client, add_to_cleanup):
    guard = service_envvar_header_content_guards_api_client.create(
        service_env_var_header_content_guard=ServiceEnvVarHeaderContentGuard(
            name="envvar-guard-api-test",
            header_name=ENVVAR_HEADER_GUARD_HEADER_NAME,
            env_var=ENVVAR_HEADER_GUARD_ENV_VAR,
        )
    )
    add_to_cleanup(service_envvar_header_content_guards_api_client, guard.pulp_href)

    retrieved = service_envvar_header_content_guards_api_client.read(guard.pulp_href)
    assert retrieved.name == "envvar-guard-api-test"
    assert retrieved.header_name == ENVVAR_HEADER_GUARD_HEADER_NAME
    assert retrieved.env_var == ENVVAR_HEADER_GUARD_ENV_VAR
    assert not hasattr(retrieved, "header_value")


def test_denied_without_header(configure_envvar_guarded_content):
    _, distribution = configure_envvar_guarded_content()
    response = requests.get(url=distribution.base_url, timeout=30)
    assert response.status_code == 403


def test_denied_with_wrong_header(configure_envvar_guarded_content):
    _, distribution = configure_envvar_guarded_content(name="envvar-guard-wrong-header")
    headers = {
        ENVVAR_HEADER_GUARD_HEADER_NAME: base64.b64encode(b"wrong-secret").decode("ascii"),
    }
    response = requests.get(url=distribution.base_url, headers=headers, timeout=30)
    assert response.status_code == 403


def test_allowed_with_matching_header(configure_envvar_guarded_content):
    secret = os.environ[ENVVAR_HEADER_GUARD_ENV_VAR].strip()
    _, distribution = configure_envvar_guarded_content(name="envvar-guard-allowed")
    headers = {
        ENVVAR_HEADER_GUARD_HEADER_NAME: base64.b64encode(secret.encode("utf-8")).decode("ascii"),
    }
    response = requests.get(url=distribution.base_url, headers=headers, timeout=30)
    assert response.status_code == 200
    assert "repodata" in str(response.content)
