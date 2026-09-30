"""Startup access-policy overrides for pulpcore viewsets that settings can't reach.

OrphansCleanupViewset is a plain DRF ``ViewSet`` with no ``urlpattern()`` method, so
``AccessPolicyFromSettings.get_access_policy`` cannot find it in ``settings.ACCESS_POLICIES``
(``get_view_urlpattern`` raises ``AttributeError`` and it falls back to the viewset's
``DEFAULT_ACCESS_POLICY``, which defaults to the admin-only module policy). Setting
``DEFAULT_ACCESS_POLICY`` on the class is the only way to open it to non-superusers.
"""

from pulpcore.app.viewsets.orphans import OrphansCleanupViewset

# Orphan cleanup deletes not-in-a-repo content *and* artifacts scoped to request.pulp_domain, so
# gate on both domain-scoped destructive permissions the operation exercises (service.domain_admin
# holds core.delete_content and core.delete_artifact). Requiring both keeps the gate aligned with
# what the op deletes. has_domain_perms needs no view model -- it checks request.pulp_domain directly.
ORPHANS_CLEANUP_ACCESS_POLICY = {
    "statements": [
        {
            "action": ["cleanup"],
            "principal": "authenticated",
            "effect": "allow",
            "condition": [
                "has_domain_perms:core.delete_content",
                "has_domain_perms:core.delete_artifact",
            ],
        },
    ],
}


def apply_pulpcore_access_policy_overrides():
    """Idempotent: safe to call once per process at plugin startup."""
    OrphansCleanupViewset.DEFAULT_ACCESS_POLICY = ORPHANS_CLEANUP_ACCESS_POLICY
