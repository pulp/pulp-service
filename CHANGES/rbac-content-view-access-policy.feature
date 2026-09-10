Under RBAC, users holding the domain-scoped ``core.view_content`` permission can now
list all content in their domain (including orphan content they pushed) via the
list-all content endpoint. Implemented in configuration only: PulpServiceAccessPolicy
now inherits from pulpcore's ``AccessPolicyFromSettings`` and the ``content`` viewset
policy in settings gates the list action on ``core.view_content`` and drops
repository-based queryset scoping. Inert until RBAC is re-enabled as the default
permission class.
