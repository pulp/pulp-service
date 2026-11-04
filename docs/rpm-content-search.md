# Ad-hoc RPM Content Search

`rpm/content-search/` is a set of lightweight, cross-domain RPM search endpoints that
take repository version hrefs directly in the request — no persisted resource
required. It's the no-setup counterpart to [Content Views](content-view.md): use it
when you just want to search a one-off set of repository versions (e.g. your own
domain plus a shared `redhat`/`community` domain) without creating and maintaining a
named Content View first.

```
GET /api/pulp/<domain>/api/v3/rpm/content-search/packages/
GET /api/pulp/<domain>/api/v3/rpm/content-search/package-groups/
GET /api/pulp/<domain>/api/v3/rpm/content-search/errata/
GET /api/pulp/<domain>/api/v3/rpm/content-search/module-streams/
```

> **Note**: no client bindings are generated for these endpoints. Call them directly
> with `curl` or `requests`.

## How it works

1. You pass one or more `repository_version` query parameters — hrefs (or PRNs) of
   repository versions, which may span any domain you can read.
2. Each href is resolved to its repository version, enforcing per-domain read access.
3. Content from all resolved versions is searched and merged, using the same query
   engine as the corresponding Content View search endpoints
   (`content-views/{pk}/search/rpm/...`).
4. A version in a domain you can't read is **silently excluded** from results, not an
   error. A malformed or unresolvable href, or omitting the parameter entirely, is a
   `400`.

Unlike a Content View, nothing is persisted — each request is self-contained and there
is no resource to create, inspect, or delete.

## Packages — `rpm/content-search/packages/`

Exact-name-filtered, NEVRA-sorted, paginated. Same query behavior as
`content-views/{pk}/search/rpm/packages/list/`.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Href or PRN of a repository version to search. Repeat to search multiple versions in one request. |
| `name` | No | Exact package name filter. |
| `limit` | No | Page size. |
| `offset` | No | Pagination offset. |

```bash
BASE_URL=https://packages.redhat.com
DOMAIN=<your-domain>
AUTH="-u '<org-id>|<username>:<token>'"

curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/packages/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "repository_version=/api/pulp/redhat/api/v3/repositories/rpm/rpm/<uuid>/versions/7/" \
  --data-urlencode "name=bash"
```

Paginated response, same shape as `content-views/{pk}/search/rpm/packages/list/`:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "pulp_href": "/api/pulp/redhat/api/v3/content/rpm/packages/.../",
      "name": "bash",
      "epoch": "0",
      "version": "5.2.15",
      "release": "2.fc38",
      "arch": "x86_64"
    }
  ]
}
```

Results are sorted by NEVRA (`name`, `version`, `release`, `arch`), with `pulp_id` as a
deterministic tie-breaker when identical NEVRA content exists across domains.

## Package groups — `rpm/content-search/package-groups/`

Substring match on group name, not paginated. Same query behavior as
`content-views/{pk}/search/rpm/package-groups/`. When the same `(name, id)` group
appears in multiple repository versions, the `packages` lists are unioned.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Case-insensitive substring filter on group name. |
| `limit` | No | Max results (1–100, default 25). |

```bash
curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/package-groups/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "search=core"
```

Response shape: `{"results": [...]}` (flat list, no pagination envelope).

## Errata — `rpm/content-search/errata/`

Full advisory/errata search with filtering, sorting, and pagination. Same query
behavior as `content-views/{pk}/search/rpm/errata/`. CVE references are included in
each result.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Substring match on errata `id` or `title`. |
| `type` | No | Comma-separated types: `security`, `bugfix`, `enhancement`, `newpackage`. Unknown values match an "other" catch-all bucket. |
| `severity` | No | Comma-separated severities: `critical`, `important`, `moderate`, `low`, `none`. Unknown values match an "other" catch-all bucket. |
| `sort_by` | No | Sort field; prefix with `-` for descending. Allowed: `id`, `updated_date`, `issued_date`, `severity`, `type`, `title`. |
| `limit` | No | Page size (1–1000, default 100). |
| `offset` | No | Pagination offset. |

```bash
curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/errata/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "type=security" \
  --data-urlencode "sort_by=-issued_date"
```

Paginated response (`count`, `next`, `previous`, `results`), same shape as
`content-views/{pk}/search/rpm/errata/`.

## Module streams — `rpm/content-search/module-streams/`

Module stream search with optional RPM name filter, not paginated. Same query
behavior as `content-views/{pk}/search/rpm/module-streams/`.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Substring match on module `name` or `stream`. |
| `rpm_names` | No | Comma-separated RPM package names; only streams containing at least one are returned. |
| `sort_by` | No | Sort field; prefix with `-` for descending. Allowed: `name`, `stream`, `version`, `context`, `arch`. |
| `limit` | No | Max results (1–5000, default 100). |

```bash
curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/module-streams/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "rpm_names=nodejs"
```

Response shape: `{"results": [...]}` (flat list, no pagination envelope).

## Access control

Any authenticated user can call these endpoints — there's no resource-level role to
grant. Results are still scoped by per-domain RBAC: you only see content from
repository versions in domains you can read (`core.view_domain`). Referencing a
version in an inaccessible domain doesn't error; it's just left out of the results.

## Errors

| Status | Cause |
|--------|-------|
| `400` | No `repository_version` parameter supplied. |
| `400` | A `repository_version` value is malformed or doesn't resolve to a repository version. |

## Limitations

- **No ad-hoc equivalent for environments (`PackageEnvironment`)** — use a
  [Content View](content-view.md) if you need to search those.
- **No persistence** — nothing is saved between requests; you must pass the full set
  of repository versions every time.
