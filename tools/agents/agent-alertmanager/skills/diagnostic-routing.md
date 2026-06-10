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
container last state, resource limits vs requests, Warning events)

Secondary: prometheus_query (shortcut: "connections" for connection
pressure context, or "request-rate" for traffic spikes)

Usually unnecessary: cloudwatch_query_logs, glitchtip_get_errors

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
are down)

Usually unnecessary: glitchtip_get_errors

## RDS storage (PulpProdServiceRDSLowStorageSpace)

Primary: cloudwatch_query_logs (shortcut: "errors" for storage-related
failures in application logs)

Secondary: prometheus_query (custom query for RDS metrics if available)

Usually unnecessary: k8s_get_pod_info, glitchtip_get_errors
