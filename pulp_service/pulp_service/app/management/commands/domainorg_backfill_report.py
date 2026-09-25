"""Read-only report of DomainOrg rows migration 0022 will backfill or skip.

Migration 0022 derives org_id for rows that have none, but silently skips rows it cannot
resolve unambiguously (no team group, empty group, no org membership, or members spanning
multiple orgs). Those skipped rows' org members stay scoped out under RBAC until a human sets
org_id. This command enumerates the missing-org_id rows and, using 0022's exact derivation
logic, marks each RESOLVABLE (with the org_id 0022 will store) or UNRESOLVABLE (with a reason),
so operators can review and remediate the skip-set before promoting RBAC.

Read-only: it makes no database writes. Remediation is out of scope.
"""

from django.core.management.base import BaseCommand

from pulp_service.app.domainorg_backfill import classify, format_json, format_table
from pulp_service.app.tasks.domainorg_backfill_report import missing_org_domain_orgs


class Command(BaseCommand):
    help = "Report DomainOrg rows with a missing org_id and whether migration 0022 can backfill them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("human", "json"),
            default="human",
            help="Output format (default: human).",
        )

    def handle(self, *args, **options):
        statuses = [classify(do) for do in missing_org_domain_orgs()]
        if options["format"] == "json":
            self.stdout.write(format_json(statuses))
        else:
            self.stdout.write(format_table(statuses))
