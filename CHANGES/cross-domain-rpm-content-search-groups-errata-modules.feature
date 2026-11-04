Extended the ad-hoc ``rpm/content-search/`` endpoints (see ``cross-domain-rpm-content-search.feature``)
with ``package-groups/``, ``errata/``, and ``module-streams/``, matching the query behavior of
their ``content-views/{pk}/search/rpm/...`` counterparts. As with the existing ``packages/``
endpoint, repository versions are passed directly as repeated ``repository_version`` query
parameters, with no persisted ``ContentView`` required.
