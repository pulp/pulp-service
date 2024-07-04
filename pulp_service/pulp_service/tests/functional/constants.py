"""Constants for Pulp Service plugin tests."""

from urllib.parse import urljoin

from pulp_smash.constants import PULP_FIXTURES_BASE_URL
from pulp_smash.pulp3.constants import (
    BASE_DISTRIBUTION_PATH,
    BASE_PUBLICATION_PATH,
    BASE_REMOTE_PATH,
    BASE_REPO_PATH,
    BASE_CONTENT_PATH,
)

# FIXME: list any download policies supported by your plugin type here.
# If your plugin supports all download policies, you can import this
# from pulp_smash.pulp3.constants instead.
# DOWNLOAD_POLICIES = ["immediate", "streamed", "on_demand"]
DOWNLOAD_POLICIES = ["immediate"]

# FIXME: replace 'unit' with your own content type names, and duplicate as necessary for each type
SERVICE_CONTENT_NAME = "service.unit"

# FIXME: replace 'unit' with your own content type names, and duplicate as necessary for each type
SERVICE_CONTENT_PATH = urljoin(BASE_CONTENT_PATH, "service/units/")

SERVICE_REMOTE_PATH = urljoin(BASE_REMOTE_PATH, "service/service/")

SERVICE_REPO_PATH = urljoin(BASE_REPO_PATH, "service/service/")

SERVICE_PUBLICATION_PATH = urljoin(BASE_PUBLICATION_PATH, "service/service/")

SERVICE_DISTRIBUTION_PATH = urljoin(BASE_DISTRIBUTION_PATH, "service/service/")

# FIXME: replace this with your own fixture repository URL and metadata
SERVICE_FIXTURE_URL = urljoin(PULP_FIXTURES_BASE_URL, "service/")
"""The URL to a service repository."""

# FIXME: replace this with the actual number of content units in your test fixture
SERVICE_FIXTURE_COUNT = 3
"""The number of content units available at :data:`SERVICE_FIXTURE_URL`."""

SERVICE_FIXTURE_SUMMARY = {SERVICE_CONTENT_NAME: SERVICE_FIXTURE_COUNT}
"""The desired content summary after syncing :data:`SERVICE_FIXTURE_URL`."""

# FIXME: replace this with the location of one specific content unit of your choosing
SERVICE_URL = urljoin(SERVICE_FIXTURE_URL, "")
"""The URL to an service file at :data:`SERVICE_FIXTURE_URL`."""

# FIXME: replace this with your own fixture repository URL and metadata
SERVICE_INVALID_FIXTURE_URL = urljoin(PULP_FIXTURES_BASE_URL, "service-invalid/")
"""The URL to an invalid service repository."""

# FIXME: replace this with your own fixture repository URL and metadata
SERVICE_LARGE_FIXTURE_URL = urljoin(PULP_FIXTURES_BASE_URL, "service_large/")
"""The URL to a service repository containing a large number of content units."""

# FIXME: replace this with the actual number of content units in your test fixture
SERVICE_LARGE_FIXTURE_COUNT = 25
"""The number of content units available at :data:`SERVICE_LARGE_FIXTURE_URL`."""
