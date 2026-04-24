# Pulp Developer — Architecture Reference

## Pulpcore Plugin Architecture

### Import rules
- PREFER importing from `pulpcore.plugin.*` over `pulpcore.app.*` where available
- As a platform plugin, pulp-service may need `pulpcore.app.*` for internal APIs
  not exported via the plugin interface (e.g., `HeaderContentGuard`, `DomainSerializer`,
  `Task` model, `NAME_FILTER_OPTIONS`, `LabelFilter`)
- For standard content plugin patterns (Content, Repository, Remote, etc.),
  always use `pulpcore.plugin.*`
- Key `pulpcore.plugin` imports:
  - Models: `Content, Repository, Remote, Distribution, Publication, AutoAddObjPermsMixin`
  - Viewsets: `RepositoryViewSet, RemoteViewSet, ContentViewSet, DistributionViewSet, RolesMixin, OperationPostponedResponse`
  - Serializers: `RepositorySerializer, RemoteSerializer, ContentSerializer, SingleArtifactContentUploadSerializer`
  - Tasks: `from pulpcore.plugin.tasking import dispatch`
  - Utils: `from pulpcore.plugin.repo_version_utils import remove_duplicates, validate_repo_version`

### Master/Detail model pattern
- Every model subclass MUST define `TYPE = "type_string"` (class variable)
- Every model Meta MUST have `default_related_name = "%(app_label)s_%(model_name)s"`
- Content models need `repo_key_fields` tuple and `unique_together` including `_pulp_domain`
- Add `AutoAddObjPermsMixin` to models needing object-level RBAC
- Model Meta should include `permissions = [("manage_roles_modelname", "Can manage roles")]`

### Viewset RBAC pattern
- Every viewset needs `DEFAULT_ACCESS_POLICY` with `statements`, `creation_hooks`, and `queryset_scoping`
- Every viewset needs `LOCKED_ROLES` with `_creator`, `_owner`, `_viewer` role tiers
- Use `queryset_filtering_required_permission` for queryset scoping

### Task dispatch pattern
- Tasks receive PKs as strings, not ORM objects
- Dispatch with resource locks:
  ```python
  result = dispatch(
      tasks.my_task,
      shared_resources=[remote],        # read locks
      exclusive_resources=[repository],  # write locks
      kwargs={"remote_pk": str(remote.pk), "repository_pk": str(repository.pk)},
  )
  return OperationPostponedResponse(result, request)
  ```
- Use `ProgressReport` as context manager for long tasks

### Sync pipeline
- Create a `Stage` subclass with `async def run(self)`
- Use `DeclarativeContent` and `DeclarativeArtifact` to emit content
- Pass to `DeclarativeVersion(stage, repository, mirror).create()`

### Serializer pattern
- Extend parent `Meta.fields` with tuple concatenation: `fields = ParentSerializer.Meta.fields + ("custom_field",)`
- Content upload serializers use `deferred_validate()` for post-artifact validation
- Use `DomainUniqueValidator` for per-domain uniqueness

---

## pulp-service Specifics (NOT a standard content plugin)

pulp-service is an infrastructure/platform plugin — it does NOT define
Repository/Remote/Distribution subclasses. Instead it adds platform services.

### Authentication (app/authentication.py)
All auth reads `HTTP_X_RH_IDENTITY` header (base64-encoded JSON).
Four auth classes, all extend `JSONHeaderRemoteAuthentication` with different `jq_filter`:

| Class | jq_filter | Notes |
|-------|-----------|-------|
| `RHServiceAccountCertAuthentication` | `.identity.x509.subject_dn` | x509 cert auth |
| `RHTermsBasedRegistryAuthentication` | `.identity \| if .user.username then "\(.org_id // "")\|\(.user.username)" else null end` | Returns null for non-user identities, allowing auth fallthrough |
| `TurnpikeTermsBasedRegistryAuthentication` | Checks `.identity.auth_type == "registry-auth"` then extracts from `.identity.registry` | Registry-auth via Turnpike proxy |
| `RHSamlAuthentication` | `.identity.associate.email` | SAML for /pulp-mgmt/ admin |

### Multi-tenancy (app/models.py, app/authorization.py)
- `DomainOrg` model links `org_id`, `user` (FK), or `group` (FK) to Pulp domains (M2M).
  Access is granted by matching org_id, direct user FK, or group membership.
- `DomainBasedPermission` extracts org_id from `.identity.internal.org_id`
- `AllowUnauthPull` permission class allows safe methods (GET/HEAD/OPTIONS) without authentication
- ContextVars bridge permission checks to signal handlers:
  - `org_id_var` — set in permission check, read in post_save signal
  - `user_id_var` — set in permission check, read in post_save signal

### Middleware (app/middleware.py)
- `ProfilerMiddleware` — cProfile on `X-Profile-Request` header
- `TrueClientIPMiddleware` — Akamai True-Client-IP → X-Forwarded-For
- `RhEdgeHostMiddleware` — X-RH-EDGE-HOST → X-Forwarded-Host
- `RHSamlAuthHeaderMiddleware` — authenticates users for /pulp-mgmt/ admin via SAML + Django sessions
- `RequestPathMiddleware` — stores path in `request_path_var` ContextVar
- `ActiveConnectionsMetricMiddleware` — OpenTelemetry connection counter

Additional ContextVars in middleware.py:
- `repository_name_var` — for repository name tracking
- `x_quay_auth_var` — for Quay authentication context
- `x_task_diagnostics_var` — for task profiling diagnostics

### Storage (app/storage.py)
- `OCIStorage` (extends `BaseStorage`) — stores artifacts as OCI blobs in Quay.io via ORAS client
- S3 via pulpcore's built-in `S3Boto3Storage` with CloudFront patches (no custom class in pulp-service)
- `CreateDomainView` clones storage settings from `template-domain-s3`

### Custom viewsets (app/viewsets.py)
- `FeatureContentGuardViewSet` — subscription-feature content guards
- `VulnerabilityReport` — RPM/Python/Gem/npm scanning via osv.dev
- `PyPIYankMonitorViewSet` — daily PyPI yank monitoring
- `AgentScanReportView` — AI agent scan reports
- `CreateDomainView` — self-service domain creation from template
- `TaskDebugView`, `TaskQueueView`, `StaleLockScanView` — admin/debug endpoints
- Health check endpoints at `/api/pulp/` prefix
- Custom Django admin site at `/api/pulp-mgmt/`

### Tasks (app/tasks/)
- `check_content_from_repo_version` — vulnerability scanning (thread+Queue+async pattern)
- `check_packages_for_monitor` / `dispatch_pypi_yank_checks` — PyPI yank monitoring
- `content_sources_domains_count` / `rhel_ai_repos_count` — OpenTelemetry metrics
- RDS connection tests for infrastructure diagnostics

### Testing (tests/functional/)
- pytest + pytest-django with pulpcore test fixtures
- Session-scoped API bindings from `pulpcore.client.pulp_service`
- Auth tests construct X-RH-IDENTITY JSON, base64-encode, inject as default headers
- `cleanup_auth_headers` fixture prevents cross-test contamination
- Run with: `pytest pulp_service/pulp_service/tests/functional/`

### Code style
- Black formatter, line length 100
- Towncrier changelog fragments in `CHANGES/` (`{issue}.{feature|bugfix|doc|removal|misc}`)
- Source: `pulp_service/pulp_service/app/`
- Tests: `pulp_service/pulp_service/tests/functional/`

### Runtime patches (critical awareness)
18 patches applied at Docker build time to modify pulpcore and upstream plugins.
Any change touching patched areas must verify patch compatibility.
Patches cover: OCI storage, CloudFront, registry API routing, ClamAV, attestation.
