"""Background task to remediate DomainOrg rows migration 0022 could not backfill.

Given an explicit {domain_org_pk, org_id} map (the org_id a human supplies for rows the
migration could not derive), this sets the org_id, ensures the rh-org-<org_id> group exists,
and assigns the domain roles on every domain the row owns. Roles are assigned with get_or_create
on the GroupRole rows -- the exact shape migration 0019/0022 write via _assign_pair -- so the
operation is idempotent (safe to re-run, and safe on a row that already holds one of the roles).
This is deliberately NOT signals._assign_domain_roles: that calls pulpcore's assign_role, a
.create() that raises on an already-present role and would crash the batch.

Writes: it mutates DomainOrg.org_id, creates Groups, and assigns roles. dry_run=True validates
and reports the intended actions without any write. Per-row transactions keep a failing row from
poisoning the batch. The JSON result is attached to the running Task as a ProfileArtifact.
"""

import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

from pulpcore.app.models import Group, ProfileArtifact
from pulpcore.app.models.role import GroupRole, Role
from pulpcore.plugin.models import Artifact, Task

from pulp_service.app.constants import ORG_GROUP_PREFIX
from pulp_service.app.domainorg_backfill import normalize_org_id
from pulp_service.app.models import DomainOrg

PROFILE_ARTIFACT_NAME = "domainorg_remediate_roles"

# Roles assigned per domain, matching migration 0019/0022 exactly.
ROLE_DOMAIN_OWNER = "core.domain_owner"  # object-level on the Domain row: manage the domain object
ROLE_DOMAIN_ADMIN = "service.domain_admin"  # domain-scoped: manage (GET+PUSH) objects inside the domain


def _record(domain_org_pk, org_id, group, domains, action, reason):
    return {
        "domain_org_pk": domain_org_pk,
        "org_id": org_id,
        "group": group,
        "domains": domains,
        "action": action,
        "reason": reason,
    }


def _assign_org_group_roles(org_group, domain, domain_ct, owner_role, admin_role):
    """Idempotently create the two GroupRole rows for org_group on domain, byte-identical to
    migration 0019's _assign_pair (object-level owner + domain-scoped admin)."""
    GroupRole.objects.get_or_create(
        role=owner_role, group=org_group, content_type=domain_ct, object_id=str(domain.pk), domain=None
    )
    GroupRole.objects.get_or_create(role=admin_role, group=org_group, content_type=None, object_id=None, domain=domain)


def remediate_one(domain_org_pk, org_id, dry_run):
    """Set org_id + assign roles for one DomainOrg row. Returns a result record; never raises for
    the expected skip cases (unknown pk, blank/sentinel org_id)."""
    normalized = normalize_org_id(org_id)
    if not normalized:
        return _record(domain_org_pk, org_id, None, [], "skipped", "invalid-org-id")

    try:
        domain_org = DomainOrg.objects.prefetch_related("domains").get(pk=domain_org_pk)
    except DomainOrg.DoesNotExist:
        return _record(domain_org_pk, normalized, None, [], "skipped", "not-found")

    group_name = f"{ORG_GROUP_PREFIX}{normalized}"
    domain_names = sorted(d.name for d in domain_org.domains.all())

    if dry_run:
        return _record(domain_org_pk, normalized, group_name, domain_names, "would-assign", "ok")

    domain_ct = ContentType.objects.get(app_label="core", model="domain")
    owner_role = Role.objects.get(name=ROLE_DOMAIN_OWNER)
    admin_role = Role.objects.get(name=ROLE_DOMAIN_ADMIN)
    with transaction.atomic():
        if domain_org.org_id != normalized:
            domain_org.org_id = normalized
            domain_org.save(update_fields=["org_id"])
        org_group, _ = Group.objects.get_or_create(name=group_name)
        for domain in domain_org.domains.all():
            _assign_org_group_roles(org_group, domain, domain_ct, owner_role, admin_role)

    return _record(domain_org_pk, normalized, group_name, domain_names, "assigned", "ok")


def remediate(assignments, dry_run):
    """Apply remediate_one over the assignment list and summarize."""
    results = [remediate_one(a["domain_org_pk"], a["org_id"], dry_run) for a in assignments]
    summary = {
        "assigned": sum(1 for r in results if r["action"] == "assigned"),
        "would_assign": sum(1 for r in results if r["action"] == "would-assign"),
        "skipped": sum(1 for r in results if r["action"] == "skipped"),
    }
    return {"dry_run": dry_run, "results": results, "summary": summary}


def generate_remediation_report(assignments, dry_run=False):
    """Dispatched task: remediate and attach the JSON result to the current task for download."""
    report = json.dumps(remediate(assignments, dry_run), indent=2, sort_keys=True)

    task = Task.current()
    with tempfile.TemporaryDirectory(dir=settings.WORKING_DIRECTORY) as temp_dir:
        report_path = Path(temp_dir) / f"{PROFILE_ARTIFACT_NAME}.json"
        report_path.write_text(report)
        artifact = Artifact.init_and_validate(str(report_path))
        try:
            artifact.save()
        except IntegrityError:
            artifact = Artifact.objects.get(sha256=artifact.sha256)
        ProfileArtifact.objects.get_or_create(artifact=artifact, task=task, name=PROFILE_ARTIFACT_NAME)
