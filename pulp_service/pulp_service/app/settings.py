"""
Check `Plugin Writer's Guide`_ for more details.

.. _Plugin Writer's Guide:
    https://docs.pulpproject.org/pulpcore/plugins/plugin-writer/index.html
"""

SUBSCRIPTION_API_URL = "https://subscription.stage.api.redhat.com/svcrest/subscription/v5/featureStatus"
SUBSCRIPTION_API_CERT = ""
AUTHENTICATION_HEADER_DEBUG = False
INSTALLED_APPS = "@merge django.contrib.admin.apps.SimpleAdminConfig"
TEST_TASK_INGESTION = False
TEST_TASK_THROUGHPUT = False
