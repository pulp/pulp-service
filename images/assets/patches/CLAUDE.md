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
| `pulpcore/`      | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | pulpcore         | 3.123.2             |
| `pulp_file/`     | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | (bundled)        | 3.123.2             |
| `pulp_container/`| [pulp/pulp_container](https://github.com/pulp/pulp_container) | pulp-container | 2.30.1              |
| `pulp_python/`   | [pulp/pulp_python](https://github.com/pulp/pulp_python)    | pulp-python      | 3.38.0              |
| `pulp_maven/`    | [pulp/pulp_maven](https://github.com/pulp/pulp_maven)      | pulp-maven       | 0.37.2              |
| `pulp_rpm/`      | [pulp/pulp_rpm](https://github.com/pulp/pulp_rpm)          | pulp-rpm         | 3.39.5              |
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

### 0081 - Fix task cancellation during graceful worker shutdown

- **Package:** pulpcore
- **Files:** `pulpcore/tasking/worker.py`, `pulpcore/tasking/redis_worker.py`
- **Description:** A graceful worker shutdown (SIGTERM, e.g. during a rollout) sets `task_grace_timeout = None` so the worker waits forever for the in-flight task. The cancel path reused that same field as its kill deadline, so canceling the task only logged `Wait for canceled task to abort.` forever and never sent SIGUSR1. The task stayed `canceling`, and the worker couldn't exit but kept heartbeating, so it held the task's exclusive resource locks indefinitely and blocked every task queued on them (seen in prod: a straggler pod wedged on a canceled `upload_group` task with 122 tasks queued behind its repository lock). Fix: a separate per-task `cancel_kill_deadline` so a cancel aborts the task whatever the shutdown grace setting. Also, once a task is being aborted, the shutdown branch no longer overrides its state, so it stays `canceled` instead of becoming `failed`.
- **Upstream:** pulp/pulpcore#8304. Drop this patch once a pulpcore release with that fix is pinned.


### 0077 - Directory membership timestamp experiment

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** Dispatches HTML directory membership date lookups through the service's disabled-by-default PULP-2505 experiment. B filters in SQL and fetches only content IDs and creation dates. Shared displayed names participate: both variants return dates by content ID, and the handler aggregates the newest date per displayed entry. See `docs/test_plans/0004-Directory_Membership_Dates.md`.

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

### 0049 — Skip content units validation

- **Package:** pulpcore
- **Files:** `pulpcore/app/serializers/repository.py`
- **Description:** Skips the content unit existence check and timestamp-of-interest update when adding more than 10,000 content units to a repository version, avoiding client request timeouts on large batch operations.

### 0058 — Fix migrate backend task

- **Package:** pulpcore
- **Files:** `pulpcore/app/tasks/migrate.py`
- **Description:** Fixes the storage backend migration task to handle artifacts that fail to migrate gracefully, collecting skipped items instead of raising a `ValidationError` on first failure.

### 0063 — Redirect large artifacts to object storage

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** Adds a `LARGE_FILE_REDIRECT_THRESHOLD` (1.7 GB) so that artifacts exceeding the threshold are always redirected to object storage, even when `domain.redirect_to_object_storage` is False. Prevents large file downloads from being served directly through the content app.
- **Note:** Includes changes from: 0065-Route-content-pull-through-writes-to-primary.patch, 0066-use-cache-control-max-age-for-redis-ttl.patch, 0068-Reset-all-db-connections-on-stale-connection-retry.patch, 0070-Retry-publication-lookup-on-replica-conflict.patch, 0073-never-decrease-redis-hash-ttl.patch, 0075-Retry-content-artifact-lookups-on-replica-conflict.patch, 0078-Return-authentication-challenge-for-missing-identity.patch, 0069-Retry-distribution-match-on-replica-conflict.patch



### 0067 — Make cached ArtifactResponse DB-free

- **Package:** pulpcore
- **Files:** `pulpcore/cache/cache.py`
- **Description:** Replaces the old 0067 skip-caching workaround with a proper fix. Instead of storing `artifact_pk` in the Redis cache entry (which required a DB round-trip on every cache HIT via `Artifact.objects.aget(pk=...)`, and couldn't work at all for path-index's unsaved in-memory artifacts), stores `storage_path` and `size` under a new `CachedArtifactResponse` type. On cache HIT, `CachedArtifactResponse` reconstructs a synthetic `Artifact` with a `CachedFile` (same pattern as pulp_maven's `IndexedFile`) — no DB query needed. This makes path-index responses fully cacheable in Redis (~25ms → ~5ms) and eliminates the DB round-trip on ALL `ArtifactResponse` cache hits (benefits non-path-index too). `ArtifactResponse` is kept in `RESPONSE_TYPES` so new workers can still read old-format cache entries (with `artifact_pk`) during rolling deployments; old workers encountering the new `CachedArtifactResponse` type treat it as a cache miss (no error). See PULP-2513.
- **Upstream:** Candidate for a pulpcore fix (cache entries should not require DB lookups).




### 0071 — Retry content guard lookup on replica conflict

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** `Handler._permit()` accesses `distribution.content_guard`, a lazily-loaded FK not covered by `_match_distribution()`'s `select_related()`, so it issues its own unguarded query. Seen in production as `OperationalError: the connection is closed` from the content app (a stale replica connection, distinct from the recovery-conflict variant fixed by patches 0069/0070) when resolving a `MavenDistribution`'s content guard. Wraps the `distribution.content_guard` access with the same catch-reset-retry pattern, calling `Handler._reset_db_connection()` (patch 0068) before retrying once.


### 0074 — No-store Cache-Control on 302 redirects

- **Package:** pulpcore
- **Files:** `pulpcore/content/handler.py`
- **Description:** When a content-artifact response is a 302 redirect (`HTTPFound`) — e.g. large artifacts routed to object storage per patch 0063, or redirect-to-object-storage domains — the redirect carried no cache directives, so the short-lived pre-signed S3/CloudFront URL in the `Location` header could be cached by intermediaries and replayed after it expired. Fix: set `Cache-Control: private, no-store` on the `HTTPFound` response before raising it, so redirect responses (and their transient signed URLs) are never cached. Complements patch 0073, which caps the in-cache TTL for redirect entries; this patch stops the redirect itself from being cached downstream.
- **Upstream:** Candidate for a pulpcore fix (redirect responses should not be cacheable).


### 0076 — Fix false-positive artifact-storage prefix check

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/fields.py`
- **Description:** `ArtifactFileField.pre_save()` used a raw string prefix check to detect whether an incoming file was already stored in Artifact storage. When `MEDIA_ROOT` is empty (e.g. S3-backed deployments), the prefix collapses to the bare string `"artifact"`, so any freshly uploaded file whose name happens to start with that substring (e.g. a Maven checksum sidecar like `artifact-2.42.13.pom.sha512`) was wrongly flagged as already being in storage, raising an unhandled `ValueError` and a 500. Fix: anchor the check on a real path boundary instead of a raw substring.
- **Upstream:** Candidate for a pulpcore fix (pulpcore #8041).
