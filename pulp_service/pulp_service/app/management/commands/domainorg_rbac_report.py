"""Read-only report of whether DomainOrg principals hold the roles to GET/PUSH their domains.

For each DomainOrg row, this checks its principals -- the row's user, its team group, and the
derived ``rh-org-<org_id>`` group -- against each domain they own, and classifies each into
FULL (``service.domain_admin``: can GET and PUSH), VIEWER (``service.domain_viewer``: GET
only), or NONE (no roles: locked out, the calunga null-org_id failure mode). Anything below
FULL is flagged so operators can spot orgs/groups that were never granted access.

Complements ``domainorg_backfill_report`` (which reports whether org_id can be backfilled); this
reports whether the roles actually landed. Read-only: it makes no database writes.
"""

from django.core.management.base import BaseCommand

from pulp_service.app.domainorg_rbac_report import format_json, format_table
from pulp_service.app.tasks.domainorg_rbac_report import rbac_statuses


class Command(BaseCommand):
    help = "Report whether DomainOrg principals hold the roles to GET/PUSH content on their domains."

    def add_arguments(self, parser):
        parser.add_argument("--group", help="Only rows whose team group has this name.")
        parser.add_argument("--user", help="Only rows whose user has this username.")
        parser.add_argument("--org-id", dest="org_id", help="Only rows with this org_id.")
        parser.add_argument(
            "--format",
            choices=("human", "json"),
            default="human",
            help="Output format (default: human).",
        )

    def handle(self, *args, **options):
        statuses = rbac_statuses(group=options["group"], user=options["user"], org_id=options["org_id"])
        if options["format"] == "json":
            self.stdout.write(format_json(statuses))
        else:
            self.stdout.write(format_table(statuses))
