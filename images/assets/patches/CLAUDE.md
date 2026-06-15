# Patches

Patches applied to upstream packages during the container image build.
Each patch modifies files installed into site-packages via the Dockerfile.

## Upstream Repositories

| Patch prefix     | GitHub repository                                          | PyPI package     | Current version tag |
| ---------------- | ---------------------------------------------------------- | ---------------- | ------------------- |
| `pulpcore/`      | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | pulpcore         | 3.112.0             |
| `pulp_file/`     | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | (bundled)        | 3.112.0             |
| `pulp_container/`| [pulp/pulp_container](https://github.com/pulp/pulp_container) | pulp-container | 2.28.0              |
| `pulp_python/`   | [pulp/pulp_python](https://github.com/pulp/pulp_python)    | pulp-python      | 3.30.3              |
| `pulp_maven/`    | [pulp/pulp_maven](https://github.com/pulp/pulp_maven)      | pulp-maven       | 0.12.0              |
| `oras/`          | [oras-project/oras-py](https://github.com/oras-project/oras-py) | oras        | 0.2.38              |
| `storages/`      | [jschneier/django-storages](https://github.com/jschneier/django-storages) | django-storages | 1.14.6 |

Versions are pinned in `pulp_service/requirements.txt`. Django-storages is a
transitive dependency pinned in pulpcore's `pyproject.toml`.

## Patches

### 0010 — Added ability to return a URL for a blob

- **Package:** oras (oras-py)
- **Files:** `oras/provider.py`
- **Description:** Adds a `return_blob_url` parameter to the oras provider so callers can get the blob's redirect URL instead of downloading the blob content. Used by OCI storage to serve content via redirects.

### 0011 — OCI storage backend changes

- **Package:** pulpcore
- **Files:** `pulpcore/app/serializers/domain.py`, `pulpcore/constants.py`, `pulpcore/content/handler.py`
- **Description:** Registers `OCIStorage` as a storage backend option, adds its domain settings serializer, and teaches the content app to redirect requests to OCI registries when a domain uses OCI storage.

### 0014 — Add Content Sources periodic telemetry task

- **Package:** pulpcore
- **Files:** `pulpcore/tasking/_util.py`
- **Description:** Imports and registers `content_sources_periodic_telemetry` and `rhel_ai_repos_periodic_telemetry` tasks from pulp-service so they run on worker startup.

### 0018 — Re-root the registry API at /api/pulp/v2/

- **Package:** pulp_container
- **Files:** `pulp_container/app/content.py`, `pulp_container/app/redirects.py`, `pulp_container/app/token_verification.py`, `pulp_container/app/urls.py`
- **Description:** Moves all container registry URL routes from `/v2/` to `/api/pulp/v2/` and the content app prefix from `/pulp/container/` to `/api/pulp-container/`. Replaces `RegistryPermission` with `DomainBasedPermission`.

### 0022 — Adds authentication to the mvn deploy api

- **Package:** pulp_maven
- **Files:** `pulp_maven/app/maven_deploy_api.py`, `pulp_maven/app/urls.py`
- **Description:** Removes the disabled authentication classes from the Maven deploy API view and re-roots the Maven API URL from `/pulp/maven/` to `/api/pulp/maven/`.

### 0028 — OCI Storage create manifest

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/publication.py`
- **Description:** Adds a `_create_oci_manifest` method to the Publication model that collects all artifacts from a repository version, retrieves their blob metadata from the OCI registry, builds an OCI image manifest, and uploads it.

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

### 0047 — Improve repair_metadata log with repo and package names

- **Package:** pulp_python
- **Files:** `pulp_python/app/tasks/repair.py`
- **Description:** Enhances the repair metadata task log message to include the repository name and resolved package names (name-version) instead of raw PKs, making repair logs actionable.

### 0048 — Re-enable attestation verification with vendored Konflux key

- **Package:** pulp_python
- **Files:** `pulp_python/app/provenance.py`, `pulp_python/app/pypi/serializers.py`, `pulp_python/app/settings.py`
- **Description:** Replaces the upstream sigstore-based attestation verification with a custom implementation that uses a vendored Konflux signing key for PEP 740 attestation validation. Adds DER certificate-based signature verification and a configurable attestation keys directory.

### 0049 — Skip content units validation

- **Package:** pulpcore
- **Files:** `pulpcore/app/serializers/repository.py`
- **Description:** Skips the content unit existence check and timestamp-of-interest update when adding more than 10,000 content units to a repository version, avoiding client request timeouts on large batch operations.

### 0052 — Pulpcore agent scan report

- **Package:** pulpcore
- **Files:** `pulpcore/app/serializers/content.py`, `pulpcore/app/serializers/repository.py`
- **Description:** Adds an `agent_scan_report` field to content and repository version serializers that links to the agent scan report endpoint, enabling the UI to display scan results.

### 0053 — Python agent scan task

- **Package:** pulp_python
- **Files:** `pulp_python/app/models.py`, `pulp_python/app/tasks/__init__.py`, `pulp_python/app/tasks/scan_package.py`
- **Description:** Adds an automatic package scanning task that dispatches on repository version creation. Downloads Python package artifacts to a temp directory, runs an external scanner, and collects results.

### 0056 — Repository publication delete

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/repository.py`
- **Description:** Optimizes repository deletion by materializing publication PKs before deleting published artifacts and switching to batched deletes (500 per batch) to limit WAL size in PostgreSQL.
