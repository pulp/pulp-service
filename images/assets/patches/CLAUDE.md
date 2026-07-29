# Patches

Patches applied to upstream packages during the container image build.
Each patch modifies files installed into site-packages via the Dockerfile.

## Upstream Repositories

| Patch prefix     | GitHub repository                                          | PyPI package     | Current version tag |
| ---------------- | ---------------------------------------------------------- | ---------------- | ------------------- |
| `pulpcore/`      | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | pulpcore         | 3.115.2             |
| `pulp_file/`     | [pulp/pulpcore](https://github.com/pulp/pulpcore)          | (bundled)        | 3.115.2             |
| `pulp_container/`| [pulp/pulp_container](https://github.com/pulp/pulp_container) | pulp-container | 2.28.0              |
| `pulp_python/`   | [pulp/pulp_python](https://github.com/pulp/pulp_python)    | pulp-python      | 3.31.2              |
| `pulp_maven/`    | [pulp/pulp_maven](https://github.com/pulp/pulp_maven)      | pulp-maven       | 0.12.0              |
| `pulp_rpm/`      | [pulp/pulp_rpm](https://github.com/pulp/pulp_rpm)          | pulp-rpm         | 3.38.2               |
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

### 0056 — Repository publication delete

- **Package:** pulpcore
- **Files:** `pulpcore/app/models/repository.py`
- **Description:** Optimizes repository deletion by materializing publication PKs before deleting published artifacts and switching to batched deletes (500 per batch) to limit WAL size in PostgreSQL.

### 0062 — Add Content View resource

- **Package:** pulpcore
- **Files:** `pulpcore/app/migrations/0155_contentview.py`, `pulpcore/app/models/__init__.py`, `pulpcore/app/models/content_view.py`, `pulpcore/app/serializers/__init__.py`, `pulpcore/app/serializers/content_view.py`, `pulpcore/app/util.py`, `pulpcore/app/util_content_view.py`, `pulpcore/app/viewsets/__init__.py`, `pulpcore/app/viewsets/content_view.py`, `pulpcore/plugin/models/__init__.py`, `pulpcore/plugin/serializers/__init__.py`, `pulpcore/plugin/util.py`, `pulpcore/plugin/viewsets/__init__.py`
- **Description:** Adds a new first-class `ContentView` resource: a domain-scoped, named object that composes Distributions -- potentially spanning multiple domains -- into a single, persistable search scope, with full CRUD and RBAC (`core.contentview_creator`/`_owner`/`_viewer` locked roles). Also adds a reusable `resolve_content_view_distributions`/`group_versions_by_domain`/`scatter_gather` utility, exposed via `pulpcore.plugin.*`, that lets plugins implement their own RBAC-respecting, cross-domain search endpoints nested under a Content View without querying the database directly. Under pulp-service, org-scoped sharing of Content Views is inherited for free from `DomainBasedPermission`, since standard pulpcore RBAC governs the resource. Also fixes `get_viewset_for_model` so it still resolves a content type's canonical viewset when a plugin (e.g. patch 0063) registers additional nested, read-only viewsets that reuse that model's queryset -- without this, RPM repository version `content_summary` hrefs and master-viewset queryset scoping broke for every content type patch 0063 touches (discovered via live end-to-end testing in the hosted dev container: an RPM sync task failed with `Could not determine ViewSet base name for model UpdateRecord`).

### 0063 — Add RPM Content View search endpoints

- **Package:** pulp_rpm
- **Files:** `pulp_rpm/app/serializers/__init__.py`, `pulp_rpm/app/serializers/content_view_search.py`, `pulp_rpm/app/viewsets/__init__.py`, `pulp_rpm/app/viewsets/content_view_search.py`
- **Description:** Implements six read-only RPM search endpoints nested under a Content View (`search/rpm/packages`, `package-groups`, `environments`, `errata`, `module-streams`, and `packages/list`), replacing the raw-Postgres batch search previously used by Content Sources. Each endpoint resolves the parent Content View's Distributions to their current repository versions across whatever domains they live in (via patch 0062's utilities) and either scatter-gathers a paginated queryset (errata, packages/list) or merges/dedups a lighter typeahead result set (packages, package-groups, environments) in Python. Requires patch 0062.
