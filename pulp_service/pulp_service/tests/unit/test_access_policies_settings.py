"""Regression: RBAC (#1535) enforced per-viewset access policies. AccessPolicyFromSettings
REPLACES the viewset default, so a settings entry that omits an action drops it. These pin
the actions each overridden endpoint must keep for non-superuser domain admins."""

from pulp_service.app.settings import ACCESS_POLICIES


def _actions(policy):
    actions = set()
    for statement in policy["statements"]:
        action = statement["action"]
        actions.update([action] if isinstance(action, str) else action)
    return actions


def test_file_content_policy_keeps_write_actions():
    # The original defect: content/file/files shared a list-only policy, dropping create/upload.
    actions = _actions(ACCESS_POLICIES["content/file/files"])
    assert {"create", "upload", "list", "retrieve"} <= actions
    assert {"set_label", "unset_label"} <= actions


def test_file_content_policy_drops_repo_queryset_scoping():
    # orphan-content read goal: not-in-a-repo content must be visible to domain members.
    assert ACCESS_POLICIES["content/file/files"]["queryset_scoping"] is None


def test_artifacts_policy_allows_domain_admin_read_and_create():
    actions = _actions(ACCESS_POLICIES["artifacts"])
    assert {"list", "retrieve", "create"} <= actions


def test_artifacts_policy_is_not_wildcard_and_omits_destroy():
    # pulpcore's default is admin-only; opening it must stay narrow -- reads + create only.
    for statement in ACCESS_POLICIES["artifacts"]["statements"]:
        action = statement["action"]
        assert action != "*"
        assert "destroy" not in ([action] if isinstance(action, str) else action)


def test_orphans_cleanup_override_allows_domain_admin():
    # orphans/cleanup is a urlpattern-less ViewSet: a settings.ACCESS_POLICIES entry is never
    # read (get_view_urlpattern raises), so the fix must live on the viewset class itself.
    from pulpcore.app.viewsets.orphans import OrphansCleanupViewset

    from pulp_service.app.pulpcore_access_policy_overrides import (
        apply_pulpcore_access_policy_overrides,
    )

    apply_pulpcore_access_policy_overrides()
    policy = OrphansCleanupViewset.DEFAULT_ACCESS_POLICY
    actions = {
        a for s in policy["statements"] for a in ([s["action"]] if isinstance(s["action"], str) else s["action"])
    }
    assert "cleanup" in actions
    assert all(s.get("principal") != "admin" for s in policy["statements"])
