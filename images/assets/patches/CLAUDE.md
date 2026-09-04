# Patches

Patches applied to upstream packages during the container image build.
Each patch modifies files installed into site-packages via the Dockerfile.

## Upstream Repositories

| Patch prefix     | GitHub repository                                          | PyPI package     | Current version tag |
| ---------------- | ---------------------------------------------------------- | ---------------- | ------------------- |
| `pulpcore/`      | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | pulpcore         | 3.117.1             |
| `pulp_file/`     | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | (bundled)        | 3.112.0             |
| `pulp_container/`| [pulp/pulp_container](https://github.com/pulp/pulp_container) | pulp-container | 2.28.0              |
| `pulp_python/`   | [pulp/pulp_python](https://github.com/pulp/pulp_python)    | pulp-python      | 3.36.0              |
| `pulp_maven/`    | [pulp/pulp_maven](https://github.com/pulp/pulp_maven)      | pulp-maven       | 0.12.0              |
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

### 0018 — Re-root the registry API at /api/pulp/v2/

- **Package:** pulp_container
- **Files:** `pulp_container/app/content.py`, `pulp_container/app/redirects.py`, `pulp_container/app/token_verification.py`, `pulp_container/app/urls.py`
- **Description:** Moves all container registry URL routes from `/v2/` to `/api/pulp/v2/` and the content app prefix from `/pulp/container/` to `/api/pulp-container/`. Replaces `RegistryPermission` with `DomainBasedPermission`.

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
