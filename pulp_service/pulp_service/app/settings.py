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

ADMIN_READONLY_GROUP = "admin-readonly"


DOMAIN_ACCESS_POLICIES = {
    "lightwell": {
        "readonly_group": "Lightwell-ReadOnly",
        "subscription_feature": "lightwell-network",
        "subscription_endpoints": ["/api/v3/content/"],
    },
}

DRF_ACCESS_POLICY = {
    "dynaconf_merge_unique": True,
    "reusable_conditions": ["pulp_service.app.access_conditions"],
}

# Settings-based access policy read by PulpServiceAccessPolicy (which inherits from
# pulpcore's AccessPolicyFromSettings). Each key is a viewset urlpattern. Only takes effect
# under RBAC (when PulpServiceAccessPolicy is the active permission class).
#
# AccessPolicyFromSettings.get_access_policy REPLACES the viewset's DEFAULT_ACCESS_POLICY with
# the entry here -- it does not merge. Any statement a viewset needs but this entry omits is
# silently dropped, so a typed viewset's override must re-declare every action it needs.

# Generic read-only /content/ list-all endpoint (pulpcore ListContentViewSet). Gating list on
# the domain-scoped core.view_content permission and dropping queryset_scoping lets a domain
# member see all content in their domain (including orphan content they pushed) while
# non-members get 403. The viewset is read-only, so a list-only override drops nothing.
_CONTENT_LIST_POLICY = {
    "statements": [
        {
            "action": ["list"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": "has_domain_perms:core.view_content",
        },
    ],
    "queryset_scoping": None,
}

# Typed FileContentViewSet (content/file/files). Because get_access_policy REPLACES the default,
# this reproduces pulp_file's FileContentViewSet.DEFAULT_ACCESS_POLICY create/upload/label
# statements verbatim -- sharing the list-only policy above silently dropped them and 403'd POST
# content/file/files under RBAC (#1535). The list/retrieve gating on core.view_content plus
# queryset_scoping=None layers the orphan-content read goal on top: base.py has already scoped
# the queryset to request.pulp_domain, so None stays within the caller's domain.
_FILE_CONTENT_POLICY = {
    "statements": [
        {
            "action": ["list", "retrieve"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": "has_domain_perms:core.view_content",
        },
        {
            "action": ["create"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": [
                "has_required_repo_perms_on_upload:file.modify_filerepository",
                "has_required_repo_perms_on_upload:file.view_filerepository",
                "has_upload_param_model_or_domain_or_obj_perms:core.change_upload",
            ],
        },
        {
            "action": ["set_label", "unset_label"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": ["has_model_or_domain_perms:core.manage_content_labels"],
        },
        {
            "action": ["upload"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": ["has_model_or_domain_perms:file.upload_files"],
        },
    ],
    "queryset_scoping": None,
}

# pulpcore ArtifactViewSet.DEFAULT_ACCESS_POLICY is admin-only; pulp-service opens read+create
# to domain admins. Artifact is domain-scoped (pulp_domain FK), so has_model_or_domain_perms
# resolves against the request domain. destroy is intentionally omitted (stays admin-only,
# pulpcore treats artifact deletion as risky).
_ARTIFACT_POLICY = {
    "statements": [
        {
            "action": ["list", "retrieve"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": "has_model_or_domain_perms:core.view_artifact",
        },
        {
            "action": ["create"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": "has_model_or_domain_perms:core.add_artifact",
        },
    ],
}

ACCESS_POLICIES = {
    "content": _CONTENT_LIST_POLICY,
    "content/file/files": _FILE_CONTENT_POLICY,
    "artifacts": _ARTIFACT_POLICY,
}
