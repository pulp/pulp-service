# Diagnostic Tool Routing Guide

Use this guide to decide which diagnostic tools to call based on the
alert type. Tools are listed in priority order — call Primary tools
first, Secondary only if Primary doesn't provide enough context.

## Tool Cost Reference
- k8s_get_pod_info: Fast (< 1s). Kubernetes API.
- prometheus_query: Fast (< 2s). Use shortcuts when possible.
- glitchtip_get_errors: Fast (< 2s). Application error tracking.
- cloudwatch_query_logs: Slow (15-30s). Use as last resort.

## OOMKill / Crash alerts (PulpOOMKilled, PulpCrashing)

Primary: k8s_get_pod_info (pod status, OOMKilled reason, restart count,
container last state, resource limits vs requests, Warning events),
cloudwatch_query_logs (shortcut: "errors" — shows what the process was
doing before the kernel killed it: sync tasks, large queries, memory
allocation patterns. The kernel kills without warning, so logs are the
only record of what led to the OOM.)

Secondary: prometheus_query (shortcut: "connections" for connection
pressure context, or "request-rate" for traffic spikes)

Usually unnecessary: glitchtip_get_errors (OOMKill doesn't give the
app a chance to report exceptions)

## High error rate (PulpApiError*BudgetBurn, PulpContentError*BudgetBurn)

Primary: glitchtip_get_errors (application exceptions correlated with
alert time), prometheus_query (shortcut: "errors" for error rate trend)

Secondary: cloudwatch_query_logs (shortcut: "5xx" for stack traces and
correlation IDs when GlitchTip doesn't have enough detail)

Usually unnecessary: k8s_get_pod_info

## Service down (PulpApiDown, PulpContentDown, PulpWorkerDown)

Primary: k8s_get_pod_info (pod phase, scheduling events, container
status, node assignment)

Secondary: prometheus_query (shortcut: "health" to confirm which pods
are down), cloudwatch_query_logs (shortcut: "errors" — startup failures,
crash logs before the pod went down)

Usually unnecessary: glitchtip_get_errors

## RDS storage (PulpProdServiceRDSLowStorageSpace)

Primary: cloudwatch_query_logs (shortcut: "errors" for storage-related
failures in application logs)

Secondary: prometheus_query (custom query for RDS metrics if available)

Usually unnecessary: k8s_get_pod_info, glitchtip_get_errors

## Log analysis hints

When calling cloudwatch_query_logs, use the "errors" shortcut first.
If it returns nothing useful, try a raw query targeting these patterns
based on the alert type.

### OOMKill / Crash
Look for the last activity before the crash timestamp:
- Large request bodies: sync tasks, bulk content uploads, RPM/container
  image pushes that load entire manifests into memory
- Task dispatch: pulp-worker picking up orphan cleanup, reclaim space,
  or repair tasks that process large querysets
- Connection pool warnings: "closing idle connection", "connection pool
  exhausted", database timeout errors
- Gunicorn worker lifecycle: "worker booting", "worker exiting (SIGKILL)"
- The last 5 API requests before the crash — which endpoint, which org

### High error rate
Look for the root exception driving the error budget burn:
- Exception stack traces with the originating file and line
- HTTP 5xx responses with correlation IDs (X-Request-ID)
- Database errors: OperationalError, InterfaceError, connection refused
- Upstream failures: timeout connecting to S3, registry, or Akamai
- Rate limiting or auth failures from external services

### Service down
Look for why the process failed to start or stay running:
- Startup failures: ImportError, ModuleNotFoundError, missing migration
- Config errors: missing env vars, malformed YAML/JSON, secret not found
- Migration failures: django.db.utils errors during migrate
- Readiness probe context: what was the app doing when the probe failed
- Gunicorn master process logs: "worker failed to boot"

### RDS storage
Look for operations that consume disk rapidly:
- Large COPY/INSERT operations, bulk content imports
- Vacuum/analyze operations that temporarily need extra space
- Orphan content cleanup generating large temp tables
- Replication lag or WAL growth indicators
