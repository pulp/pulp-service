import gc
import logging

from pulpcore.app.models import RepositoryContent, RepositoryVersion
from pulpcore.plugin.models import ProgressReport
from pulpcore.plugin.util import get_domain

log = logging.getLogger(__name__)

GC_INTERVAL = 100


def repair_7465(dry_run=False):
    """Populate missing content_ids cache on repository versions."""
    domain = get_domain()
    log.info('Performing datarepair for issue #7465 for domain "%s"', domain.name)

    versions_qs = RepositoryVersion.objects.filter(
        repository__pulp_domain=domain,
        content_ids=None,
    )
    total = versions_qs.count()

    if not total:
        log.info('Data repair for issue #7465: no missing content_ids in domain "%s"', domain.name)
        return

    number_checked = 0
    number_fixed = 0

    with (
        ProgressReport(
            message="Repository versions checked",
            code="repair.7465.versions_checked",
            total=total,
        ) as checked_progress,
        ProgressReport(
            message="Repository versions fixed",
            code="repair.7465.versions_fixed",
        ) as fixed_progress,
    ):
        for rv in versions_qs.only("pk", "repository_id", "number").iterator(chunk_size=500):
            if not dry_run:
                content_ids = list(
                    RepositoryContent.objects.filter(
                        repository_id=rv.repository_id,
                        version_added__number__lte=rv.number,
                    )
                    .exclude(version_removed__number__lte=rv.number)
                    .values_list("content_id", flat=True)
                )
                RepositoryVersion.objects.filter(pk=rv.pk).update(content_ids=content_ids)
                del content_ids
                number_fixed += 1
                fixed_progress.increment()

            number_checked += 1
            checked_progress.increment()

            if number_checked % GC_INTERVAL == 0:
                gc.collect()

        fixed_progress.total = number_fixed

    if dry_run:
        log.info(
            'Data repair for issue #7465 dry run: %d versions need fixing in domain "%s"',
            number_checked,
            domain.name,
        )
    else:
        log.info(
            'Data repair for issue #7465: %d versions fixed in domain "%s"',
            number_fixed,
            domain.name,
        )
