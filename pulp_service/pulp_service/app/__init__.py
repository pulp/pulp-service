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
    """Create/update service.domain_admin and service.domain_viewer roles with plugin permissions.

    Connected to every plugin's post_migrate. A plugin's permissions are created (by
    django.contrib.auth's create_permissions) when that plugin's post_migrate fires, and
    post_migrate is emitted per app in INSTALLED_APPS order. Gating this to only the service
    app's post_migrate would miss any plugin that emits after service on a fresh single
    migrate (the roles self-heal on the next migrate). Running on every plugin's post_migrate
    instead means the last plugin to emit repopulates the roles with every plugin's
    permissions present, so a fresh single migrate yields complete roles regardless of app
    order. The permissions.set() calls run in one transaction so the intermediate
    DELETE+INSERT is never visible to live API pods during rolling upgrades (PostgreSQL
    READ COMMITTED hides it).
    """
    try:
        Role = apps.get_model("core", "Role")
        Permission = apps.get_model("auth", "Permission")
    except LookupError:
        return

    from django.apps import apps as django_apps
    from django.db import transaction

    from pulpcore.plugin import PulpPluginAppConfig

    plugin_labels = {ac.label for ac in django_apps.get_app_configs() if isinstance(ac, PulpPluginAppConfig)}
    all_permissions = Permission.objects.filter(content_type__app_label__in=plugin_labels)
    view_permissions = all_permissions.filter(codename__startswith="view")

    with transaction.atomic():
        admin_role, _ = Role.objects.update_or_create(
            name="service.domain_admin",
            defaults={"locked": False, "description": "Admin role for all domain-level plugin permissions."},
        )
        admin_role.permissions.set(all_permissions)

        viewer_role, _ = Role.objects.update_or_create(
            name="service.domain_viewer",
            defaults={"locked": False, "description": "Viewer role for all domain-level view permissions."},
        )
        viewer_role.permissions.set(view_permissions)
