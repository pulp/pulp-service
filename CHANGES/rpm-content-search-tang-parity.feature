Added ``rpm/content-search/package-names/`` (typeahead: each package name once) and
``rpm/content-search/environments/`` endpoints, a case-insensitive name-prefix ``search`` filter
on ``packages/``, a ``has_packages`` filter on ``module-streams/``, and ``offset`` support on every
content search endpoint. The ``content-views/{pk}/search/rpm/...`` endpoints share the same changes.
