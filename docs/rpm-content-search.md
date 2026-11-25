# Ad-hoc RPM Content Search

`rpm/content-search/` is a set of lightweight, cross-domain RPM search endpoints that
take repository version hrefs directly in the request — no persisted resource
required. It's the no-setup counterpart to Content Views
(`content-views/{pk}/search/rpm/...`, which share the same query engine): use it
when you just want to search a one-off set of repository versions (e.g. your own
domain plus a shared `redhat`/`community` domain) without creating and maintaining a
named Content View first.

```
GET /api/pulp/<domain>/api/v3/rpm/content-search/packages/
GET /api/pulp/<domain>/api/v3/rpm/content-search/package-names/
GET /api/pulp/<domain>/api/v3/rpm/content-search/package-groups/
GET /api/pulp/<domain>/api/v3/rpm/content-search/environments/
GET /api/pulp/<domain>/api/v3/rpm/content-search/errata/
GET /api/pulp/<domain>/api/v3/rpm/content-search/module-streams/
```

The endpoints are part of the OpenAPI schema, so generated clients (e.g. the
`pulp_service` bindings or Zest) can call them.

## How it works

1. You pass one or more `repository_version` query parameters — hrefs (or PRNs) of
   repository versions, which may span any domain you can read.
2. Each href is resolved to its repository version, enforcing per-domain read access.
3. Each domain is queried separately and the results are merged into one page.
4. A version in a domain you can't read is **silently excluded** from results, not an
   error. A malformed or unresolvable href, or omitting the parameter entirely, is a
   `400`.

Unlike a Content View, nothing is persisted — each request is self-contained and there
is no resource to create, inspect, or delete.

## Common behavior

**Response envelope.** Every endpoint returns the standard paginated envelope:

```json
{"count": 42, "next": "…?limit=10&offset=10&…", "previous": null, "results": [...]}
```

`count` is the total number of matches. `limit` and `offset` are supported everywhere;
there is **no maximum offset**.

**Sort order.** Text columns are sorted case-insensitively first and by exact value
second (both by code point), so `base system` sorts before `Development Tools`, and
the order is identical whether the versions live in one domain or many. Names that
differ only in punctuation or spacing may sort differently than in a database using a
linguistic collation (e.g. `en_US.UTF-8`). Every sort ends with a unique tie-breaker,
so paging through results never skips or repeats a row.

**Deduplication.** `packages/` and `errata/` return one row per content unit (the same
package or advisory in two domains is two rows). `package-names/`, `package-groups/`,
`environments/` and `module-streams/` return one row per name / `(name, id)` /
`(name, stream)` across all the searched versions, and `count` counts those rows.

## Packages — `packages/`

One row per package build, sorted by `name`, `version`, `release`, `arch` (then
`pulp_href`).

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Href or PRN of a repository version to search. Repeat to search multiple versions in one request. |
| `name` | No | Exact package name. |
| `search` | No | Case-insensitive prefix of the package name (`du` matches `duck` and `DUMMY`). Can be combined with `name`. |
| `limit` | No | Page size (1–1000, default 100). |
| `offset` | No | Number of results to skip. |

```bash
BASE_URL=https://packages.redhat.com
DOMAIN=<your-domain>
AUTH="-u '<org-id>|<username>:<token>'"

curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/packages/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "repository_version=/api/pulp/redhat/api/v3/repositories/rpm/rpm/<uuid>/versions/7/" \
  --data-urlencode "search=redhat-release"
```

Each result has `pulp_href`, `name`, `epoch`, `version`, `release`, `arch`, `summary`,
`description`, `checksum_type`, `pkgId`, `url`, `location_href` and `is_modular`.

## Package names — `package-names/`

Typeahead: each matching package name once, sorted by name.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Case-insensitive prefix of the package name. |
| `limit` | No | Page size (1–1000, default 100). |
| `offset` | No | Number of results to skip. |

Each result is `{"name": ..., "summary": ...}`, where `summary` comes from one of the
packages with that name. `count` is the number of distinct matching names.

## Package groups — `package-groups/`

Case-insensitive substring match on the group name, sorted by name. When the same
`(name, id)` group appears in several repository versions, it's returned once with the
`packages` lists unioned.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Case-insensitive substring of the group name. |
| `limit` | No | Page size (1–1000, default 100). |
| `offset` | No | Number of results to skip. |

## Environments — `environments/`

Same as package groups, for package environments (deduplicated by `(name, id)`). Each
result has `pulp_href`, `id`, `name`, `description` and `group_ids`.

## Errata — `errata/`

Advisory search with filtering, sorting and pagination. CVE references are included in
each result as `cves`: a list of `{href, ref_id, title, ref_type}` objects.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Case-insensitive substring of the advisory `id`, `title` or `summary`. |
| `type` | No | Comma-separated types, matched exactly (e.g. `security`, `bugfix`, `enhancement`, `newpackage`). `other` also matches every type except `security`, `bugfix` and `enhancement` — so it includes `newpackage`. |
| `severity` | No | Comma-separated severities, matched exactly and case-sensitively. Pulp values are `Critical`, `Important`, `Moderate`, `Low`. `Unknown` also matches every other severity (including empty and `None`). |
| `sort_by` | No | `id`, `issued_date`, `updated_date`, `severity`, `type` or `title`; prefix with `-` for descending. Default `-issued_date` (newest first). Ties are broken by advisory id. Any other value is a `400`. |
| `limit` | No | Page size (1–1000, default 100). |
| `offset` | No | Number of results to skip. |

```bash
curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/errata/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "type=security" \
  --data-urlencode "severity=Critical,Important"
```

## Module streams — `module-streams/`

One row per `(name, stream)`: the module with the lowest `version` (compared as text).

| Parameter | Required | Description |
|-----------|----------|-------------|
| `repository_version` | Yes (at least one) | Same as above. |
| `search` | No | Case-insensitive substring of the module name. |
| `rpm_names` | No | Comma-separated RPM package names; only streams containing at least one are returned. |
| `has_packages` | No | `true`: only modules with packages. `false`: only modules without packages. Omitted: all modules. |
| `sort_by` | No | `name`, `stream`, `version`, `context` or `arch`; prefix with `-` for descending. Default `name`. Ties are broken by name and stream. Any other value is a `400`. |
| `limit` | No | Page size (1–5000, default 5000). |
| `offset` | No | Number of results to skip. |

```bash
curl ${AUTH} -G "${BASE_URL}/api/pulp/${DOMAIN}/api/v3/rpm/content-search/module-streams/" \
  --data-urlencode "repository_version=/api/pulp/${DOMAIN}/api/v3/repositories/rpm/rpm/<uuid>/versions/3/" \
  --data-urlencode "rpm_names=nodejs" \
  --data-urlencode "has_packages=true"
```

Each result has `pulp_href`, `name`, `stream`, `version`, `context`, `arch`,
`description`, `profiles` and `packages` (the sorted package names).

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
| `400` | An unsupported `sort_by` value, or a `has_packages` value other than `true`/`false`. |

## Limitations

- **No persistence** — nothing is saved between requests; you must pass the full set
  of repository versions every time.
