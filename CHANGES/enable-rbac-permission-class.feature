Made RBAC the default authorization model by switching the default permission class from
``DomainBasedPermission`` to ``PulpServiceAccessPolicy`` (in both the dev container and the
production ClowdApp). Domain members can now upload content and view it immediately through
the content endpoints -- including orphan content not yet in any repository -- because they
hold ``core.view_content`` via ``service.domain_admin``.

Turning RBAC on surfaced three issues, all fixed here:

* The ``service.domain_admin`` / ``service.domain_viewer`` roles were seeded on the service
  app's ``post_migrate`` alone, before later plugins (file, certguard) had created their
  permissions, so a fresh migrate left the roles incomplete and domain owners got 403 creating
  a repository. The roles are now rebuilt on every plugin's ``post_migrate``, each rebuild in a
  transaction, so the full permission set is present once the last plugin has migrated.

* Reads of a ``public-*`` domain by a caller with no role returned 404: ``has_permission``
  allowed the read but ``scope_queryset`` filtered the object out. ``scope_queryset`` now
  honours the public-domain read bypass too, returning 200 without exposing other domains.

* Members of an org that owns a domain were locked out (404 on reads, 400 on uploads) when the
  domain's ``rh-org-<org_id>`` group held no roles. This happens when the ``DomainOrg`` has a
  null ``org_id`` (its create request carried no ``internal.org_id``), which skips the org
  group's role grant while the team group still gets it. A data migration backfills existing
  domains, deriving the org from the team group's members and granting ``rh-org-<org_id>`` the
  missing roles. Domain creation now also derives ``org_id`` from the creating user's
  ``rh-org-<org_id>`` membership when the request omits ``internal.org_id``, so no new
  null-``org_id`` domains are produced.
