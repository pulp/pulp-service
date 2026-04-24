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

## Dev Container: hosted-pulp-dev-env

A self-contained development container with PostgreSQL, Redis, and all Pulp services running locally. Built automatically on every push to any branch.

### Image Location

```
ghcr.io/pulp/hosted-pulp-dev-env:<branch-name>
```

Branch names are sanitized for Docker tags: `/` is replaced with `-` (e.g., branch `feature/foo` produces tag `feature-foo`).

### Running the Container

Mount the shared workspace volume at `/workspace` per alcove conventions:

```bash
docker run -d \
  -v workspace:/workspace \
  -p 24817:24817 \
  -p 24816:24816 \
  --name pulp-dev \
  ghcr.io/pulp/hosted-pulp-dev-env:main
```

If the container finds a pulp-service checkout at `/workspace/pulp-service`, it installs it in development mode (`pip install -e`) at startup. Because `PULP_GUNICORN_RELOAD` is enabled by default, Gunicorn watches for Python file changes and automatically reloads -- you do not need to run `pulp-restart` after editing source files in most cases.

### Services

| Service      | Port  | Description                              |
|------------- |-------|------------------------------------------|
| pulp-api     | 24817 | Django REST API (Gunicorn WSGI)          |
| pulp-content | 24816 | Async content delivery (Gunicorn aiohttp)|
| pulp-worker  | --    | Celery background task worker            |
| PostgreSQL   | 5432  | Database (local, trust auth)             |
| Redis        | 6379  | Cache and Celery broker                  |

All five services are managed by supervisord. The `pulp-restart` command only restarts Pulp services (api, content, worker), not PostgreSQL or Redis.

### Configuration

The dev container has domain support enabled (`DOMAIN_ENABLED=True`) and token authentication disabled (`TOKEN_AUTH_DISABLED=True`).

#### Environment Variables

Override these at `docker run` time with `-e`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PULP_DEFAULT_ADMIN_PASSWORD` | `password` | Admin user password set during initialization |
| `PULP_API_WORKERS` | `2` | Number of Gunicorn workers for the API |
| `PULP_CONTENT_WORKERS` | `2` | Number of Gunicorn workers for the content app |
| `PULP_WORKERS` | `2` | Number of Celery worker processes |
| `PULP_GUNICORN_TIMEOUT` | `90` | Gunicorn request timeout in seconds |
| `PULP_GUNICORN_RELOAD` | `true` | Auto-reload Gunicorn on Python file changes |

### Dev Container Commands

**Patch Management:**

```bash
pulp-add-patch /path/to/my-fix.patch      # Apply a patch to pulpcore site-packages
pulp-remove-patch /path/to/my-fix.patch   # Reverse a patch
pulp-restart                               # Restart after patching (required)
```

**Service Management:**

```bash
pulp-restart              # Restart all Pulp services
pulp-restart api          # Restart pulp-api only
pulp-restart content      # Restart pulp-content only
pulp-restart worker       # Restart pulp-worker only
supervisorctl status      # Check all service status
```

You need `pulp-restart` after applying/removing patches, changing `/etc/pulp/settings.py`, or modifying Celery task definitions. Regular Python source edits auto-reload via Gunicorn.

**Running Tests:**

```bash
pulp-test                                    # Run all functional tests
pulp-test path/to/test_file.py               # Run specific test file
pulp-test path/to/test_file.py::TestClass    # Run specific test class
```

**Database Access:**

```bash
runuser -u pulp -- psql -d pulp                     # PostgreSQL shell
runuser -u pulp -- pulpcore-manager showmigrations  # Check migrations
runuser -u pulp -- pulpcore-manager shell           # Django shell
```

**Admin Credentials:** admin / password (override with `PULP_DEFAULT_ADMIN_PASSWORD`)

### Alcove Integration

When used with alcove, the `/workspace` volume is automatically shared between the Skiff container (running Claude Code) and this dev container. Repositories are cloned into `/workspace/<name>/`. The dev container image is declared in the alcove agent definition via `dev_container.image`.
