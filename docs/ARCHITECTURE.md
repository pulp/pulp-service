# Pulp Service Architecture

## Overview

This is a Django plugin for Pulp (Python package repository management system) that provides a backend REST API application for managing container images and Python packages.

## Technology Stack

- **Framework**: Django 4.2+ with Django REST Framework (DRF)
- **Web Servers**:
  - Gunicorn (WSGI) for pulp-api
  - Gunicorn with aiohttp.GunicornWebWorker for pulp-content
- **Python Version**: 3.9+ (tested on 3.11)
- **Database**: PostgreSQL
- **Deployment**: Container-based (Docker), deployed on OpenShift
- **Task Queue**: Celery with Redis
- **Storage**: S3-compatible object storage (configurable)

## Quick Reference Index

This index helps you quickly locate code for common development tasks.

### Core Plugin Code

| Component | File Path | Description |
|-----------|-----------|-------------|
| **Django Settings** | `pulp_service/pulp_service/app/settings.py` | Plugin Django settings overrides |
| **Models** | `pulp_service/pulp_service/app/models.py` | Plugin-specific database models |
| **ViewSets** | `pulp_service/pulp_service/app/viewsets.py` | REST API endpoints |
| **Serializers** | `pulp_service/pulp_service/app/serializers.py` | API request/response serializers |
| **Authentication** | `pulp_service/pulp_service/app/authentication.py` | Custom auth backends (X.509, SAML) |
| **Authorization** | `pulp_service/pulp_service/app/authorization.py` | Permission and access control logic |
| **Middleware** | `pulp_service/pulp_service/app/middleware.py` | Django middleware (profiling, headers, metrics) |
| **Storage** | `pulp_service/pulp_service/app/storage.py` | Custom S3 storage backends |
| **Signals** | `pulp_service/pulp_service/app/signals.py` | Django signal handlers |
| **Tasks** | `pulp_service/pulp_service/app/tasks/` | Celery background tasks |
| **Content Server** | `pulp_service/pulp_service/app/content.py` | aiohttp middleware for pulp-content |

### Deployment & Configuration

| Component | File Path | Description |
|-----------|-----------|-------------|
| **OpenShift Deploy** | `deploy/clowdapp.yaml` | Production deployment configuration |
| **API Startup** | `images/assets/pulp-api` | Local dev API server startup script |
| **Content Startup** | `images/assets/pulp-content` | Local dev content server startup script |
| **Worker Startup** | `images/assets/pulp-worker` | Local dev worker startup script |
| **WSGI Middleware** | `images/assets/log_middleware.py` | User extraction from X-RH-IDENTITY |
| **Gunicorn Config** | `images/assets/gunicorn_config.py` | Gunicorn hooks and middleware registration |
| **Dependencies** | `pulp_service/requirements.txt` | Python package dependencies with versions |

### Monitoring & Observability

| Component | File Path | Description |
|-----------|-----------|-------------|
| **OTEL Config** | `deploy/otel-config.yaml` (in clowdapp) | OpenTelemetry collector configuration |
| **Grafana Dashboards** | `deploy/dashboards/*.configmap.yaml` | Pre-built Grafana dashboard definitions |

### Common Development Tasks

- **Adding new API endpoint**: Create viewset in `viewsets.py`, serializer in `serializers.py`, register URL
- **Custom authentication**: Add class to `authentication.py`, register in Django settings
- **New middleware**: Add class to `middleware.py`, register in `PULP_MIDDLEWARE` setting
- **Background task**: Create task in `tasks/`, call from viewset or signal
- **Modify logging**: Update `--access-logformat` in `images/assets/pulp-api` and `deploy/clowdapp.yaml`
- **Add metrics**: Use `init_otel_meter()` from pulpcore.metrics in relevant module
- **Database model**: Add to `models.py`, create migration with `pulpcore-manager makemigrations`

## Service Architecture

### pulp-api
REST API service for managing repositories, content, and permissions.

**Local Development**:
- **Entry Point**: `images/assets/pulp-api`
- **Port**: 24817
- **WSGI Application**: `pulpcore.app.wsgi:application`

**Production (OpenShift)**:
- **Command**: `pulpcore-api`
- **Port**: 8000
- **External Path**: `/api/pulp`
- **Custom access log format with correlation-id tracking**
- **User extraction from X-RH-IDENTITY header via WSGI middleware**

### pulp-content
Content delivery service for serving repository artifacts.

**Local Development**:
- **Entry Point**: `images/assets/pulp-content`
- **Port**: 24816
- **WSGI Application**: `pulpcore.content:server`
- **Worker Class**: `aiohttp.GunicornWebWorker`

**Production (OpenShift)**:
- **Command**: `pulpcore-content`
- **Port**: 8000
- **External Path**: `/api/pulp-content`
- **Custom middleware for org_id response headers**
- **Gunicorn config**: `/tmp/gunicorn_config.py`

### pulp-worker
Background task processor using Celery.

**Entry Point**: `images/assets/pulp-worker` (local) or `pulpcore-worker` (production)
- **Worker Type**: Redis-based or pulpcore-based (configurable)
- **Auxiliary Workers**: Separate worker pool with auto-scaling support

## Request Flow

```
Client Request
    ↓
Load Balancer / Reverse Proxy (sets X-Forwarded-For, X-RH-IDENTITY)
    ↓
Gunicorn (access logs)
    ↓
WSGI Middleware (UserExtractionMiddleware)
    ↓
Django Middleware Stack
    ↓
Django REST Framework Views/ViewSets
    ↓
Response
```

## Middleware Stack

### WSGI Level (images/assets/log_middleware.py)
*Plugin-specific middleware - not part of upstream Pulpcore*

Applied via Gunicorn's `post_worker_init` hook:

1. **UserExtractionMiddleware**
   - Extracts user info from `X-RH-IDENTITY` header
   - Decodes base64 JSON payload
   - Sets `environ["REMOTE_USER"]` and `environ["ORG_ID"]`
   - Supports multiple identity types: user, x509, SAML

### Django Level (pulp_service/pulp_service/app/middleware.py)
*Plugin-specific middleware - extends upstream Pulpcore Django middleware*

Applied in order via Django settings:

1. **ProfilerMiddleware** - Profiles requests when the `X-Profile-Request` header is present
2. **RhEdgeHostMiddleware** - Maps `X-RH-EDGE-HOST` to `X-FORWARDED-HOST`
3. **RHSamlAuthHeaderMiddleware** - Extracts user from `X-RH-IDENTITY` for `/pulp-mgmt/` paths
4. **RequestPathMiddleware** - Stores request path in ContextVar for signals
5. **ActiveConnectionsMetricMiddleware** - Tracks concurrent connections with OpenTelemetry

### aiohttp Level (pulp_service/pulp_service/app/content.py)
*Plugin-specific middleware - extends upstream Pulpcore content server*

For pulp-content service:

1. **add_rh_org_id_resp_header** - Adds `X-RH-ORG-ID` response header from identity

## Logging Configuration

### Access Logs

**pulp-api** (Production):
```
pulp [%({correlation-id}o)s]: %(h)s %(l)s user:%({REMOTE_USER}e)s org_id:%({ORG_ID}e)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(M)s x_forwarded_for:"%({X-Forwarded-For}i)s"
```

**pulp-api** (Local/Dev):
```
pulp [%({correlation-id}o)s]: %(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" x_forwarded_for:"%({X-Forwarded-For}i)s"
```

**pulp-content** (Production):
```
%a %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" cache:"%{X-PULP-CACHE}o" artifact_size:"%{X-PULP-ARTIFACT-SIZE}o" rh_org_id:"%{X-RH-ORG-ID}o" x_forwarded_for:"%{X-Forwarded-For}i"
```

**Format Fields**:
- `%({correlation-id}o)s` - Correlation ID from response header
- `%(h)s` / `%a` - Remote address
- `%(l)s` - Remote logname
- `%(u)s` - Remote user
- `%({REMOTE_USER}e)s` - User from WSGI environ (extracted by middleware)
- `%({ORG_ID}e)s` - Org ID from WSGI environ (extracted by middleware)
- `%(t)s` - Timestamp
- `%(r)s` - Request line
- `%(s)s` - Status code
- `%(b)s` - Response size in bytes
- `%(f)s` / `%{Referer}i` - Referer header
- `%(a)s` / `%{User-Agent}i` - User agent
- `%(M)s` - Request time in milliseconds (pulp-api only)
- `x_forwarded_for:"%({X-Forwarded-For}i)s"` - X-Forwarded-For header (comma-separated list of IPs, first IP is original client)
- `%{X-PULP-CACHE}o` - Cache hit/miss status (pulp-content only)
- `%{X-PULP-ARTIFACT-SIZE}o` - Artifact size
- `%{X-RH-ORG-ID}o` - Organization ID response header

### Application Logs
- Standard Python `logging` module
- Each module uses: `_logger = logging.getLogger(__name__)`
- Configured via Django settings
- Logs sent to stdout for container-based collection

## Context Variables

The application uses Python's `contextvars` for request-scoped data:

- `repository_name_var` - Current repository name
- `x_quay_auth_var` - Quay authentication token
- `x_task_diagnostics_var` - Task diagnostics flag
- `request_path_var` - Request path (set by RequestPathMiddleware)
- `org_id_var` - Organization ID
- `user_id_var` - User ID

These are accessible throughout the request lifecycle and in async tasks.

## Authentication & Authorization

### Authentication Classes (pulp_service/pulp_service/app/authentication.py)
*Plugin-specific authentication - extends upstream Pulpcore auth classes*

1. **RHServiceAccountCertAuthentication** - X.509 certificate authentication (plugin-specific)
2. **RHSamlAuthentication** - SAML authentication via X-RH-IDENTITY header (plugin-specific)
3. **SessionAuthentication** - Django session-based auth (upstream)
4. **BasicAuthentication** - HTTP Basic auth (upstream)

### Header-based Identity
The `X-RH-IDENTITY` header contains base64-encoded JSON:

```json
{
  "identity": {
    "user": {"username": "...", "email": "..."},
    "org_id": "123456"
  }
}
```

## Storage Backend

Custom S3 storage implementation in `pulp_service/pulp_service/app/storage.py`:
*Plugin-specific - extends Django's S3Boto3Storage beyond upstream Pulpcore*

- **AIPCCStorageBackend** - Extends Django's S3Boto3Storage
- **OCIStorageBackend** - OCI-specific storage with manifest handling
- Supports multipart uploads and custom metadata

## Database Models

Key models in `pulp_service/pulp_service/app/models.py`:
*Plugin-specific models - extend upstream Pulpcore models*

- **RHCertGuardPermission** - Certificate-based access control
- **PushRepository** - Repository configuration
- **RHServiceAccount** - Service account management
- **RpmPackageDownload** - Package download tracking for metrics

## API ViewSets

Main API endpoints in `pulp_service/pulp_service/app/viewsets.py`:
*Plugin-specific viewsets - extend upstream Pulpcore viewsets*

- **RHCertGuardPermissionViewSet** - Manage cert guard permissions
- **PushRepositoryViewSet** - Repository CRUD operations
- **BusinessMetricsViewSet** - Business metrics and analytics
- **RHServiceAccountViewSet** - Service account management

## Signals & Hooks

The application uses Django signals for event handling:

- Repository creation/deletion events
- Content publication events
- User authentication events

Signals can access request context via ContextVars.

## Task System

Background tasks using Celery (in `pulp_service/pulp_service/app/tasks/`):

- **Package scanning** (ClamAV integration)
- **Metrics collection**
- **Repository synchronization**
- **Storage cleanup**

## Configuration Files

### Key Directories

- `images/assets/` - Container startup scripts and patches
- `pulp_service/pulp_service/app/` - Main Django application code
- `docs/` - Documentation
- `tools/` - Helper tools and benchmarking utilities

### Important Files

- `images/assets/pulp-api` - API server startup script (local/dev)
- `images/assets/pulp-content` - Content server startup script (local/dev)
- `images/assets/log_middleware.py` - WSGI middleware for user extraction
- `images/assets/gunicorn_config.py` - Gunicorn hooks configuration
- `pulp_service/pulp_service/app/middleware.py` - Django middleware
- `pulp_service/pulp_service/app/settings.py` - Django settings overrides
- `deploy/clowdapp.yaml` - OpenShift ClowdApp deployment configuration

## Environment Variables

> **Note on Canonical Sources:**
> - Default values shown here are examples and may drift from actual deployment
> - Production values: See `deploy/clowdapp.yaml` for authoritative deployment configuration
> - Version dependencies: See `pulp_service/requirements.txt` for pinned versions
> - Variables marked with 🔧 are **plugin-specific** (defined in this plugin)
> - Variables marked with ⬆️ are **upstream Pulpcore** (inherited from pulpcore)
> - Variables marked with 🔌 are **upstream plugin** (from pulp-python, pulp-container, etc.)

### Core Configuration
*⬆️ Upstream Pulpcore settings*

- ⬆️ `DJANGO_SETTINGS_MODULE=pulpcore.app.settings`
- ⬆️ `PULP_SETTINGS=/etc/pulp/settings.py` - Path to Django settings file
- ⬆️ `PULP_API_ROOT=/api/pulp/` - API root path
- ⬆️ `PULP_CONTENT_ORIGIN` - Base URL for content delivery
- ⬆️ `PULP_CONTENT_PATH_PREFIX=/api/pulp-content/` - Content path prefix

### Database & Storage
*⬆️ Upstream Pulpcore settings*

- ⬆️ `PULP_DB_ENCRYPTION_KEY=/etc/pulp/keys/database_fields.symmetric.key` - DB field encryption
- ⬆️ `PULP_CACHE_ENABLED=true` - Enable Redis caching
- ⬆️ `PULP_REDIS_PORT=6379` - Redis port
- ⬆️ `PULP_STORAGES__default__BACKEND` - Storage backend class (🔧 set to plugin AIPCCStorageBackend)
- ⬆️ `PULP_STORAGES__default__OPTIONS__default_acl` - S3 ACL setting
- ⬆️ `PULP_STORAGES__default__OPTIONS__signature_version=s3v4` - S3 signature version
- ⬆️ `PULP_STORAGES__default__OPTIONS__addressing_style=path` - S3 addressing style
- ⬆️ `PULP_MEDIA_ROOT` - Media files root (empty for S3)

### Gunicorn Configuration
*⬆️ Upstream Pulpcore settings, 🔧 plugin customizes via startup scripts*

- ⬆️ `PULP_API_GUNICORN_TIMEOUT=1800` - API request timeout (seconds)
- ⬆️ `PULP_API_GUNICORN_WORKERS=1` - Number of API workers
- ⬆️ `PULP_API_GUNICORN_MAX_REQUESTS=20` - Max requests per worker before restart
- ⬆️ `PULP_API_GUNICORN_MAX_REQUESTS_JITTER=5` - Jitter for max requests
- ⬆️ `PULP_CONTENT_GUNICORN_TIMEOUT=90` - Content request timeout
- ⬆️ `PULP_CONTENT_GUNICORN_GRACEFUL_TIMEOUT=300` - Graceful shutdown timeout
- ⬆️ `PULP_CONTENT_GUNICORN_MAX_REQUESTS=20` - Max requests per content worker
- 🔧 `GUNICORN_CMD_ARGS=--config /usr/bin/log_middleware.py` - Additional Gunicorn args (plugin-specific)

### Authentication & Authorization
*🔧 Plugin-specific configuration with upstream base*

- ⬆️ `PULP_AUTHENTICATION_BACKENDS` - List of Django authentication backends (Python list literal of dotted module paths, 🔧 plugin adds custom backends)
- ⬆️ `PULP_REST_FRAMEWORK__DEFAULT_AUTHENTICATION_CLASSES` - DRF auth classes (Python list literal of dotted module paths, 🔧 plugin adds custom classes)
- ⬆️ `PULP_REST_FRAMEWORK__DEFAULT_PERMISSION_CLASSES` - DRF permission classes (Python list literal of dotted module paths)
- 🔧 `PULP_AUTHENTICATION_JSON_HEADER=HTTP_X_RH_IDENTITY` - Identity header name (plugin-specific)
- 🔧 `PULP_AUTHENTICATION_JSON_HEADER_JQ_FILTER=.identity.user.username` - JQ filter for username (plugin-specific)
- 🔌 `PULP_TOKEN_AUTH_DISABLED=true` - Disable container registry token auth (pulp-container setting)
- ⬆️ `PULP_USE_X_FORWARDED_HOST=true` - Use X-Forwarded-Host for URL building
- ⬆️ `PULP_SECURE_PROXY_SSL_HEADER=['HTTP_X_FORWARDED_PROTO', 'https']` - SSL proxy header (Python list literal: [header_name, value])

### Middleware
*🔧 Plugin-specific configuration*

- ⬆️/🔧 `PULP_MIDDLEWARE` - List of Django middleware classes (Python list literal of dotted module paths, plugin adds custom middleware)

### Security & Content
*⬆️ Upstream Pulpcore settings*

- ⬆️ `PULP_ALLOWED_CONTENT_CHECKSUMS=["sha224", "sha256", "sha384", "sha512"]` - Allowed checksums (Python list literal)
- ⬆️ `PULP_CSRF_TRUSTED_ORIGINS` - List of trusted CSRF origins (Python list literal)
- ⬆️ `PULP_DOMAIN_ENABLED=true` - Enable multi-domain support

### Worker Configuration
*⬆️ Upstream Pulpcore settings with 🔧 plugin customizations*

- ⬆️ `PULP_WORKER_TYPE=redis` - Worker implementation (redis or pulpcore)
- ⬆️ `PULP_TASK_PROTECTION_TIME=20160` - Task retention time (minutes)
- ⬆️ `PULP_TASK_DIAGNOSTICS=['memory', 'memray', 'pyinstrument']` - Available profilers (Python list literal)
- ⬆️ `PULP_UPLOAD_PROTECTION_TIME=480` - Upload cleanup time (minutes)
- ⬆️ `PULP_MAX_CONCURRENT_CONTENT=200` - Batch size for content sync

### Observability (OpenTelemetry)
*🔧 Plugin-specific configuration*

- 🔧 `PULP_OTEL_ENABLED=true` - Enable OpenTelemetry (plugin-specific)
- 🔧 `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` - OTLP protocol
- 🔧 `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:10000/` - Collector endpoint
- 🔧 `OTEL_METRIC_EXPORT_INTERVAL=7000` - Export interval (ms)
- 🔧 `OTEL_METRIC_EXPORT_TIMEOUT=7000` - Export timeout (ms)
- 🔧 `OTEL_TRACES_EXPORTER=none` - Disable trace export
- 🔧 `OTEL_PYTHON_EXCLUDED_URLS=.*livez,.*status` - Exclude URLs from metrics (comma-separated regex patterns)
- 🔧 `PULP_OTEL_PULP_API_HISTOGRAM_BUCKETS=[100.0,250.0,500.0,1000.0,2500.0,5000.0]` - Histogram buckets in milliseconds (Python list literal)

### External Services Integration
*🔧 Plugin-specific configuration*

- 🔧 `PULP_CLAMAV_HOST` - ClamAV service hostname (plugin-specific)
- 🔧 `PULP_CLAMAV_PORT=10000` - ClamAV service port (plugin-specific)
- 🔧 `PULP_FEATURE_SERVICE_API_URL` - Feature service API URL (plugin-specific)
- 🔧 `PULP_FEATURE_SERVICE_API_CERT_PATH=/etc/pulp/certs/pulp-services-non-prod.pem` - Service cert (plugin-specific)
- 🔧 `PULP_PYPI_API_HOSTNAME` - PyPI API hostname for distribution URLs (plugin-specific)
- 🔧 `SENTRY_DSN` - Sentry/GlitchTip error tracking DSN (optional, plugin-specific)

### Feature Flags
*🔧 Plugin-specific feature toggles*

- 🔧 `PULP_TEST_TASK_INGESTION=false` - Enable test task ingestion endpoint
- 🔌 `PULP_PYTHON_GROUP_UPLOADS=true` - Group Python package uploads (pulp-python)
- 🔧 `PULP_UVLOOP_ENABLED=false` - Enable uvloop for content workers
- 🔧 `PULP_RDS_CONNECTION_TESTS_ENABLED=false` - Enable RDS connection test endpoints
- 🔧 `PULP_API_APP_TTL=120` - API application TTL (seconds)

### Deployment Parameters
*🔧 Plugin-specific OpenShift deployment configuration (see deploy/clowdapp.yaml)*

- 🔧 `PULP_API_REPLICAS=1` - Number of API replicas (default, can be overridden)
- 🔧 `PULP_CONTENT_REPLICAS=1` - Number of content replicas (default, can be overridden)
- 🔧 `PULP_WORKER_REPLICAS=2` - Number of worker replicas (default, can be overridden)
- 🔧 `PULP_WORKER_AUXILIARY_REPLICAS=1` - Number of auxiliary worker replicas (default, can be overridden)
- 🔧 `PULP_MIGRATION_REPLICAS=1` - Number of migration replicas
- 🔧 `CLAMAV_REPLICAS=1` - Number of ClamAV replicas

## Development Patterns

### Modifying Deployment Configuration

When making changes that affect both local development and production:

1. **Local/Dev**: Modify startup scripts in `images/assets/` (e.g., `pulp-api`, `pulp-content`)
2. **Production**: Update `deploy/clowdapp.yaml` deployment configuration
3. **Both environments**: Ensure changes are synchronized between both files

Example: Adding X-Forwarded-For to logs requires updating:
- `images/assets/pulp-api` - Update the `--access-logformat` option in the Gunicorn command
- `images/assets/pulp-content` - Update the `--access-logformat` option in the Gunicorn command
- `deploy/clowdapp.yaml` - Update the `--access-logformat` in both `pulp-api` and `pulp-content` deployment command args

### Adding New Headers to Logs

1. **For access logs**: Modify `--access-logformat` in startup scripts and clowdapp.yaml
2. **For application code**: Use ContextVars pattern in middleware

### Adding New Middleware

1. **WSGI level**: Add to `log_middleware.py` and register in `gunicorn_config.py`
2. **Django level**: Add class to `middleware.py` and register in Django settings
3. **aiohttp level**: Add to `content.py` and append to `app._middlewares`

### Adding New APIs

1. Create model in `models.py`
2. Create serializer in appropriate serializers file
3. Create viewset in `viewsets.py`
4. Register in URL configuration

## Deployment

### Container Build
Uses multi-stage Dockerfile with:
- Base image: UBI9 Python 3.11
- Pulpcore and plugins installed via pip
- Custom patches applied from `images/assets/patches/`

### OpenShift Deployment (ClowdApp)

The application is deployed using Red Hat's ClowdApp operator. Configuration: `deploy/clowdapp.yaml`

#### Deployments

> **Note**: Resource limits, replica counts, timeouts, and other configuration values shown below are examples from a reference deployment. For authoritative production values, always consult `deploy/clowdapp.yaml`.

**1. pulp-api**
- **Purpose**: REST API service for managing repositories, content, and permissions
- **Command**: `pulpcore-api` (Gunicorn-based)
- **Port**: 8000 (internal), exposed via `/api/pulp` path
- **Default Replicas**: 1 (configurable via `PULP_API_REPLICAS`)
- **Gunicorn Configuration**:
  - Timeout: 1800s (30 minutes)
  - Workers: 1 (configurable)
  - Max requests per worker: 20 (with jitter: 5)
- **Resource Limits**:
  - CPU: 500m request, 1000m limit
  - Memory: 256Mi request, 512Mi limit
- **Health Checks**:
  - Readiness: GET `/api/pulp/api/v3/livez/` (delay: 5s, period: 60s)
  - Liveness: GET `/api/pulp/api/v3/livez/` (delay: 10s, period: 120s)
- **Sidecars**: OpenTelemetry collector
- **Init Containers**: wait-on-migrations

**2. pulp-content**
- **Purpose**: Content delivery service for serving repository artifacts
- **Command**: `pulpcore-content` (aiohttp-based)
- **Port**: 8000 (internal), exposed via `/api/pulp-content` path
- **Default Replicas**: 1 (configurable via `PULP_CONTENT_REPLICAS`)
- **Gunicorn Configuration**:
  - Timeout: 90s
  - Graceful timeout: 300s
  - Max requests per worker: 20 (with jitter: 5)
  - Config file: `/tmp/gunicorn_config.py`
- **Resource Limits**:
  - CPU: 250m request, 500m limit
  - Memory: 256Mi request, 512Mi limit
- **Health Checks**:
  - Readiness: GET `/api/pulp-content/default/` (delay: 60s, period: 60s)
  - Liveness: GET `/api/pulp-content/default/` (delay: 60s, period: 120s)
- **Sidecars**: OpenTelemetry collector
- **Init Containers**: wait-on-migrations

**3. pulp-worker**
- **Purpose**: Background task processor (Celery workers)
- **Command**: `pulpcore-worker`
- **Default Replicas**: 2 (configurable via `PULP_WORKER_REPLICAS`)
- **Resource Limits**:
  - CPU: 250m request, 500m limit
  - Memory: 1024Mi request, 2048Mi limit
- **Termination Grace Period**: 3660s (61 minutes)
- **Worker Type**: Redis-based (configurable via `PULP_WORKER_TYPE`)
- **Sidecars**: OpenTelemetry collector (optional)

**4. pulp-worker-auxiliary**
- **Purpose**: Auxiliary worker pool for background tasks
- **Command**: `pulpcore-worker --auxiliary`
- **Default Replicas**: 1 (configurable via `PULP_WORKER_AUXILIARY_REPLICAS`)
- **Auto-scaling**:
  - Min replicas: 1
  - Max replicas: 20
  - Trigger: Prometheus metric `pulp_waiting_tasks > 1`
  - Scale up: 5 pods per 30s
  - Scale down: 2 pods per 60s (with 300s stabilization window)
- **Resource Limits**: Same as pulp-worker
- **Termination Grace Period**: 3660s (61 minutes)

**5. clamav**
- **Purpose**: Antivirus scanning service for uploaded content
- **Image**: `docker.io/clamav/clamav:1.5.1`
- **Command**: `/init-unprivileged`
- **Port**: 10000 (TCP)
- **Default Replicas**: 1
- **Configuration**:
  - Max file size: 2G
  - Max scan size: 2G
  - Stream max length: 2G
- **Resource Limits**:
  - CPU: 1 request, 2 limit
  - Memory: 3Gi request, 4Gi limit
- **Health Checks**: `clamdscan --ping 1`

**6. migrate-db**
- **Purpose**: Database migration job
- **Command**: `bash -x /tmp/migrate.sh`
- **Script**: Runs `pulpcore-manager migrate --noinput`
- **Replicas**: 1 (runs once, then sleeps)
- **Init Containers**: wait-on-postgres

#### Jobs

Jobs are one-time or periodic tasks managed by ClowdJobInvocation:

1. **create-settings-and-ingress**: Creates Kubernetes secrets and routes
2. **reset-admin-password**: Resets the admin user password
3. **create-contentsources-user**: Creates the `contentsources` service user
4. **add-new-pulp-admin-users-3**: Adds predefined admin users to the system

#### Managed Resources

**Database**:
- PostgreSQL 15
- Managed by Clowder
- Connection info injected via `/cdapp/cdappconfig.json`

**In-Memory Database**:
- Redis
- Used for caching and Celery broker
- Managed by Clowder

**Object Storage**:
- S3-compatible storage (MinIO or AWS S3)
- Bucket name: `pulp-default-domain-s3`
- Managed by Clowder

#### Secrets

- `pulp-db-fields-encryption`: Database field encryption key
- `pulp-admin-password`: Admin user password
- `pulp-content-sources-password`: Content sources user password
- `pulp-settings`: Django settings.py configuration
- `subscription-api-cert`: Service certificate for external API calls
- `pulp-glitchtip`: Sentry/GlitchTip DSN (optional)

#### Health Checks & Startup

**Init Containers** (all services):
1. `wait-on-postgres`: Wait for PostgreSQL to be ready
2. `wait-on-migrations`: Wait for database migrations to complete

**Readiness Probes**: Ensure service is ready to accept traffic
**Liveness Probes**: Ensure service is running, restart if unhealthy

**Termination Grace Period**:
- API/Content: 120s
- Workers: 3660s (allows long-running tasks to complete)

## Monitoring & Observability

### OpenTelemetry Integration

**Configuration** (via environment variables):
- `PULP_OTEL_ENABLED`: Enable/disable telemetry
- `OTEL_EXPORTER_OTLP_PROTOCOL`: `http/protobuf`
- `OTEL_EXPORTER_OTLP_ENDPOINT`: `http://localhost:10000/`
- `OTEL_METRIC_EXPORT_INTERVAL`: 7000ms
- `OTEL_METRIC_EXPORT_TIMEOUT`: 7000ms
- `OTEL_TRACES_EXPORTER`: `none` (traces disabled, metrics only)

**Collector Sidecars**:
- Deployed alongside api, content, and worker pods
- Receives metrics via OTLP protocol
- Exports to Prometheus endpoint (port 9000)
- Memory: 256Mi request, 384Mi limit

**Metrics Pipeline** (`pulp-otel-config` ConfigMap):
1. **metrics/aggregation**: Aggregates `api.request_duration` metrics
2. **metrics/main**: Exports all other metrics
3. **Processors**:
   - `memory_limiter`: Limits memory to 200MiB
   - `filter/*`: Filters specific metrics
   - `attributes/remove_worker_name`: Removes worker name attribute
   - `batch/*`: Batches metrics for export
   - `groupbyattrs/*`: Groups by specific attributes

**Excluded URLs**: `.*livez,.*status` (health checks excluded from metrics)

**API Histogram Buckets**: `[100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0]` (milliseconds)

### Logging
- **Structured logging**: Correlation IDs, user, org_id, request time
- **Access logs**: Sent to stdout, collected by OpenShift logging
- **Application logs**: Python logging module, sent to stdout

### Profiling
- **On-demand profiling**: Via `X-Profile-Request` header
- **Task diagnostics**: Memory, memray, pyinstrument profilers
- **ProfilerMiddleware**: Captures request performance data

### Error Tracking
- **Sentry/GlitchTip** integration (optional)
- DSN configured via `pulp-glitchtip` secret
- Automatic error reporting and aggregation

## Security Features

- Certificate-based authentication (X.509)
- SAML integration via Red Hat SSO
- Organization-based isolation (org_id)
- Repository-level access control
- Content scanning with ClamAV

## Upstream Dependencies

This service is a Django plugin built on top of Pulp (Python-based repository management) and extends it with Red Hat-specific features for multi-tenancy, authentication, and cloud deployment.

> **Note**: Version numbers shown below are pinned in `pulp_service/requirements.txt`. Always check that file for the current authoritative versions in use.

### Pulpcore

- **Version**: 3.95.0 (see requirements.txt)
- **Repository**: https://github.com/pulp/pulpcore
- **Documentation**: https://docs.pulpproject.org/pulpcore/

**What Pulpcore Provides:**
- Core Django models: Content, Repository, Publication, Distribution, Domain
- REST API framework built on Django REST Framework
- Task queue system (Celery-based) for asynchronous operations
- Content storage abstraction (local filesystem, S3, Azure)
- Plugin API for extending functionality
- RBAC (Role-Based Access Control) system
- Content sync and versioning
- Artifact management and deduplication

**Plugin Integration Points:**
- Extends core models with plugin-specific models (RHCertGuardPermission, PushRepository, RHServiceAccount)
- Overrides authentication backends (RHServiceAccountCertAuthentication, RHSamlAuthentication)
- Adds custom middleware (ProfilerMiddleware, RhEdgeHostMiddleware, ActiveConnectionsMetricMiddleware)
- Extends storage backends (AIPCCStorageBackend, OCIStorageBackend)
- Adds custom viewsets for Red Hat-specific functionality
- Implements multi-domain support for tenant isolation

### Pulp Python Plugin

- **Version**: 3.22.1 (see requirements.txt)
- **Repository**: https://github.com/pulp/pulp_python
- **Documentation**: https://docs.pulpproject.org/pulp_python/

**What It Provides:**
- PyPI repository support (Simple API and JSON API)
- Python package content models (PythonPackageContent)
- Package upload and sync from PyPI
- Distribution and publication models for Python repositories

**Plugin Integration:**
- Custom domain-based routing: `/api/pypi/{domain}/{distribution}/simple/`
- Multi-tenant PyPI repositories with org_id isolation
- Download tracking for business metrics (RpmPackageDownload model)
- RHOAI distribution support with 50+ specialized distributions

### Pulp Container Plugin

- **Version**: 2.26.2 (see requirements.txt)
- **Repository**: https://github.com/pulp/pulp_container
- **Documentation**: https://docs.pulpproject.org/pulp_container/

**What It Provides:**
- OCI/Docker registry API (v2)
- Container image and manifest models
- Image layer storage and deduplication
- Push and pull operations
- Tag management

**Plugin Integration:**
- Custom storage backend (OCIStorageBackend) with manifest handling
- Token authentication disabled (PULP_TOKEN_AUTH_DISABLED=true)
- Certificate-based access control (RHCertGuardPermission)
- Multi-tenant container registries with domain isolation

### Pulp RPM Plugin

- **Version**: 3.32.5 (see requirements.txt)
- **Repository**: https://github.com/pulp/pulp_rpm
- **Documentation**: https://docs.pulpproject.org/pulp_rpm/

**What It Provides:**
- RPM/DNF repository support
- RPM package content models
- Repository metadata generation (repodata)
- Package signing support
- Modular content support

**Plugin Integration:**
- Download tracking for business metrics (RpmPackageDownload)
- Domain-based multi-tenant RPM repositories
- Custom distribution configurations

### Additional Pulp Plugins

**Pulp Gem** (0.7.3, see requirements.txt):
- RubyGems repository support
- Documentation: https://docs.pulpproject.org/pulp_gem/

**Pulp NPM** (0.4.0, see requirements.txt):
- NPM registry support
- Documentation: https://docs.pulpproject.org/pulp_npm/

**Pulp Maven** (0.11.0, see requirements.txt):
- Maven repository support
- Documentation: https://docs.pulpproject.org/pulp_maven/

**Pulp Hugging Face** (0.1.0, see requirements.txt):
- Hugging Face model repository support

### Key Architectural Boundaries

**Upstream Pulpcore Responsibilities:**
- Content storage and retrieval
- Task execution and queuing
- Basic RBAC and permissions
- Content versioning and snapshots
- Database models for content types
- Plugin extension points

**Plugin-Specific Responsibilities:**
- Red Hat SSO/SAML integration via X-RH-IDENTITY
- Multi-tenancy (org_id, domain-based isolation)
- Custom authentication (X.509 certificates, service accounts)
- OpenTelemetry metrics and profiling
- Business metrics and analytics
- ClamAV integration for content scanning
- Cloud-native deployment (OpenShift/Clowder)
- Custom access logging with correlation IDs

### Version Compatibility

This plugin requires:
- Python 3.9+ (tested on 3.11, 3.12)
- Django 4.2+
- PostgreSQL 15+
- Redis (for caching and Celery broker)

Upstream Pulp plugins are version-locked in requirements.txt to ensure compatibility. When upgrading Pulpcore or plugins, all dependencies should be upgraded together and thoroughly tested.
