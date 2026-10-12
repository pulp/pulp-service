Added ``GET rpm/content-search/packages/``, an ad-hoc, cross-domain RPM package search that
takes repository-version hrefs directly as repeated ``repository_version`` query parameters
instead of requiring a persisted ``ContentView``. This lets callers (e.g. image-builder) search
a one-off set of repository versions -- potentially spanning their own domain and shared domains
such as ``redhat`` or ``community`` -- in a single request. Per-domain read access is enforced
the same way as ``ContentView`` search: a referenced repository version in a domain the caller
can't read is silently excluded from results rather than causing an error. Built on the same
``scatter_gather`` query engine as the ``content-views/{pk}/search/rpm/...`` endpoints.
