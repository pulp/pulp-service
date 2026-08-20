Added a data migration that converts existing DomainOrg entries into pulpcore RBAC role
assignments (object-level core.domain_owner/core.domain_viewer on the domain plus
domain-scoped service.domain_admin/service.domain_viewer), creating rh-org-<org_id> groups
for org-id-only entries.
