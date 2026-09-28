"""Background task + ORM queries for the DomainOrg RBAC access report.

Mirrors ``domainorg_backfill_report``: the same query/classify logic backs both the management
command and a dispatched task that writes the JSON report to a pulpcore Artifact attached to the
running Task via a ProfileArtifact, so an admin can download it through the task's
``profile_artifacts`` action.

For each DomainOrg row this reports, per (principal, domain), whether the principal holds the
roles needed to GET/PUSH content. Effective access for a *user* principal unions the user's own
UserRole rows with the GroupRole rows of every group the user belongs to (so a user who is admin
only via their ``rh-org-<org_id>`` group reads as FULL, not falsely locked-out).

Read-only: it queries roles and only writes the report Artifact, never role or DomainOrg rows.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError

from pulpcore.app.models import Group, ProfileArtifact
from pulpcore.app.models.role import GroupRole, UserRole
from pulpcore.plugin.models import Artifact, Task

from pulp_service.app.constants import ORG_GROUP_PREFIX
from pulp_service.app.domainorg_backfill import derive_org_id
from pulp_service.app.domainorg_rbac_report import (
    REASON_OK,
    RbacStatus,
    classify_reason,
    classify_tier,
    format_json,
)
from pulp_service.app.models import DomainOrg

PROFILE_ARTIFACT_NAME = "domainorg_rbac_report"


def _domain_content_type():
    return ContentType.objects.get(app_label="core", model="domain")


def _group_role_names(group, domain, domain_ct):
    """(domain-scoped role names, object-level-on-Domain role names) the group holds."""
    scoped = GroupRole.objects.filter(group=group, domain=domain).values_list("role__name", flat=True)
    objlevel = GroupRole.objects.filter(group=group, content_type=domain_ct, object_id=str(domain.pk)).values_list(
        "role__name", flat=True
    )
    return set(scoped), set(objlevel)


def _user_role_names(user, domain, domain_ct):
    """Effective role names for a user: own UserRoles unioned with their groups' GroupRoles."""
    group_ids = list(user.groups.values_list("pk", flat=True))
    scoped = set(UserRole.objects.filter(user=user, domain=domain).values_list("role__name", flat=True))
    objlevel = set(
        UserRole.objects.filter(user=user, content_type=domain_ct, object_id=str(domain.pk)).values_list(
            "role__name", flat=True
        )
    )
    if group_ids:
        scoped |= set(
            GroupRole.objects.filter(group_id__in=group_ids, domain=domain).values_list("role__name", flat=True)
        )
        objlevel |= set(
            GroupRole.objects.filter(
                group_id__in=group_ids, content_type=domain_ct, object_id=str(domain.pk)
            ).values_list("role__name", flat=True)
        )
    return scoped, objlevel


def _principals(domain_org):
    """The principals expected to own a DomainOrg's domains: its user, its team group, and its
    derived ``rh-org-<org_id>`` group. Yields (principal_type, principal_name, source, kind, obj)
    where kind is "user"/"group"; a None obj marks an expected-but-missing principal (locked out).
    """
    if domain_org.user_id is not None:
        user = domain_org.user
        yield ("user", user.username, "row-user", "user", user)
    if domain_org.group_id is not None:
        group = domain_org.group
        yield ("group", group.name, "row-group", "group", group)
    # Use derive_org_id (not normalize_org_id) so a null/sentinel org_id row -- the "locked out"
    # calunga shape this report exists to surface -- still resolves its rh-org-<org_id> group from
    # the team members, matching migration 0022's derivation. A None obj (group never created)
    # marks the org group as expected-but-missing so it is reported NONE, not silently omitted.
    org_id = derive_org_id(domain_org)
    if org_id is not None:
        name = f"{ORG_GROUP_PREFIX}{org_id}"
        group = Group.objects.filter(name=name).first()
        yield ("group", name, "derived-org-group", "group", group)


def _status_for(domain_org, domain, principal, domain_ct):
    principal_type, principal_name, source, kind, obj = principal
    if obj is None:
        # Expected principal that does not exist (e.g. rh-org-<org_id> group never created).
        tier, can_get, can_push, has_obj, roles = "NONE", False, False, False, []
        reason = classify_reason(tier, has_principal=False)
    else:
        if kind == "user":
            scoped, objlevel = _user_role_names(obj, domain, domain_ct)
        else:
            scoped, objlevel = _group_role_names(obj, domain, domain_ct)
        tier, can_get, can_push, has_obj = classify_tier(scoped, objlevel)
        roles = sorted(scoped | objlevel)
        reason = classify_reason(tier)

    return RbacStatus(
        domain_org_pk=domain_org.pk,
        domain_name=domain.name,
        principal_type=principal_type,
        principal_name=principal_name,
        principal_source=source,
        tier=tier,
        can_get=can_get,
        can_push=can_push,
        has_domain_object_role=has_obj,
        roles=roles,
        flagged=reason != REASON_OK,
        reason=reason,
    )


def report_domain_orgs(group=None, user=None, org_id=None):
    """DomainOrg rows owning at least one domain, optionally filtered by team group name,
    username, or org_id."""
    qs = (
        DomainOrg.objects.filter(domains__isnull=False)
        .distinct()
        .select_related("user", "group")
        .prefetch_related("domains", "user__groups", "group__user_set")
    )
    if org_id:
        qs = qs.filter(org_id=org_id)
    if group:
        qs = qs.filter(group__name=group)
    if user:
        qs = qs.filter(user__username=user)
    return qs


def rbac_statuses(group=None, user=None, org_id=None):
    """One RbacStatus per (principal, domain) across the matched DomainOrg rows."""
    # ponytail: 2-4 role queries per (principal, domain); fine for an occasional admin diagnostic.
    # If the whole-fleet report gets slow, batch the GroupRole/UserRole rows for the domain set once.
    domain_ct = _domain_content_type()
    statuses = []
    for domain_org in report_domain_orgs(group=group, user=user, org_id=org_id):
        principals = list(_principals(domain_org))
        for domain in domain_org.domains.all():
            if not principals:
                statuses.append(
                    RbacStatus(
                        domain_org_pk=domain_org.pk,
                        domain_name=domain.name,
                        principal_type="none",
                        principal_name=None,
                        principal_source="unresolved",
                        tier="NONE",
                        can_get=False,
                        can_push=False,
                        has_domain_object_role=False,
                        roles=[],
                        flagged=True,
                        reason=classify_reason("NONE", has_principal=False),
                    )
                )
                continue
            statuses.extend(_status_for(domain_org, domain, p, domain_ct) for p in principals)
    return statuses


def generate_rbac_report():
    """Build the JSON RBAC report and attach it to the current task for download."""
    report = format_json(rbac_statuses())

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
