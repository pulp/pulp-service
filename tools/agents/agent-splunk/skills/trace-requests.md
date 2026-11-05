# Tracing a failing request: Akamai → NSGW → Pulp

Every request to packages.redhat.com crosses three hops. A 5xx seen at the edge
can be emitted by any of them, so for every error group you report you MUST
identify the hop that emitted it and show the log evidence from each hop.

```
client → Akamai edge (Splunk) → NSGW Envoy gateway (CloudWatch, source=nsgw) → pulp-api / pulp-content (CloudWatch, source=pulp)
```

All operations are read-only. Never guess at a cause the logs do not show.

## Hop 1: Akamai edge (tool: `splunk_search`)

- Index: `index=federated:rh_akamai sourcetype=datastream:access reqHost=packages.redhat.com`
- `reqPath` has **no leading slash** (e.g. `api/pulp-content/...`). Prefix it with `/` when
  matching it against NSGW or Pulp logs.
- Useful fields (DataStream 2; confirm they exist on a sample event before relying on them):
  `_time`, `reqTimeSec` (epoch seconds), `reqMethod`, `reqPath`, `queryStr`, `statusCode`,
  `cliIP`, `UA`, `reqId`, `errorCode`, `turnAroundTimeMSec`, `transferTimeMSec`, `cacheStatus`,
  `breadcrumbs`.
- Start with an aggregate (`| stats count by statusCode, reqMethod, reqPath`) to build error
  groups, then pull 1–3 sample events per group with `| table _time reqTimeSec reqMethod reqPath statusCode cliIP UA errorCode turnAroundTimeMSec`.
- `errorCode` and `breadcrumbs` often show an edge-generated error (origin connect failure,
  origin timeout, edge read timeout). Note them; they are evidence for the classification.

## Hop 2: NSGW gateway (tool: `cloudwatch_trace_logs`, `source=nsgw`)

For each sample request, query a window of **±2 minutes around the Akamai timestamp**
(absolute `start_time`/`end_time` in UTC) with `query=trace` and `match=<request path
without query string>`. If that returns too many rows, narrow `match` to a more specific
path segment, or use a raw query filtering on both path and the client IP (Akamai `cliIP`
appears in the Envoy `xff` field).

If the parsed fields come back empty, the log format changed: run `query=raw` with the same
`match`, read the raw lines, and say in your report that the parse regex needs updating.

The Envoy fields:

| Field | Meaning |
|---|---|
| `status` | Status Envoy returned to Akamai |
| `flags` | Envoy response flags; `-` means none (the upstream answered) |
| `details` | Response code details (e.g. `via_upstream`, `ext_authz_denied`, `upstream_reset_before_response_started{...}`) |
| `upstream_host` | Pulp pod IP:port; `-` means no upstream was contacted |
| `upstream_cluster` | Target service (`pulp-api` or `pulp-content`) |
| `duration` / `upstream_time` | Total time / upstream time in ms |
| `request_id` | Envoy x-request-id |

Pulp routes behind the gateway:
- `pulp-api:8000`: `/api/pulp/`, `/api/pypi/`, `/v2`, `/extensions/v2`, `/trusted-libraries`,
  `/lightwell/python`, `/lightwell/network/python`
- `pulp-content:8000`: `/api/pulp-content/`, `/pulp/container/`

## Hop 3: Pulp application (tool: `cloudwatch_trace_logs`, `source=pulp`)

Only go here when the NSGW line shows the request reached a Pulp pod (`upstream_host` is
a pod IP), or when no NSGW line was found and you need to rule Pulp out.

1. `query=trace`, `match=<path>`, a window of ±2 minutes. Pick the access line with the same
   method, path, and status that is closest in time to the NSGW line.
   - pulp-api line: `pulp [<correlation-id>]: <ip> - user:<id> org_id:<org> [<ts>] "<METHOD> <path> HTTP/1.0" <status> ... <time_ms> x_forwarded_for:"..."`
   - pulp-content line: `<ip> [<ts>] "<METHOD> <path> HTTP/1.1" <status> ... rh_org_id:"..." x_forwarded_for:"..."`
2. For pulp-api, take the `correlation-id` from the access line and run `query=trace`,
   `match=<correlation-id>` to get the traceback or error lines for that request.
3. Run `query=errors` over a narrow window (±1 minute) on the same pod
   (raw query adding `filter kubernetes.pod_name = "<pod>"`) to catch errors logged without
   a correlation id: tracebacks, `WORKER TIMEOUT`, worker restarts, OOM.
4. If there is no access line at all, the request likely died in the pod before it was
   logged (gunicorn worker timeout or kill, or OOM). Look for `WORKER TIMEOUT` and
   `Booting worker` on the pod that `upstream_host` points at.

## Classification: where was the error emitted?

Decide the origin for each sample request with this table, then roll it up per error group.

| Evidence | Error origin |
|---|---|
| Akamai 5xx, **no** matching NSGW line in the window | `akamai-edge` (failed between the edge and the gateway: origin connect or DNS failure, edge timeout). Quote `errorCode` and `breadcrumbs` |
| NSGW status < 500 (2xx/3xx/4xx) but Akamai returned 5xx | `akamai-edge` (e.g. edge read timeout or a truncated response; compare `turnAroundTimeMSec` with NSGW `duration`) |
| NSGW 5xx with `flags` `UH` (no healthy upstream) | `nsgw` (no ready pulp pods). Check whether the upstream cluster had zero endpoints |
| NSGW 5xx with `flags` `UF`, `URX`, `UO` | `nsgw` (connect failure, retries exhausted, or overflow toward the pulp pods) |
| NSGW 504 with `flags` `UT` | `nsgw` timeout waiting for Pulp. Pulp was **slow**, not erroring: report the Pulp endpoint and its latency |
| NSGW 5xx with `flags` `UC` or `details` `upstream_reset_before_response_started` | `pulp-api`/`pulp-content` pod dropped the connection (worker timeout, crash, OOM). Confirm with Hop 3 |
| NSGW 5xx with `details` `ext_authz_error` | `nsgw` (CAPS ext_authz unreachable or erroring) |
| NSGW 5xx with `flags` `-`, `details` `via_upstream`, `upstream_host` a pod IP | `pulp-api` or `pulp-content` (per `upstream_cluster`). Pulp emitted it; quote the Pulp access line and traceback |
| NSGW status 0 / `DC` | Client or Akamai disconnected. Not a server error unless it correlates with slow upstream times |

If CloudWatch is unavailable (tool missing, credential or permission errors), say so
explicitly and set the origin to `unknown (trace unavailable)`. Do not infer it from the
Akamai data alone.

## Budget

Each CloudWatch query takes 5–30 s. Trace at most **5 error groups**, ordered by volume, and
at most **2 sample requests per group**. If more than 2 samples in a group already agree on
the origin, stop tracing that group.

## Report format

For each error group:

```
### <statusCode> <METHOD> <endpoint pattern>   (count: N)
Error origin: akamai-edge | nsgw | pulp-api | pulp-content | pulp-worker | unknown
Pulp service: pulp-api | pulp-content | pulp-worker | n/a
Confidence: high | medium | low

Trace (one sample request):
- Akamai   <time UTC>  status=<..> errorCode=<..> turnAround=<..>ms
- NSGW     <time UTC>  status=<..> flags=<..> details=<..> upstream=<cluster>/<pod-ip or -> duration=<..>ms
- Pulp     <time UTC>  pod=<pod> status=<..> correlation_id=<..> time=<..>ms
- Pulp error: <exception type and message, or the first relevant traceback line>

Why: <one or two sentences tying the evidence to the origin>
```

Also include the environment, the Splunk time range, and the CloudWatch windows you queried.
Redact credentials, tokens, `X-RH-IDENTITY` values, and `x-akamai-origin-secret` values if any
appear in the logs.
