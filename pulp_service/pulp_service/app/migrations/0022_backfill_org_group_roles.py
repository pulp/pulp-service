import logging
from importlib import import_module

from django.db import migrations

from pulp_service.app.constants import ORG_GROUP_PREFIX

_logger = logging.getLogger(__name__)

# Inlined from the former pulp_service.app.domainorg_backfill module (removed with the DomainOrg
# model). A migration must stay self-contained, so the org_id derivation it needs lives here.
MISSING_ORG_SENTINELS = frozenset({"", "null", "None"})


def normalize_org_id(value):
    """Return a real org_id string, or None for NULL / blank / whitespace / sentinel values."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in MISSING_ORG_SENTINELS:
        return None
    return text


def _team_org_ids(domain_org):
    """Distinct org ids among the team group's members, from their rh-org-<org_id> groups."""
    org_ids = set()
    for user in domain_org.group.user_set.all():
        for name in user.groups.filter(name__startswith=ORG_GROUP_PREFIX).values_list("name", flat=True):
            org_ids.add(name[len(ORG_GROUP_PREFIX) :])
    return org_ids


def derive_org_id(domain_org):
    """The org_id this backfill stores: the stored value if real, else the single unambiguous org
    among the team group's members, else None."""
    stored = normalize_org_id(domain_org.org_id)
    if stored:
        return stored
    if domain_org.group_id is None:
        return None
    org_ids = _team_org_ids(domain_org)
    return next(iter(org_ids)) if len(org_ids) == 1 else None

# Reuse migration 0019's role-assignment primitives so the GroupRole rows this backfill
# writes are byte-identical to the ones 0019/0021 write. Migration module names start with
# a digit, so load it by dotted string (same pattern as 0021).
_migration_0019 = import_module("pulp_service.app.migrations.0019_convert_domainorg_to_roles")


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
        org_id = derive_org_id(domain_org)
        if not org_id:
            skipped += 1
            _logger.warning(
                "org-group role backfill: DomainOrg pk=%s has no derivable org_id; skipping.", domain_org.pk
            )
            continue
        if normalize_org_id(domain_org.org_id) != org_id:
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
