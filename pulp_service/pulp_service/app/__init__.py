from pulpcore.plugin import PulpPluginAppConfig


class PulpServicePluginAppConfig(PulpPluginAppConfig):
    """Entry point for the service plugin."""

    name = "pulp_service.app"
    label = "service"
    version = "0.1.0"
    python_package_name = "pulp_service"
    domain_compatible = True

    def ready(self):
        super().ready()
        from django.apps import apps
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401

        post_migrate.connect(
            _populate_domain_view_access_policies,
            sender=self,
            dispatch_uid="populate_domain_view_access_policies",
        )

        for app_config in apps.get_app_configs():
            if isinstance(app_config, PulpPluginAppConfig):
                post_migrate.connect(
                    _populate_service_roles,
                    sender=app_config,
                    dispatch_uid=f"populate_service_roles_{app_config.label}",
                )


def _populate_domain_view_access_policies(sender, apps, **kwargs):  # noqa: ARG001
    from pulp_service.app.viewsets import CreateDomainView, MigrateDomainView

    try:
        AccessPolicy = apps.get_model("core", "AccessPolicy")
    except LookupError:
        return

    for view_cls in (CreateDomainView, MigrateDomainView):
        access_policy = getattr(view_cls, "DEFAULT_ACCESS_POLICY", None)
        if access_policy is None:
            continue
        viewset_name = view_cls.urlpattern()
        db_access_policy, created = AccessPolicy.objects.get_or_create(
            viewset_name=viewset_name, defaults=access_policy
        )
        if not created and not db_access_policy.customized:
            for key, value in access_policy.items():
                setattr(db_access_policy, key, value)
            db_access_policy.save()


def _populate_service_roles(sender, apps, **kwargs):  # noqa: ARG001
    """Create/update service.domain_admin and service.domain_viewer roles with plugin permissions."""
    # Only run for the service app's own migration. pulp_service depends on all plugins, so by the
    # time it migrates every plugin's permissions exist. Running once (instead of once per plugin)
    # avoids repeated permissions.set() DELETE+INSERT windows that cause 403s during rolling upgrades.
    if sender.label != "service":
        return

    try:
        Role = apps.get_model("core", "Role")
        Permission = apps.get_model("auth", "Permission")
    except LookupError:
        return

    from django.apps import apps as django_apps

    from pulpcore.plugin import PulpPluginAppConfig

    plugin_labels = {ac.label for ac in django_apps.get_app_configs() if isinstance(ac, PulpPluginAppConfig)}
    all_permissions = Permission.objects.filter(content_type__app_label__in=plugin_labels)

    admin_role, _ = Role.objects.update_or_create(
        name="service.domain_admin",
        defaults={"locked": False, "description": "Admin role for all domain-level plugin permissions."},
    )
    admin_role.permissions.set(all_permissions)

    view_permissions = all_permissions.filter(codename__startswith="view")
    viewer_role, _ = Role.objects.update_or_create(
        name="service.domain_viewer",
        defaults={"locked": False, "description": "Viewer role for all domain-level view permissions."},
    )
    viewer_role.permissions.set(view_permissions)
