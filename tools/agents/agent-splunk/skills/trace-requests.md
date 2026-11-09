# Tracing a failing request: Akamai → NSGW → Pulp

Every request to packages.redhat.com crosses three hops. A 5xx seen at the edge
can be emitted by any of them, so for every error group you report you MUST
identify the hop that emitted it and show the log evidence from each hop.

```
client → Akamai edge (Sumo Logic) → NSGW Envoy gateway (CloudWatch, source=nsgw) → pulp-api / pulp-content (CloudWatch, source=pulp)
```

All operations are read-only. Never guess at a cause the logs do not show.

## Hop 1: Akamai edge (tool: `sumo_search`)

The Akamai DataStream access logs are in Sumo Logic. Reference query for pulp 5xx errors:

```
_index=rh_akamai "packages.redhat.com"
| json auto
| where reqhost = "packages.redhat.com"
  and num(statuscode) >= 500
  and reqpath matches "*api/pulp*"
```

- Always add `| json auto` (each message is a JSON log line), and keep the `"packages.redhat.com"`
  keyword before the first pipe: it makes the search much faster.
- Field names are **lowercase**: `reqhost`, `reqpath`, `statuscode`. Values are strings, so wrap
  numbers in `num()` (e.g. `num(statuscode) >= 500`).
- `reqpath` has **no leading slash** (e.g. `api/pulp-content/...`). Prefix it with `/` when
  matching it against NSGW or Pulp logs. Match paths with `matches "*api/pulp*"` (wildcards), not regex.
- Other useful DataStream 2 fields, also lowercase: `reqtimesec` (epoch seconds), `reqmethod`,
  `querystr`, `cliip`, `ua`, `reqid`, `errorcode`, `turnaroundtimemsec`, `transfertimemsec`,
  `cachestatus`, `breadcrumbs`. **Before relying on any of them, run one query that returns a
  single message (`| limit 1`) and check which fields actually exist.** `_messagetime` is the
  event time (UTC).
- To build error groups, start with an aggregate:
  `... | count by statuscode, reqmethod, reqpath | sort by _count desc`. Then pull 1–3 sample
  messages per group by adding `and reqpath = "<path>"` to the `where` clause, with `| limit 3`.
- `errorcode` and `breadcrumbs` often show an edge-generated error (origin connect failure,
  origin timeout, edge read timeout). Note them; they are evidence for the classification.

## Hop 2: NSGW gateway (tool: `cloudwatch_trace_logs`, `source=nsgw`)

For each sample request, query a window of **±2 minutes around the Akamai timestamp**
(absolute `start_time`/`end_time` in UTC) with `query=trace` and `match=<request path
without query string>`. If that returns too many rows, narrow `match` to a more specific
path segment, or use a raw query filtering on both path and the client IP (Akamai `cliip`
appears in the Envoy `xff` field).

When you write a raw query that uses `parse`, choose the output columns with `display`, never
`fields`. Logs Insights rejects `fields <parsed field>` after a `parse` with
`MalformedQueryException: Ephemeral field is already defined`.

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
   a correlation id: tracebacks, gunicorn `WORKER TIMEOUT`, gunicorn worker restarts, OOM.
4. If there is no access line at all, the request likely died in the pod before it was
   logged (gunicorn worker timeout or kill, or OOM). Look for `WORKER TIMEOUT` and
   `Booting worker` on the pod that `upstream_host` points at.

"Worker" in the steps above means a **gunicorn worker process inside the pulp-api or
pulp-content pod**, not the `pulp-worker` deployment.

### pulp-worker: contributing cause, never the origin

`pulp-worker` pods run background tasks and never answer HTTP requests, so they cannot
emit the 5xx that Akamai saw:
- A **deferred** task runs on a pulp-worker. The API has already answered `202` with a task
  href, and a task failure only shows up as a failed task state, not as an HTTP 5xx.
- An **immediate** task runs inside the pulp-api process, so its traceback and the 5xx
  appear in the **pulp-api** logs (same correlation id) and the origin is `pulp-api`.

Do **not** treat these pulp-worker log lines as request errors:
- Task tracebacks or failed tasks with no matching failing user request.
- 5xx statuses the worker *received* from an upstream (remote sync, S3, other services). These
  are outbound failures, not responses to the client you are tracing.

A pulp-worker can still be the **root cause** of a pulp-api or pulp-content failure, e.g. a
long-running task holding resource locks or loading the database until API requests stall.
Only look at pulp-worker logs when the request failed with a timeout signature (NSGW `UT`/504
or `UC`/`upstream_reset_before_response_started`, a `WORKER TIMEOUT` on the API pod, or DB lock
or statement-timeout errors in the request's trace). Query them with `source=pulp` and a raw
query adding `filter @logStream like /pulp-worker/`, over the same ±2 minute window. Report
`Contributing cause: pulp-worker` **only** when both of these hold:
1. A worker error or long-running task in the **same domain or on the same repository** as the
   failing request overlaps the request's time window, and
2. The request itself shows one of the timeout signatures above.

Otherwise set `Contributing cause: none found`.

## Classification: where was the error emitted?

Decide the origin for each sample request with this table, then roll it up per error group.

| Evidence | Error origin |
|---|---|
| Akamai 5xx, **no** matching NSGW line in the window | `akamai-edge` (failed between the edge and the gateway: origin connect or DNS failure, edge timeout). Quote `errorcode` and `breadcrumbs` |
| NSGW status < 500 (2xx/3xx/4xx) but Akamai returned 5xx | `akamai-edge` (e.g. edge read timeout or a truncated response; compare `turnaroundtimemsec` with NSGW `duration`) |
| NSGW 5xx with `flags` `UH` (no healthy upstream) | `nsgw` (no ready pulp pods). Check whether the upstream cluster had zero endpoints |
| NSGW 5xx with `flags` `UF`, `URX`, `UO` | `nsgw` (connect failure, retries exhausted, or overflow toward the pulp pods) |
| NSGW 504 with `flags` `UT` | `nsgw` timeout waiting for Pulp. Pulp was **slow**, not erroring: report the Pulp endpoint and its latency |
| NSGW 5xx with `flags` `UC` or `details` `upstream_reset_before_response_started` | `pulp-api`/`pulp-content` pod dropped the connection (gunicorn worker timeout, crash, OOM). Confirm with Hop 3; check for a pulp-worker contributing cause |
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
### <statuscode> <METHOD> <endpoint pattern>   (count: N)
Error origin: akamai-edge | nsgw | pulp-api | pulp-content | unknown
Pulp service: pulp-api | pulp-content | n/a
Contributing cause: pulp-worker (<task type, domain, task id>) | none found
Confidence: high | medium | low

Trace (one sample request):
- Akamai   <time UTC>  status=<..> errorcode=<..> turnaround=<..>ms
- NSGW     <time UTC>  status=<..> flags=<..> details=<..> upstream=<cluster>/<pod-ip or -> duration=<..>ms
- Pulp     <time UTC>  pod=<pod> status=<..> correlation_id=<..> time=<..>ms
- Pulp error: <exception type and message, or the first relevant traceback line>
- Worker   <time UTC>  pod=<pod> task=<task type/id> domain=<..> error=<..>   (only if a contributing cause was found)

Why: <one or two sentences tying the evidence to the origin>
```

Also include the environment, the Sumo Logic time range, and the CloudWatch windows you queried.
Redact credentials, tokens, `X-RH-IDENTITY` values, and `x-akamai-origin-secret` values if any
appear in the logs.
