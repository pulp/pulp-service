# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pulp-service is a **Django REST Framework plugin for Pulpcore** that extends the Pulp content management platform with Red Hat cloud-specific features: multi-tenant authentication (X-RH-IDENTITY), custom S3/OCI storage backends, domain-based org isolation, content guards, vulnerability reporting, and OpenTelemetry observability.

The plugin is registered via the `pulpcore.plugin` entry point in `pulp_service/setup.py`.

## Repository Layout

- `pulp_service/` — The Python package (all source code lives here)
  - `pulp_service/app/` — Core Django app: models, viewsets, serializers, middleware, auth, storage, tasks
  - `pulp_service/tests/functional/` — Functional tests (pytest + pytest-django)
  - `setup.py`, `requirements.txt` — Package definition and dependencies
- `images/` — Container build assets (startup scripts for pulp-api, pulp-content, pulp-worker; WSGI middleware)
- `deploy/` — OpenShift ClowdApp deployment manifests
- `docs/ARCHITECTURE.md` — Comprehensive architecture reference (read this for deep context)
- `CHANGES/` — Towncrier changelog fragments

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
```

Test dependencies: `pytest`, `pytest-django` (see `unittest_requirements.txt` / `functest_requirements.txt`).

## Architecture

**Three-service model:**
1. **pulp-api** — Gunicorn WSGI serving Django REST API (port 24817 local, 8000 prod)
2. **pulp-content** — Gunicorn + aiohttp async content delivery (port 24816 local, 8000 prod)
3. **pulp-worker** — Celery workers for background tasks (Redis broker)

**Request flow:**
WSGI middleware (`images/assets/log_middleware.py`) → Django middleware stack (`app/middleware.py`) → DRF ViewSets (`app/viewsets.py`)

**Key patterns:**
- **Authentication**: `X-RH-IDENTITY` header (base64-encoded JSON) → custom auth classes in `app/authentication.py`
- **Multi-tenancy**: `DomainOrg` model maps org_id → Pulp domain; domain-based routing for content APIs
- **Context variables**: `ContextVar` instances in `app/middleware.py` carry request-scoped data (org_id, user_id, request_path) across layers
- **Storage backends**: `AIPCCStorageBackend` (S3) and `OCIStorageBackend` (OCI/ORAS) in `app/storage.py`
- **Tasks**: Background work in `app/tasks/` (package scanning, domain metrics, RDS testing)

**Upstream plugins this extends**: pulpcore, pulp-python, pulp-container, pulp-rpm, pulp-gem, pulp-npm, pulp-maven, pulp-hugging-face.

## Changelog Process

Uses **towncrier**. For any non-trivial change, create a file in `CHANGES/` named `{issue_number}.{category}` where category is one of: `feature`, `bugfix`, `doc`, `removal`, `deprecation`, `misc`.

## Code Style

- **Black** formatter, line length 100, targeting py36/py37
- Excludes: migrations, docs, build directories
