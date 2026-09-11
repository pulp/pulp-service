Switched the default REST framework permission class from ``DomainBasedPermission`` to
``PulpServiceAccessPolicy``, enabling RBAC as the default authorization backend. Users
can now upload content into their domain and view it immediately via the content
endpoints even when it is not in any repository (orphan content), because domain members
hold the domain-scoped ``core.view_content`` permission through ``service.domain_admin``.
