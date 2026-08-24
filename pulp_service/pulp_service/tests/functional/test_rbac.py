import pytest


@pytest.mark.parallel
class TestRBACAccessPoliciesRegistered:
    """Verify that RBAC access policies, roles, and permissions are correctly registered."""

    def test_vuln_report_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="vuln_report_service")
        assert policies.count == 1
        policy = policies.results[0]
        assert policy.statements
        actions = {a for s in policy.statements for a in s["action"]}
        assert {"list", "create", "retrieve", "destroy"} <= actions
        assert policy.creation_hooks is not None
        assert policy.queryset_scoping is not None

    def test_pypi_yank_monitor_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="pypi_yank_monitor")
        assert policies.count == 1
        policy = policies.results[0]
        assert policy.statements
        actions = {a for s in policy.statements for a in s["action"]}
        assert {"list", "create", "retrieve", "destroy", "check", "report"} <= actions
        assert policy.creation_hooks is not None
        assert policy.queryset_scoping is not None

    def test_feature_content_guard_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="contentguards/service/feature")
        assert policies.count == 1
        policy = policies.results[0]
        assert policy.statements
        actions = {a for s in policy.statements for a in s["action"]}
        assert {"list", "create", "retrieve", "update", "partial_update", "destroy"} <= actions
        assert policy.creation_hooks is not None
        assert policy.queryset_scoping is not None

    def test_envvar_header_content_guard_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="contentguards/service/envvar_header")
        assert policies.count == 1
        policy = policies.results[0]
        assert policy.statements
        actions = {a for s in policy.statements for a in s["action"]}
        assert {"list", "create", "retrieve", "update", "partial_update", "destroy"} <= actions
        assert policy.creation_hooks is not None
        assert policy.queryset_scoping is not None

    def test_domain_create_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="domains/create")
        assert policies.count == 1

    def test_domain_migrate_access_policy_exists(self, pulpcore_bindings):
        policies = pulpcore_bindings.AccessPoliciesApi.list(viewset_name="domains/migrate")
        assert policies.count == 1
        policy = policies.results[0]
        actions = [s["action"] for s in policy.statements]
        assert ["create"] in actions


@pytest.mark.parallel
class TestRBACDeniesWithoutAuth:
    """Verify that unauthenticated users are denied."""

    def test_list_vuln_reports_denied_without_auth(self, service_bindings, anonymous_user):
        with anonymous_user:
            with pytest.raises(service_bindings.module.ApiException) as exc:
                service_bindings.VulnReportServiceApi.list()
            assert exc.value.status == 401

    def test_list_pypi_monitors_denied_without_auth(self, service_bindings, anonymous_user):
        with anonymous_user:
            with pytest.raises(service_bindings.module.ApiException) as exc:
                service_bindings.PypiYankMonitorApi.list()
            assert exc.value.status == 401


@pytest.mark.parallel
class TestRBACAdminAccess:
    """Verify that admin users can perform RBAC-gated operations (superuser bypasses both layers)."""

    def test_admin_can_list_vuln_reports(self, vuln_report_service_api):
        result = vuln_report_service_api.list()
        assert result.count >= 0

    def test_admin_can_list_pypi_monitors(self, pypi_yank_monitor_api):
        result = pypi_yank_monitor_api.list()
        assert result.count >= 0

    def test_admin_can_list_content_guards(self, service_content_guards_api_client):
        result = service_content_guards_api_client.list()
        assert result.count >= 0


@pytest.mark.parallel
class TestServiceDomainRoles:
    """Verify that service.domain_admin and service.domain_viewer roles
    are created by post_migrate
    """

    def test_domain_admin_role_exists(self, pulpcore_bindings):
        roles = pulpcore_bindings.RolesApi.list(name="service.domain_admin")
        assert roles.count == 1
        role = roles.results[0]
        assert role.locked is False

    def test_domain_admin_has_permissions(self, pulpcore_bindings):
        roles = pulpcore_bindings.RolesApi.list(name="service.domain_admin")
        role = roles.results[0]
        assert len(role.permissions) > 0

    def test_domain_viewer_role_exists(self, pulpcore_bindings):
        roles = pulpcore_bindings.RolesApi.list(name="service.domain_viewer")
        assert roles.count == 1
        role = roles.results[0]
        assert role.locked is False

    def test_domain_viewer_has_only_view_permissions(self, pulpcore_bindings):
        roles = pulpcore_bindings.RolesApi.list(name="service.domain_viewer")
        role = roles.results[0]
        assert len(role.permissions) > 0
        for perm in role.permissions:
            codename = perm.split(".", 1)[1] if "." in perm else perm
            assert codename.startswith("view"), f"Non-view permission found in viewer role: {perm}"

    def test_domain_viewer_is_subset_of_admin(self, pulpcore_bindings):
        admin_roles = pulpcore_bindings.RolesApi.list(name="service.domain_admin")
        viewer_roles = pulpcore_bindings.RolesApi.list(name="service.domain_viewer")
        admin_perms = set(admin_roles.results[0].permissions)
        viewer_perms = set(viewer_roles.results[0].permissions)
        assert viewer_perms <= admin_perms, f"Viewer has permissions not in admin: {viewer_perms - admin_perms}"
