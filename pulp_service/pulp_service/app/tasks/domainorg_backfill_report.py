"""Background task: generate the DomainOrg backfill report as a downloadable artifact.

Mirrors the ``domainorg_backfill_report`` management command, but runs as a dispatched task
and writes the JSON report to a pulpcore Artifact attached to the running Task via a
ProfileArtifact, so an admin can download it through the task's ``profile_artifacts`` action.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q

from pulpcore.app.models import ProfileArtifact
from pulpcore.plugin.models import Artifact, Task

from pulp_service.app.domainorg_backfill import MISSING_ORG_SENTINELS, classify, format_json
from pulp_service.app.models import DomainOrg

PROFILE_ARTIFACT_NAME = "domainorg_backfill_report"


def missing_org_domain_orgs():
    """DomainOrg rows that own at least one domain and have a missing/blank/sentinel org_id.

    Mirrors normalize_org_id's notion of "missing": SQL NULL, the sentinel strings, and
    whitespace-only or whitespace-wrapped sentinel values (normalize_org_id strips before
    matching, e.g. " null " -> None). Shared with the management command so the two cannot
    drift from migration 0022's definition of "missing".
    """
    missing = (
        Q(org_id__isnull=True)
        | Q(org_id__in=list(MISSING_ORG_SENTINELS))
        | Q(org_id__regex=r"^\s+$")
        | Q(org_id__regex=r"^\s*(null|None)\s*$")
    )
    return (
        DomainOrg.objects.filter(missing)
        .filter(domains__isnull=False)
        .distinct()
        .prefetch_related("domains", "group__user_set")
    )


def generate_backfill_report():
    """Build the JSON backfill report and attach it to the current task for download.

    Read-only with respect to DomainOrg: it only writes the report Artifact and its
    ProfileArtifact link, never DomainOrg.org_id.
    """
    statuses = [classify(do) for do in missing_org_domain_orgs()]
    report = format_json(statuses)

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
