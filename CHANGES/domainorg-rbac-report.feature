Added a read-only ``domainorg_rbac_report`` management command that reports whether each
``DomainOrg``'s principals (its user, its team group, and its derived ``rh-org-<org_id>``
group) actually hold the roles needed to GET and PUSH content on the domains they own. Each
principal/domain pair is classified FULL (``service.domain_admin``: GET and PUSH), VIEWER
(``service.domain_viewer``: GET only), or NONE (no roles: locked out), and anything
below FULL is flagged with a reason, so operators can spot orgs or groups that were never
granted access -- the complement to ``domainorg_backfill_report``, which reports only whether
``org_id`` can be backfilled. Supports ``--group``, ``--user``, and ``--org-id`` filters and
``--format {human,json}``. The same report is also available to admins via
``POST /api/pulp/debug/domainorg-rbac-report/``, which dispatches a task that produces the
report as a downloadable JSON file (retrievable from the task's ``profile_artifacts``).
