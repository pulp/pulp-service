# PyPI Yank Monitor

The PyPI Yank Monitor checks Python packages in your Pulp repositories against public PyPI to detect yanked versions. When a package version is yanked on PyPI (for example, due to a security vulnerability or supply chain attack), the monitor identifies matching packages in your local repositories and generates a report.

Monitors run automatically on a daily schedule. You can also trigger a check on demand.

## How monitoring works

A daily scheduled task dispatches a yank check for every registered monitor. The task queries the PyPI JSON API for each package in the monitored repository and compares the yank status of each version. Results are stored as a report linked to the monitor.

Yank monitors are **not scoped to a Pulp domain**. Although you access the API through a domain-specific path (for example, `/api/pulp/public-rhai/api/v3/`), monitors are stored globally. The daily scheduled task runs from the `default` domain and processes all monitors regardless of which domain path was used to create them.

> **Note**: The `pulp_href` returned for a monitor always uses the `default` domain path (for example, `/api/pulp/default/api/v3/pypi_yank_monitor/...`), even when you create the monitor through a different domain path. Use the returned `pulp_href` for subsequent operations on that monitor.

## Prerequisites

Before proceeding, ensure that you have:

1. **Superuser access** to the Pulp instance. The yank monitor API requires superuser permissions.
2. **A Python repository** in your Pulp domain with content to monitor.
3. **API credentials** configured. See the [API access guide](https://gitlab.cee.redhat.com/hosted-pulp/pulp-docs/-/blob/main/api-access.md) for authentication options.

## Getting started

### Define environment variables

Set the following variables to simplify the commands in this guide:

```bash
BASE_URL=https://packages.redhat.com
DOMAIN=public-rhai
API_BASE=${BASE_URL}/api/pulp/${DOMAIN}/api/v3
AUTH="-u '11009103|myuser:eyJhbGciOi...'"
```

Replace `DOMAIN` with your Pulp domain name and the credentials with your own Terms-Based Registry credentials.

### Find the repository to monitor

List the Python repositories in your domain:

```bash
curl ${AUTH} ${API_BASE}/repositories/python/python/
```

Note the `pulp_href` value of the repository that you want to monitor. For example:

```
/api/pulp/public-rhai/api/v3/repositories/python/python/01234567-89ab-cdef-0123-456789abcdef/
```

## Creating a yank monitor

Create a monitor by specifying a name and the repository `pulp_href`:

```bash
curl ${AUTH} -X POST ${API_BASE}/pypi_yank_monitor/ \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "yank-monitor-my-repo",
    "repository": "/api/pulp/public-rhai/api/v3/repositories/python/python/01234567-89ab-cdef-0123-456789abcdef/"
  }'
```

The response includes the monitor `pulp_href`:

```json
{
  "pulp_href": "/api/pulp/default/api/v3/pypi_yank_monitor/abcdef01-2345-6789-abcd-ef0123456789/",
  "name": "yank-monitor-my-repo",
  "repository": "/api/pulp/public-rhai/api/v3/repositories/python/python/01234567-89ab-cdef-0123-456789abcdef/",
  "repository_version": null,
  "last_checked": null
}
```

You can also pin the monitor to a specific repository version instead of the latest by using `repository_version` instead of `repository`. Exactly one of the two fields must be specified.

## Triggering a yank check

Monitors run automatically once per day. To trigger an on-demand check, send a POST request to the monitor `check` endpoint:

```bash
curl ${AUTH} -X POST \
  ${API_BASE}/pypi_yank_monitor/abcdef01-2345-6789-abcd-ef0123456789/check/
```

The response returns a `202 Accepted` with a task reference:

```json
{
  "task": "/api/pulp/public-rhai/api/v3/tasks/fedcba98-7654-3210-fedc-ba9876543210/"
}
```

You can check the task status:

```bash
curl ${AUTH} ${API_BASE}/tasks/fedcba98-7654-3210-fedc-ba9876543210/
```

## Viewing yank reports

After a check completes, retrieve the latest report for a monitor:

```bash
curl ${AUTH} \
  ${API_BASE}/pypi_yank_monitor/abcdef01-2345-6789-abcd-ef0123456789/report/
```

The response includes the report with any yanked packages found:

```json
{
  "pulp_created": "2026-04-13T10:30:00.000000Z",
  "pulp_last_updated": "2026-04-13T10:30:00.000000Z",
  "report": {
    "attrs==21.1.0": {
      "yanked_reason": "This version has been yanked due to a security issue."
    }
  },
  "repository_name": "my-repo"
}
```

The `report` field is a JSON object where each key is a package in `name==version` format. If no yanked packages are found, the `report` field is an empty object (`{}`).

If no check has been run yet, the endpoint returns a `404 Not Found`.

## Listing and managing monitors

### List all monitors

```bash
curl ${AUTH} ${API_BASE}/pypi_yank_monitor/
```

### Filter monitors by name

```bash
curl ${AUTH} "${API_BASE}/pypi_yank_monitor/?name=yank-monitor-my-repo"
```

### Filter monitors by repository

```bash
curl ${AUTH} "${API_BASE}/pypi_yank_monitor/?repository=/api/pulp/public-rhai/api/v3/repositories/python/python/01234567-89ab-cdef-0123-456789abcdef/"
```

### Delete a monitor

```bash
curl ${AUTH} -X DELETE \
  ${API_BASE}/pypi_yank_monitor/abcdef01-2345-6789-abcd-ef0123456789/
```

## API reference

All endpoints use the base path `/api/pulp/{domain}/api/v3/`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `pypi_yank_monitor/` | `GET` | List all monitors (supports filtering by `name`, `repository`, `pulp_label_select`) |
| `pypi_yank_monitor/` | `POST` | Create a new monitor |
| `pypi_yank_monitor/{id}/` | `GET` | Retrieve a specific monitor |
| `pypi_yank_monitor/{id}/` | `DELETE` | Delete a monitor |
| `pypi_yank_monitor/{id}/check/` | `POST` | Trigger an on-demand yank check (returns 202 with task) |
| `pypi_yank_monitor/{id}/report/` | `GET` | Retrieve the latest yank report (returns 404 if no report exists) |

All endpoints require **superuser** permissions.
