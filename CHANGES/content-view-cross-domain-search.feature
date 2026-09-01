Added the ``ContentView`` resource (``content-views/``): a named, persistable scope composed
of Distributions that may span multiple domains. Added six nested, read-only RPM search
endpoints (``content-views/{pk}/search/rpm/packages``, ``package-groups``, ``environments``,
``errata``, ``module-streams``, ``packages/list``) that resolve a content view's distributions
to their current repository versions and search across them, honoring per-domain RBAC by
silently excluding distributions in domains the requesting user can no longer view.
