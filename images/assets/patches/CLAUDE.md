# Patches

Patches applied to upstream packages during the container image build.
Each patch modifies files installed into site-packages via the Dockerfile.

## Validation notes

Apply the existing stack with GNU `patch`, as the Dockerfiles do. Some older
patches rely on its context matching and do not apply with `git apply`.
When testing an isolated source tree through `oci-env exec`, set `PYTHONPATH`
after `sudo -u pulp`; sudo can remove it even with `-E`.

## Upstream Repositories

| Patch prefix     | GitHub repository                                          | PyPI package     | Current version tag |
| ---------------- | ---------------------------------------------------------- | ---------------- | ------------------- |
| `pulpcore/`      | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | pulpcore         | 3.121.2             |
| `pulp_file/`     | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | (bundled)        | 3.112.0             |
| `pulp_container/`| [pulp/pulp_container](https://github.com/pulp/pulp_container) | pulp-container | 2.28.0              |
| `pulp_python/`   | [pulp/pulp_python](https://github.com/pulp/pulp_python)    | pulp-python      | 3.36.2              |
| `pulp_maven/`    | [pulp/pulp_maven](https://github.com/pulp/pulp_maven)      | pulp-maven       | 0.34.1              |
| `pulp_rpm/`      | [pulp/pulp_rpm](https://github.com/pulp/pulp_rpm)          | pulp-rpm         | 3.39.0              |
| `storages/`      | [jschneier/django-storages](https://github.com/jschneier/django-storages) | django-storages | 1.14.6 |

Versions are pinned in `pulp_service/requirements.txt`. Django-storages is a
transitive dependency pinned in pulpcore's `pyproject.toml`.

## Decommissioned: OCI Storage

The custom OCI storage backend (`OCIStorage`, ORAS client, Quay.io blob storage) has been
**decommissioned**. The following patches and dependencies were removed:

- Patch 0010 — oras blob URL redirect support
- Patch 0011 — OCIStorage backend registration in pulpcore
- Patch 0028 — OCI manifest creation on publication
- `oras` Python dependency and `pulp_service/app/storage.py`

The separate `oci-storage-backup-setup` repository is unaffected.

## Patches

### 0077 - Directory membership timestamp experiment

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** Dispatches eligible HTML directory membership date lookups through the service's disabled-by-default PULP-2505 experiment. B filters in SQL and fetches only content IDs and creation dates. Both variants preserve pulpcore's newest-timestamp-wins behavior; shared displayed names retain A to keep the experiment scope conservative. JSON listings are unchanged. See `docs/test_plans/0004-Directory_Membership_Dates.md`.

### 0022 — Adds authentication to the mvn deploy api

- **Package:** pulp_maven
- **Files:** `pulp_maven/app/maven_deploy_api.py`, `pulp_maven/app/urls.py`
- **Description:** Removes the disabled authentication classes from the Maven deploy API view and re-roots the Maven API URL from `/pulp/maven/` to `/api/pulp/maven/`.

### 0031 — Replace ResponseContentDisposition in CloudFront

- **Package:** django-storages
- **Files:** `storages/backends/s3.py`
- **Description:** Fixes CloudFront signed URL generation by replacing the uppercase `ResponseContentDisposition` query parameter with the lowercase `response-content-disposition` form that CloudFront expects.

### 0032 — Disable the timestamp of interest query

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/content.py`
- **Description:** Removes the `SELECT FOR UPDATE` timestamp-of-interest refresh query that caused deadlocks under high concurrency, replacing it with a no-op stub.

### 0034 — Fix profile artifact being stored in default domain

- **Package:** pulpcore
- **Files:** `pulpcore/tasking/_util.py`
- **Description:** Wraps diagnostic profile artifact creation in `with_domain(task.pulp_domain)` so the artifact is stored in the task's domain instead of the default domain.

### 0044 — Move content app heartbeat to a thread

- **Package:** pulpcore
- **Files:** `pulpcore/content/__init__.py`
- **Description:** Converts the content app heartbeat from an async coroutine to a synchronous thread with a shutdown event. Replaces `asyncio.sleep` with `threading.Event.wait` and async ORM calls with synchronous ones.

### 0048 — Re-enable attestation verification with vendored Konflux key

- **Package:** pulp_python
- **Files:** `pulp_python/app/provenance.py`, `pulp_python/app/pypi/serializers.py`, `pulp_python/app/settings.py`
- **Description:** Replaces the upstream sigstore-based attestation verification with a custom implementation that uses a vendored Konflux signing key for PEP 740 attestation validation. Adds DER certificate-based signature verification and a configurable attestation keys directory.

### 0049 — Skip content units validation

- **Package:** pulpcore
- **Files:** `pulpcore/app/serializers/repository.py`
- **Description:** Skips the content unit existence check and timestamp-of-interest update when adding more than 10,000 content units to a repository version, avoiding client request timeouts on large batch operations.

### 0058 — Fix migrate backend task

- **Package:** pulpcore
- **Files:** `pulpcore/app/tasks/migrate.py`
- **Description:** Fixes the storage backend migration task to handle artifacts that fail to migrate gracefully, collecting skipped items instead of raising a `ValidationError` on first failure.

### 0059 — Add content negotiation and JSON listing to the content app

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/publication.py`, `pulpcore/cache/__init__.py`, `pulpcore/cache/cache.py`, `pulpcore/content/handler.py`
- **Description:** Adds content negotiation to the content app so clients requesting `application/json` receive a JSON directory listing instead of a file download. Extends the cache layer to handle the new response type.

### 0060 — Add content_handler_json to PythonDistribution

- **Package:** pulp_python
- **Files:** `pulp_python/app/models.py`, `pulp_python/app/utils.py`
- **Description:** Implements `content_handler_json` on `PythonDistribution` to serve JSON-formatted package metadata responses when clients request `application/json` via the content app.

### 0062 — Add If-Modified-Since header support

- **Package:** pulpcore
- **Files:** `pulpcore/cache/cache.py`, `pulpcore/content/handler.py`
- **Description:** Adds `If-Modified-Since` request header handling to the content app so clients receive `304 Not Modified` responses when cached content has not changed, reducing unnecessary data transfer.

### 0063 — Redirect large artifacts to object storage

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** Adds a `LARGE_FILE_REDIRECT_THRESHOLD` (1.7 GB) so that artifacts exceeding the threshold are always redirected to object storage, even when `domain.redirect_to_object_storage` is False. Prevents large file downloads from being served directly through the content app.

### 0064 — Add ETag header support to content app

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`, `pulpcore/cache/cache.py`
- **Description:** Adds `ETag` (sha256-based) and `Cache-Control: public, max-age=0, must-revalidate` headers to content app file responses. Extends the cache layer's `_check_not_modified()` to handle `If-None-Match` requests alongside `If-Modified-Since`, and calls it on both cache HIT and MISS paths so ETag-matched 304 responses are properly cached in Redis.
- **Upstream:** Not upstreamed yet — candidate for pulpcore contribution (no upstream PR).

### 0065 — Route content pull-through writes to primary

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** Routes pull-through caching operations and failed-download updates to the primary database while content reads use the replica.

### 0066 — Use Cache-Control max-age for Redis TTL (non-redirect domains only)

- **Package:** pulpcore
- **Files:** `pulpcore/cache/cache.py`, `pulpcore/content/handler.py`
- **Description:** Sets `Cache-Control: max-age=86400` on content app responses and uses that value as the Redis cache entry TTL, but only when the domain has `redirect_to_object_storage=False` (content streamed through the app). When `redirect_to_object_storage=True`, responses are redirects to signed S3/CloudFront URLs with limited lifetimes, so the Redis TTL is left at the default to avoid serving expired signed URLs from cache.

### 0067 — Make cached ArtifactResponse DB-free

- **Package:** pulpcore
- **Files:** `pulpcore/cache/cache.py`
- **Description:** Replaces the old 0067 skip-caching workaround with a proper fix. Instead of storing `artifact_pk` in the Redis cache entry (which required a DB round-trip on every cache HIT via `Artifact.objects.aget(pk=...)`, and couldn't work at all for path-index's unsaved in-memory artifacts), stores `storage_path` and `size` under a new `CachedArtifactResponse` type. On cache HIT, `CachedArtifactResponse` reconstructs a synthetic `Artifact` with a `CachedFile` (same pattern as pulp_maven's `IndexedFile`) — no DB query needed. This makes path-index responses fully cacheable in Redis (~25ms → ~5ms) and eliminates the DB round-trip on ALL `ArtifactResponse` cache hits (benefits non-path-index too). `ArtifactResponse` is kept in `RESPONSE_TYPES` so new workers can still read old-format cache entries (with `artifact_pk`) during rolling deployments; old workers encountering the new `CachedArtifactResponse` type treat it as a cache miss (no error). See PULP-2513.
- **Upstream:** Candidate for a pulpcore fix (cache entries should not require DB lookups).

### 0068 — Reset all DB connections on stale connection retry

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `Handler._reset_db_connection()` only reset the `default` database alias, so when the content app's `ContentReplicaRouter` sent a read to the `replica` alias and that connection went stale (`OperationalError: the connection is closed`), the authentication retry in `pulpcore/content/authentication.py` kept hitting the same dead connection and failing. Resets every configured alias via `django.db.connections.all()` instead of just `default`.

### 0069 — Retry distribution match on replica conflict

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `Handler._match_distribution()` had no retry logic around its `Distribution` lookup, so a Postgres hot-standby recovery conflict on the read replica (`OperationalError: terminating connection due to conflict with recovery`) surfaced as an unhandled 500 on every content-app request. Catches `InterfaceError`/`DatabaseError` around the query, resets connections via `_reset_db_connection()` (patch 0068), and retries once.

### 0070 — Retry publication lookup on replica conflict

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `_match_and_stream()` calls `distro.get_repository_publication_and_version()` (in `pulpcore/app/models/publication.py`), which runs `self.publication.cast()` — an unguarded multi-table query. This call site was missed by patch 0069, so it still surfaced raw `OperationalError: terminating connection due to conflict with recovery` 500s from the content app when the read replica killed the query mid-flight (seen in production for `rpm_rpmpublication` lookups). Adds `Handler._get_repository_publication_and_version()`, wrapping the call with the same catch-reset-retry pattern as patch 0069.

### 0071 — Retry content guard lookup on replica conflict

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `Handler._permit()` accesses `distribution.content_guard`, a lazily-loaded FK not covered by `_match_distribution()`'s `select_related()`, so it issues its own unguarded query. Seen in production as `OperationalError: the connection is closed` from the content app (a stale replica connection, distinct from the recovery-conflict variant fixed by patches 0069/0070) when resolving a `MavenDistribution`'s content guard. Wraps the `distribution.content_guard` access with the same catch-reset-retry pattern, calling `Handler._reset_db_connection()` (patch 0068) before retrying once.

### 0073 — Never decrease Redis hash TTL when caching a new entry

- **Package:** pulpcore
- **Files:** `pulpcore/cache/cache.py`
- **Description:** Pulp's content cache stores all entries for one distribution in a single Redis hash. Every `set()` call ran `EXPIRE` on the hash key, resetting the TTL for **all** entries. When a cacheable 404 (no `Cache-Control` header) was stored with the default `EXPIRES_TTL` of 600 s, it reset the hash TTL from 86400 s (set by artifact entries via patch 0066) back to 600 s. After 10 minutes Redis evicted the entire hash — including artifact entries that should have lived for 24 hours. This caused perpetual `X-PULP-CACHE: MISS` on every request because the cache never survived long enough to serve a HIT. Fix: check the current TTL before calling `EXPIRE` and only increase it, never decrease. Additionally, caps the in-entry `expires` for `HTTPFound` (redirect) entries at `DEFAULT_EXPIRES_TTL` (600 s) as defense-in-depth. Patch 0066 already skips the max-age TTL override for domains with `redirect_to_object_storage=True`, but patch 0063 forces large files (>1.7 GB) through redirect even on non-redirect domains — without this cap, those redirect entries would cache expired pre-signed S3/CloudFront URLs for up to 23 hours.
- **Upstream:** Candidate for a pulpcore fix (defense against mixed-TTL hash entries).

### 0074 — No-store Cache-Control on 302 redirects

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** When a content-artifact response is a 302 redirect (`HTTPFound`) — e.g. large artifacts routed to object storage per patch 0063, or redirect-to-object-storage domains — the redirect carried no cache directives, so the short-lived pre-signed S3/CloudFront URL in the `Location` header could be cached by intermediaries and replayed after it expired. Fix: set `Cache-Control: private, no-store` on the `HTTPFound` response before raising it, so redirect responses (and their transient signed URLs) are never cached. Complements patch 0073, which caps the in-cache TTL for redirect entries; this patch stops the redirect itself from being cached downstream.
- **Upstream:** Candidate for a pulpcore fix (redirect responses should not be cacheable).

### 0075 — Retry content artifact lookups on replica conflict

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `_match_and_stream()` runs three more unguarded `ContentArtifact` queries that patches 0069-0071 didn't cover: the pass-through publication lookup, and (for repository versions served without a publication) the `index.html` existence check and the final content-artifact lookup. Seen in production as `OperationalError: canceling statement due to conflict with recovery` from the content app (a `core_contentartifact` lookup by `relative_path` scoped to a repository version's content, e.g. for a Maven repo) when the read replica killed the query mid-flight during hot-standby recovery. Adds `Handler._retry_content_artifact_query()`, a reusable async wrapper around the same catch-reset-retry pattern as patches 0069/0070, and applies it at all three call sites.

### 0076 — Fix false-positive artifact-storage prefix check

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/fields.py`
- **Description:** `ArtifactFileField.pre_save()` used a raw string prefix check to detect whether an incoming file was already stored in Artifact storage. When `MEDIA_ROOT` is empty (e.g. S3-backed deployments), the prefix collapses to the bare string `"artifact"`, so any freshly uploaded file whose name happens to start with that substring (e.g. a Maven checksum sidecar like `artifact-2.42.13.pom.sha512`) was wrongly flagged as already being in storage, raising an unhandled `ValueError` and a 500. Fix: anchor the check on a real path boundary instead of a raw substring.
- **Upstream:** Candidate for a pulpcore fix (pulpcore #8041).
