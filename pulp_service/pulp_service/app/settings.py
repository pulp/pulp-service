"""
Check `Plugin Writer's Guide`_ for more details.

.. _Plugin Writer's Guide:
    https://docs.pulpproject.org/pulpcore/plugins/plugin-writer/index.html
"""

FEATURE_SERVICE_API_URL = "https://feature.stage.api.redhat.com/features/v2/featureStatus"
FEATURE_SERVICE_API_CERT_PATH = ""
# Connect/read timeouts (seconds) for calls made to the Features Service API. This call
# happens synchronously on the content app's shared sync-to-async worker thread, so it must
# be bounded -- otherwise a slow/unavailable Features Service stalls every content request
# being served by that worker, not just the guarded one.
# NOTE: kept as two scalars rather than a single (connect, read) tuple -- Pulp's settings
# pipeline round-trips through JSON/YAML in places, which silently turns tuples into lists,
# and `requests` only splits a *tuple* into (connect, read); a list is treated as a single
# malformed timeout value and raises a ValueError.
FEATURE_SERVICE_API_CONNECT_TIMEOUT = 2
FEATURE_SERVICE_API_READ_TIMEOUT = 5
AUTHENTICATION_HEADER_DEBUG = False
INSTALLED_APPS = "@merge django.contrib.admin.apps.SimpleAdminConfig,hijack,hijack.contrib.admin"
TEST_TASK_INGESTION = False
LOGIN_REDIRECT_URL = "/api/pulp-mgmt/"
LOGIN_URL = "/api/pulp-mgmt/login/"

# Django Hijack settings
HIJACK_LOGIN_REDIRECT_URL = "/api/pulp-mgmt/"
HIJACK_LOGOUT_REDIRECT_URL = "/api/pulp-mgmt/"
HIJACK_ALLOW_GET_REQUESTS = True

# RDS Test endpoints setting
RDS_CONNECTION_TESTS_ENABLED = False


DOMAIN_ACCESS_POLICIES = {
    "lightwell": {
        "readonly_group": "Lightwell-ReadOnly",
        "subscription_feature": "lightwell-network",
        "subscription_endpoints": ["/api/v3/content/"],
    },
}
