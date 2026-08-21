import logging

from django.conf import settings
from django.db import migrations

_logger = logging.getLogger(__name__)


def _ensure_service_roles(apps):
    """Create service.domain_admin / service.domain_viewer inline.

    Mirrors pulp_service.app._populate_service_roles. We cannot rely on that
    post_migrate handler here: post_migrate runs only after ALL migrations are
    complete, so roles do not exist while this data migration runs.
    """
    Role = apps.get_model("core", "Role")
    Permission = apps.get_model("auth", "Permission")

    # Intentionally read the LIVE app registry, not the historical `apps` passed in: the
    # permission set granted to these roles must match what the runtime post_migrate handler
    # (_populate_service_roles) grants, which is derived from the installed plugins. Historical
    # migration state exposes no PulpPluginAppConfig instances, so it cannot supply that set.
    from django.apps import apps as django_apps
    from pulpcore.plugin import PulpPluginAppConfig

    plugin_labels = {ac.label for ac in django_apps.get_app_configs() if isinstance(ac, PulpPluginAppConfig)}
    all_permissions = Permission.objects.filter(content_type__app_label__in=plugin_labels)

    admin_role, _ = Role.objects.get_or_create(
        name="service.domain_admin",
        defaults={"locked": False, "description": "Admin role for all domain-level plugin permissions."},
    )
    admin_role.permissions.set(all_permissions)

    viewer_role, _ = Role.objects.get_or_create(
        name="service.domain_viewer",
        defaults={"locked": False, "description": "Viewer role for all domain-level view permissions."},
    )
    viewer_role.permissions.set(all_permissions.filter(codename__startswith="view"))

    return admin_role, viewer_role


def _assign_pair(role_model, entity_kwargs, object_role, scoped_role, domain, domain_ct):
    """Create the object-level + domain-scoped role rows for one entity on one domain.

    Idempotent: get_or_create keys on the full unique_together tuple
    (entity, role, content_type, object_id, domain).
    """
    # Object-level: role asserted ON the domain object itself (view/manage/list domains)
    role_model.objects.get_or_create(
        role=object_role,
        content_type=domain_ct,
        object_id=str(domain.pk),
        domain=None,
        **entity_kwargs,
    )
    # Domain-scoped: role asserted on every object that lives INSIDE the domain
    role_model.objects.get_or_create(
        role=scoped_role,
        content_type=None,
        object_id=None,
        domain=domain,
        **entity_kwargs,
    )


def convert_domainorgs_to_roles(apps, schema_editor):
    DomainOrg = apps.get_model("service", "DomainOrg")
    Role = apps.get_model("core", "Role")
    UserRole = apps.get_model("core", "UserRole")
    GroupRole = apps.get_model("core", "GroupRole")
    Group = apps.get_model("core", "Group")
    Domain = apps.get_model("core", "Domain")
    ContentType = apps.get_model("contenttypes", "ContentType")

    admin_role, viewer_role = _ensure_service_roles(apps)

    # pulpcore locked roles: exist on upgraded DBs. get_or_create keeps the FK
    # safe on a fresh/edge DB. pulpcore's post_migrate populates their permissions after migrate-db.
    domain_owner_role, _ = Role.objects.get_or_create(name="core.domain_owner")
    domain_viewer_role, _ = Role.objects.get_or_create(name="core.domain_viewer")

    # get_or_create (not get): ContentType rows are created in post_migrate and
    # may be absent on a fresh DB. Creating it early is harmless, a no-op for rows that already exist.
    domain_ct, _ = ContentType.objects.get_or_create(app_label="core", model="domain")

    # Convert every DomainOrg entry. iterator(chunk_size=...) streams rows instead of
    # loading them all at once; chunk_size is required for prefetch_related to be observed
    # (Django 4.1+). select_related pulls the user/group FKs in the same query to avoid N+1.
    queryset = DomainOrg.objects.select_related("user", "group").prefetch_related("domains")
    for domain_org in queryset.iterator(chunk_size=500):
        domains = list(domain_org.domains.all())
        if not domains:
            continue

        user = domain_org.user
        group = domain_org.group
        # org_id-only entry (no user or group). Create an rh-org-<org-id> group.
        if user is None and group is None and domain_org.org_id:
            group, _ = Group.objects.get_or_create(name=f"rh-org-{domain_org.org_id}")

        for domain in domains:
            # user-set
            if user is not None:
                _assign_pair(UserRole, {"user": user}, domain_owner_role, admin_role, domain, domain_ct)
            # group-set
            if group is not None:
                _assign_pair(GroupRole, {"group": group}, domain_owner_role, admin_role, domain, domain_ct)

    # readonly groups (Lightwell-ReadOnly) for DOMAIN_ACCESS_POLICIES. Runs once total,
    # independent of DomainOrg rows (must still fire on a DB with no DomainOrg entries).
    # This is a one-time conversion of the CURRENT runtime state: it reflects the policy
    # config present at migration time and does not track later changes to DOMAIN_ACCESS_POLICIES.
    for domain_name, policy in getattr(settings, "DOMAIN_ACCESS_POLICIES", {}).items():
        readonly_group_name = policy.get("readonly_group")
        if not readonly_group_name:
            continue
        # Domain.name is unique, so this matches at most one row. A configured policy may
        # reference a domain that does not exist in this environment (e.g. lightwell not yet
        # provisioned); skip it but log so a typo/misconfig is visible rather than silent.
        domain = Domain.objects.filter(name=domain_name).first()
        if domain is None:
            _logger.warning(
                "DOMAIN_ACCESS_POLICIES references domain %r which does not exist; "
                "skipping readonly role assignment for group %r.",
                domain_name,
                readonly_group_name,
            )
            continue
        group, _ = Group.objects.get_or_create(name=readonly_group_name)
        _assign_pair(GroupRole, {"group": group}, domain_viewer_role, viewer_role, domain, domain_ct)


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0018_add_rbac_permissions"),
    ]

    operations = [migrations.RunPython(convert_domainorgs_to_roles, migrations.RunPython.noop)]
