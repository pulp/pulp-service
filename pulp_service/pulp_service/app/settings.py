"""
Check `Plugin Writer's Guide`_ for more details.

.. _Plugin Writer's Guide:
    https://docs.pulpproject.org/pulpcore/plugins/plugin-writer/index.html
"""

FEATURE_SERVICE_API_URL = "https://feature.stage.api.redhat.com/features/v1/featureStatus"
FEATURE_SERVICE_API_CERT_PATH = ""
AUTHENTICATION_HEADER_DEBUG = False
INSTALLED_APPS = "@merge django.contrib.admin.apps.SimpleAdminConfig,hijack,hijack.contrib.admin"
TEST_TASK_INGESTION = False
LOGIN_REDIRECT_URL='/api/pulp-mgmt/'
