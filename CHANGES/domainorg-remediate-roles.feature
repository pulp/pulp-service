Added the ``POST /api/pulp/debug/domainorg-remediate-roles/`` admin endpoint to set org_id and
assign domain roles for DomainOrg rows that migration 0022 could not backfill, restoring GET/PUSH
access for the org's ``rh-org-<org_id>`` group. Supports ``dry_run`` and returns a downloadable
JSON result artifact.
