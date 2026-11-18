import json
import uuid

import requests

from pulp_npm.tests.functional.constants import NPM_FIXTURE_URL


# @pytest.mark.xfail
def test_artifact_size_header_on_pull_through_cache(
    npm_bindings, npm_remote_factory, npm_distribution_factory, http_get, delete_orphans_pre
):
    """Test that a pull-through distro can be installed from."""
    remote = npm_remote_factory(url=NPM_FIXTURE_URL)
    distro = npm_distribution_factory(remote=remote.pulp_href)
    package = "react"

    package_metadata = json.loads(http_get(f"{distro.base_url}{package}"))
    assert package_metadata["name"] == package

    latest_package_version = package_metadata["dist-tags"]["latest"]
    latest_package_metadata = package_metadata["versions"][latest_package_version]
    package_filename = latest_package_metadata["dist"]["tarball"].removeprefix(NPM_FIXTURE_URL)

    response = requests.get(f"{distro.base_url}{package_filename}")

    assert response.headers.get("X-PULP-ARTIFACT-SIZE")


def test_empty_distribution_returns_404(file_bindings, gen_object_with_cleanup):
    """A distribution with no repository, publication, or remote must 404, not 500.

    Regression test for PULP-2717. Patch 0080 used a bare ``_`` as the throwaway target of
    three tuple unpacks in ``Handler._match_and_stream``, which made ``_`` a function-local
    name and shadowed gettext's ``_``. A distribution with nothing configured reaches the
    "Distribution is not pointing to..." branch without executing any of those unpacks, so
    the gettext call raised ``UnboundLocalError`` and the content app answered 500. In
    production this hit every request for a stale COPR pass-through distribution.
    """
    distro = gen_object_with_cleanup(
        file_bindings.DistributionsFileApi,
        {"name": str(uuid.uuid4()), "base_path": str(uuid.uuid4())},
    )

    response = requests.get(f"{distro.base_url}missing/path", timeout=30)

    assert response.status_code == 404, response.text
    assert "Distribution is not pointing to" in response.reason
