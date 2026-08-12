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
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401

        post_migrate.connect(
            _populate_domain_view_access_policies,
            sender=self,
            dispatch_uid="populate_domain_view_access_policies",
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
