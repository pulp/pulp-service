import requests

from pulpcore.client.pulp_rpm import RpmRepositorySyncURL
from pulpcore.client.pulp_service import ServiceFeatureContentGuard
from pulp_service.tests.functional.constants import (
    CONTENT_GUARD_HEADER_NAME,
    CONTENT_GUARD_HEADER_VALUE,
    CONTENT_GUARD_FEATURES,
    CONTENT_GUARD_FILTER,
)


def test_feature_service(
    add_to_cleanup,
    monitor_task,
    rpm_repository_api,
    rpm_repository_factory,
    rpm_rpmremote_factory,
    rpm_distribution_factory,
    service_content_guards_api_client,
):
    # create the repo and remote
    remote = rpm_rpmremote_factory()
    repo = rpm_repository_factory(autopublish=True, metadata_checksum_type="sha512")

    # create the content guard
    content_certguard = service_content_guards_api_client.create(
        service_feature_content_guard=ServiceFeatureContentGuard(
            name="test",
            header_name=CONTENT_GUARD_HEADER_NAME,
            features=CONTENT_GUARD_FEATURES,
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

    headers = {CONTENT_GUARD_HEADER_NAME: CONTENT_GUARD_HEADER_VALUE}
    url = distribution.base_url
    response = requests.get(url=url, headers=headers)

    print(response.status_code)
    print(response.text)
    print(response.content)
    assert response.status_code == 200

    features_available = {
        feature["name"] for feature in response.json() if feature["isEntitled"] is True
    }
    assert features_available == set(CONTENT_GUARD_FEATURES)

    # assert list of features from response is equal to expected
