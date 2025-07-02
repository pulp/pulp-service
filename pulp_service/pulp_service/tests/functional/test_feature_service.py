import pytest
import requests

from pulpcore.client.pulp_rpm import RpmRepositorySyncURL
from pulpcore.client.pulp_service import ServiceFeatureContentGuard
from pulp_service.tests.functional.constants import (
    CONTENT_GUARD_FEATURES,
    CONTENT_GUARD_FEATURES_NOT_SUBSCRIBED,
    CONTENT_GUARD_FILTER,
    CONTENT_GUARD_HEADER_NAME,
    CONTENT_GUARD_HEADER_VALUE,
)


@pytest.fixture
def configure_guarded_content(
    add_to_cleanup,
    monitor_task,
    rpm_repository_api,
    rpm_repository_factory,
    rpm_rpmremote_factory,
    rpm_distribution_factory,
    service_content_guards_api_client,
):
    def _configure_guarded_content(features):
        # create the repo and remote
        remote = rpm_rpmremote_factory()
        repo = rpm_repository_factory(autopublish=True, metadata_checksum_type="sha512")

        # create the content guard
        content_certguard = service_content_guards_api_client.create(
            service_feature_content_guard=ServiceFeatureContentGuard(
                name="test",
                header_name=CONTENT_GUARD_HEADER_NAME,
                features=features,
                jq_filter=CONTENT_GUARD_FILTER,
            )
        )
        add_to_cleanup(service_content_guards_api_client, content_certguard.pulp_href)

        # create the distribution
        distribution = rpm_distribution_factory(
            repository=repo.pulp_href, content_guard=content_certguard.pulp_href
        )

        # sync content
        repository_sync_data = RpmRepositorySyncURL(remote=remote.pulp_href)
        sync_response = rpm_repository_api.sync(repo.pulp_href, repository_sync_data)
        monitor_task(sync_response.task)

        return distribution

    return _configure_guarded_content


def test_forbidden_feature_service(configure_guarded_content):
    distribution = configure_guarded_content(features=CONTENT_GUARD_FEATURES_NOT_SUBSCRIBED)
    headers = {CONTENT_GUARD_HEADER_NAME: CONTENT_GUARD_HEADER_VALUE}
    url = distribution.base_url
    response = requests.get(url=url, headers=headers)

    assert response.status_code == 403


def test_entitled_feature_service(configure_guarded_content):
    distribution = configure_guarded_content(features=CONTENT_GUARD_FEATURES)
    headers = {CONTENT_GUARD_HEADER_NAME: CONTENT_GUARD_HEADER_VALUE}
    url = distribution.base_url
    response = requests.get(url=url, headers=headers)

    assert response.status_code == 200
    assert "repodata" in str(response.content)
