# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pulp-service is a **Django REST Framework plugin for Pulpcore** that extends the Pulp content management platform with Red Hat cloud-specific features: multi-tenant authentication (X-RH-IDENTITY), custom S3/OCI storage backends, domain-based org isolation, content guards, vulnerability reporting, and OpenTelemetry observability.

The plugin is registered via the `pulpcore.plugin` entry point in `pulp_service/setup.py`.

Upstream: https://pulpproject.org/ | Source: https://github.com/pulp/pulp-service

## Repository Layout

- `pulp_service/` — The Python package (all source code lives here)
  - `pulp_service/app/` — Core Django app: models, viewsets, serializers, middleware, auth, storage, tasks
  - `pulp_service/tests/functional/` — Functional tests (pytest + pytest-django)
  - `setup.py`, `requirements.txt` — Package definition and dependencies
- `images/` — Container build assets (startup scripts for pulp-api, pulp-content, pulp-worker; WSGI middleware)
- `deploy/` — OpenShift ClowdApp deployment manifests
- `docs/ARCHITECTURE.md` — Comprehensive architecture reference (read this for deep context)
- `CHANGES/` — Towncrier changelog fragments

## Quick Reference Index

### Core Plugin Code

| Component | File | Description |
|-----------|------|-------------|
| **Settings** | `pulp_service/app/settings.py` | Django settings overrides |
| **Models** | `pulp_service/app/models.py` | DomainOrg, FeatureContentGuard, VulnerabilityReport |
| **ViewSets** | `pulp_service/app/viewsets.py` | REST API endpoints |
| **Serializers** | `pulp_service/app/serializers.py` | DRF serializers |
| **Authentication** | `pulp_service/app/authentication.py` | X.509 cert, SAML, registry auth backends |
| **Authorization** | `pulp_service/app/authorization.py` | Domain-based RBAC permissions |
| **Middleware** | `pulp_service/app/middleware.py` | Profiler, edge host, SAML, OTEL metrics |
| **Storage** | `pulp_service/app/storage.py` | AIPCCStorageBackend, OCIStorageBackend |
| **Signals** | `pulp_service/app/signals.py` | User creation, domain creation hooks |
| **Tasks** | `pulp_service/app/tasks/` | Package scanning, domain metrics |
| **Content** | `pulp_service/app/content.py` | aiohttp middleware for pulp-content |
| **Admin** | `pulp_service/app/admin.py` | Django admin (Users, Groups, Domains, Tasks) |

### Deployment & Config

| Component | File | Description |
|-----------|------|-------------|
| **ClowdApp** | `deploy/clowdapp.yaml` | Production deployment (5 deployments + jobs) |
| **Dockerfile** | `Dockerfile` | Container image (UBI9, Python 3.11 venv) |
| **WSGI Middleware** | `images/assets/log_middleware.py` | User extraction from X-RH-IDENTITY |
| **Gunicorn Config** | `images/assets/gunicorn_config.py` | Gunicorn hooks and middleware |
| **Dependencies** | `pulp_service/requirements.txt` | Pinned plugin versions |
| **Patches** | `images/assets/patches/` | Upstream plugin patches applied at build time |

## Architecture

**Three-service model:**
1. **pulp-api** — Gunicorn WSGI serving Django REST API (port 24817 local, 8000 prod)
2. **pulp-content** — Gunicorn + aiohttp async content delivery (port 24816 local, 8000 prod)
3. **pulp-worker** — Celery workers for background tasks (Redis broker)

**Request flow:**
```
Client -> Load Balancer (sets X-Forwarded-For, X-RH-IDENTITY)
  -> Gunicorn
    -> WSGI: UserExtractionMiddleware (extracts user/org_id from X-RH-IDENTITY)
      -> Django Middleware Stack
        -> DRF ViewSets
          -> Response
```

**Key patterns:**
- **Authentication**: `X-RH-IDENTITY` header (base64-encoded JSON) → custom auth classes in `app/authentication.py`
- **Multi-tenancy**: `DomainOrg` model maps org_id → Pulp domain; domain-based routing for content APIs
- **Context variables**: `ContextVar` instances in `app/middleware.py` carry request-scoped data (org_id, user_id, request_path) across layers
- **Storage backends**: `AIPCCStorageBackend` (S3) and `OCIStorageBackend` (OCI/ORAS) in `app/storage.py`
- **Tasks**: Background work in `app/tasks/` (package scanning, domain metrics, RDS testing)

**Upstream plugins this extends**: pulpcore, pulp-python, pulp-container, pulp-rpm, pulp-gem, pulp-npm, pulp-maven, pulp-hugging-face.

## Middleware Stack

**WSGI** (`images/assets/log_middleware.py`):
1. **UserExtractionMiddleware** — Decodes base64 X-RH-IDENTITY, sets `REMOTE_USER` and `ORG_ID`

**Django** (`pulp_service/app/middleware.py`):
1. **ProfilerMiddleware** — cProfile on `X-Profile-Request` header
2. **RhEdgeHostMiddleware** — Maps `X-RH-EDGE-HOST` to `X-FORWARDED-HOST`
3. **RHSamlAuthHeaderMiddleware** — Auth for `/pulp-mgmt/` paths
4. **RequestPathMiddleware** — Stores path in ContextVar for signals
5. **ActiveConnectionsMetricMiddleware** — OTEL concurrent connection tracking

**aiohttp** (`pulp_service/app/content.py`):
1. **add_rh_org_id_resp_header** — Adds `X-RH-ORG-ID` response header

## Authentication Classes

1. **RHServiceAccountCertAuthentication** — X.509 cert via X-RH-IDENTITY
2. **RHTermsBasedRegistryAuthentication** — Registry auth from standard RH identity (`identity.user`)
3. **TurnpikeTermsBasedRegistryAuthentication** — Registry auth from Turnpike identity (`identity.registry`)
4. **RHSamlAuthentication** — SAML via X-RH-IDENTITY
5. **SessionAuthentication** — Django sessions (upstream)
6. **BasicAuthentication** — HTTP Basic (upstream)

X-RH-IDENTITY header: base64-encoded JSON. Standard format:
`{"identity": {"org_id": "123456", "user": {"username": "..."}}}`.
Turnpike registry format:
`{"identity": {"type": "Registry", "auth_type": "registry-auth", "registry": {"org_id": "...", "username": "..."}}}`.

## Context Variables

- `org_id_var` — Organization ID (set by authorization)
- `user_id_var` — User ID (set by authorization)
- `request_path_var` — Request path (set by RequestPathMiddleware)
- `repository_name_var` — Current repository name
- `x_quay_auth_var` — Quay authentication token
- `x_task_diagnostics_var` — Task diagnostics flag

## Key Environment Variables

- `PULP_AUTHENTICATION_JSON_HEADER=HTTP_X_RH_IDENTITY`
- `PULP_OTEL_ENABLED=true` / `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:10000/`
- `PULP_CLAMAV_HOST` / `PULP_CLAMAV_PORT=10000`
- `PULP_FEATURE_SERVICE_API_URL` / `PULP_FEATURE_SERVICE_API_CERT_PATH`
- `PULP_UVLOOP_ENABLED=false`
- `SENTRY_DSN` (optional)

Full env var reference: `docs/ARCHITECTURE.md`

## Common Development Tasks

- **New API endpoint**: viewset in `viewsets.py`, serializer in `serializers.py`, register URL
- **Custom auth**: class in `authentication.py`, register in Django settings
- **New middleware**: class in `middleware.py`, register in `PULP_MIDDLEWARE`
- **Background task**: create in `tasks/`, call from viewset or signal
- **Modify logging**: update `--access-logformat` in `images/assets/pulp-api` AND `deploy/clowdapp.yaml`
- **Add metrics**: use `init_otel_meter()` from `pulpcore.metrics`
- **New model**: add to `models.py`, run `pulpcore-manager makemigrations`

## Build and Development Commands

```bash
# Install in development mode
cd pulp_service && pip install -e .

# Format code (line length 100)
black --line-length 100 pulp_service/

# Run all functional tests
pytest pulp_service/pulp_service/tests/functional/

# Run a single test file
pytest pulp_service/pulp_service/tests/functional/test_authentication.py

# Run a single test
pytest pulp_service/pulp_service/tests/functional/test_authentication.py::TestClass::test_method

# Build container image
docker build -t pulp-service .

# Ephemeral deployment
bonfire namespace reserve --duration 8h
bonfire deploy-env -n <namespace> --template-file deploy/clowdapp.yaml
```

Test dependencies: `pytest`, `pytest-django` (see `unittest_requirements.txt` / `functest_requirements.txt`).

## Code Style

- **Formatter**: Black, line-length 100
- **Testing**: pytest + pytest-django
- **Plugin pattern**: models, serializers, viewsets, tasks (same as all Pulp plugins)
- Entry point: `pulp_service = pulp_service:default_app_config`

## Changelog Process

Uses **towncrier**. For any non-trivial change, create a file in `CHANGES/` named `{issue_number}.{category}` where category is one of: `feature`, `bugfix`, `doc`, `removal`, `deprecation`, `misc`.

## Task Completion Checklist

1. `black --line-length 100` on changed files
2. `pytest pulp_service/pulp_service/tests/functional/` on relevant tests
3. Towncrier entry in `CHANGES/` for non-trivial changes
4. `pulpcore-manager makemigrations` if models changed
5. Check `images/assets/patches/` if modifying upstream plugin behavior
6. Sync changes between `images/assets/pulp-*` scripts and `deploy/clowdapp.yaml`

## Full Reference

For deployment specs, ClowdApp breakdown, resource limits, and complete env var documentation see `docs/ARCHITECTURE.md`.
