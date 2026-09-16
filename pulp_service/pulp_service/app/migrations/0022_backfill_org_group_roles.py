import logging
from importlib import import_module

from django.db import migrations

from pulp_service.app.constants import ORG_GROUP_PREFIX

_logger = logging.getLogger(__name__)

# Reuse migration 0019's role-assignment primitives so the GroupRole rows this backfill
# writes are byte-identical to the ones 0019/0021 write. Migration module names start with
# a digit, so load it by dotted string (same pattern as 0021).
_migration_0019 = import_module("pulp_service.app.migrations.0019_convert_domainorg_to_roles")


def _derive_org_id(domain_org):
    """Return the org_id whose rh-org-<org_id> group owns this DomainOrg, or None.

    A DomainOrg whose create request carried no identity.internal.org_id has a null
    org_id, so 0019/0021 (gated on `if domain_org.org_id:`) granted the team group its
    roles but skipped the rh-org-<org_id> group -- leaving org members (service accounts
    in rh-org-<org_id> only) with no role on the domain (404/400 under RBAC).

    Use the stored org_id when present; otherwise derive it from the team group's members,
    who are all auto-added to rh-org-<org_id>. Accept the derived value only when every
    member shares exactly one org -- never guess for a mixed-org or empty team.
    """
    if domain_org.org_id:
        return str(domain_org.org_id).strip() or None
    if domain_org.group_id is None:
        return None
    org_ids = set()
    for user in domain_org.group.user_set.all():
        for name in user.groups.filter(name__startswith=ORG_GROUP_PREFIX).values_list("name", flat=True):
            org_ids.add(name[len(ORG_GROUP_PREFIX) :])
    return next(iter(org_ids)) if len(org_ids) == 1 else None


def backfill_org_group_roles(apps, schema_editor):  # noqa: ARG001
    DomainOrg = apps.get_model("service", "DomainOrg")
    Role = apps.get_model("core", "Role")
    GroupRole = apps.get_model("core", "GroupRole")
    Group = apps.get_model("core", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Same role objects 0019 assigns. set_permissions=False leaves their permission
    # membership to the post_migrate handler, matching 0021's rationale.
    admin_role, _viewer = _migration_0019._ensure_service_roles(apps, set_permissions=False)
    domain_owner_role, _ = Role.objects.get_or_create(name="core.domain_owner")
    domain_ct, _ = ContentType.objects.get_or_create(app_label="core", model="domain")

    repaired = skipped = 0
    for domain_org in DomainOrg.objects.prefetch_related("domains", "group__user_set"):
        domains = list(domain_org.domains.all())
        if not domains:
            continue
        org_id = _derive_org_id(domain_org)
        if not org_id:
            skipped += 1
            _logger.warning(
                "org-group role backfill: DomainOrg pk=%s has no derivable org_id; skipping.", domain_org.pk
            )
            continue
        if not domain_org.org_id:
            domain_org.org_id = org_id
            domain_org.save(update_fields=["org_id"])
        org_group, _ = Group.objects.get_or_create(name=f"{ORG_GROUP_PREFIX}{org_id}")
        for domain in domains:
            # Object-level (core.domain_owner) + domain-scoped (service.domain_admin) rows,
            # idempotent via get_or_create; identical shape to 0019's _assign_pair.
            _migration_0019._assign_pair(
                GroupRole, {"group": org_group}, domain_owner_role, admin_role, domain, domain_ct
            )
        repaired += 1

    _logger.info("org-group role backfill: repaired=%s skipped=%s", repaired, skipped)


class Migration(migrations.Migration):
    dependencies = [
        ("service", "0021_backfill_domainorg_roles"),
    ]

    operations = [
        migrations.RunPython(backfill_org_group_roles, migrations.RunPython.noop, elidable=True),
    ]
