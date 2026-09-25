# Content guard audit

The content guard audit creates a read-only inventory of Pulp domains,
distributions, and content guards. Use the report to identify distributions
that Pulp reports as unguarded. The report does not determine whether Akamai,
the North-South Gateway (NSWG), a VPN proxy, or another network path protects a
distribution.

## Run the audit

Before running the audit, configure `~/.netrc` with credentials for the target
Pulp host. The tool supports `stage` and `prod` and excludes domains whose names
start with `public-` by default.

```bash
uv run management_tools/audit-content-guards.py \
  --env stage \
  --output content-guards-stage.json \
  --failed-operations-output content-guards-stage-failed.json
```

Use `--env prod` to target production. Use `--include-public` to include
`public-*` domains. The script uses the system OpenSSL trust store before the
certificate authority (CA) bundle provided with Requests. If the system trust
store does not contain the required Red Hat CA, set `REQUESTS_CA_BUNDLE` to an
approved CA bundle.

The tool discovers typed distribution collection endpoints from the Pulp
OpenAPI schema. It skips the generic `/distributions/` aggregation endpoint,
which can return HTTP 500 for stale or malformed subtype records. The typed
endpoints include the distribution types advertised by the target Pulp
instance.

### Concurrency, timeouts, and retries

The tool audits domains sequentially by default. Set `--workers` to a value from
1 through 4 to enable bounded concurrency. The aggregate request-rate defaults
to four requests per second in stage and two requests per second in production;
override it with `--request-rate`. Use `--quiet` to suppress progress messages.

Ordinary Pulp API requests use a 30-second timeout. Guard detail requests use a
separate 120-second timeout because some guard types, including RBAC guards,
can take longer to retrieve. Override it with `--guard-detail-timeout`:

```bash
uv run management_tools/audit-content-guards.py \
  --env stage \
  --workers 1 \
  --guard-detail-timeout 180 \
  --output content-guards-stage.json \
  --failed-operations-output content-guards-stage-failed.json
```

The tool retries selected transient network and server errors. It does not retry
certificate, authentication, redirect, or other client errors. The command
exits with a nonzero status when the report is incomplete.

### Retry failed domain audits

The failed-operations manifest is checkpointed after each completed domain.
Successful operations are removed; failed and not-yet-started operations remain
available for retry. Retrying creates a new report and a new manifest. It does
not merge results from different audit runs.

```bash
uv run management_tools/audit-content-guards.py \
  --env stage \
  --workers 1 \
  --retry-manifest content-guards-stage-failed.json \
  --failed-operations-output content-guards-stage-retry.json \
  --output content-guards-stage-retry-report.json
```

The retry verifies that the environment, host, public-domain setting, probe
configuration, endpoint inventory, and domain identities still match the
manifest. Operational settings such as worker count and timeout can change
between runs.

## Interpret the report

The JSON report has top-level `metadata`, `complete`, `errors`,
`domains_without_content_guards`, `domains`, and `distributions` fields.

| Field | Description |
|---|---|
| `complete` | `true` when every selected domain audit completed; `false` means the inventory is partial. |
| `errors` | Sanitized errors for domains or inventory requests that failed. |
| `metadata.selected_domain_count` | Number of domains selected for this run. |
| `domains` | Successfully audited domain records. Failed domains are not included. |
| `domains_without_content_guards` | Domains with no content guard objects listed in that domain. This is not a count of domains with unguarded distributions. |
| `distributions` | Flat list of distributions returned by typed distribution endpoints. |

Each domain record includes its name and href, default guard, list of guard
objects, `content_guard_count`, `has_content_guard`, and its distributions.
`has_content_guard` means that at least one guard object exists in the domain;
it does not mean that every distribution is protected or that a guard is the
domain default.

Each distribution record includes:

| Field | Description |
|---|---|
| `domain`, `domain_href` | Domain name and API href associated with the distribution. |
| `type` | Typed distribution endpoint, such as `rpm/rpm` or `python/pypi`. |
| `name`, `pulp_href` | Distribution name and API href. |
| `base_path`, `base_url`, `repository` | Distribution path and source information returned by Pulp. |
| `explicit_content_guard` | Guard assigned directly to this distribution, or `null`. |
| `effective_content_guard` | Explicit guard, or the domain default guard when no explicit guard is assigned, or `null`. |
| `explicit_guard_type`, `effective_guard_type` | Guard subtype inferred from the corresponding href, or `null`. |
| `effective_guard` | Present when an effective guard exists. Includes the resolved guard name, type, href, and composite children when applicable. Guard configuration values are not included. |
| `pulp_classification` | `pulp_guarded` when an effective guard exists; otherwise `pulp_unguarded`. |
| `gateway_assessment` | `not_evaluated` unless optional endpoint probes were run. A probe only describes its tested endpoint. |
| `probes` | Optional unauthenticated endpoint observations. The tool tries `HEAD` and falls back to streamed `GET` if the endpoint returns `405`. These observations do not establish protection on other paths. |

For example, this record has no explicit or effective guard:

```json
{
  "domain": "cs-community",
  "type": "python/pypi",
  "name": "community-packages",
  "pulp_href": "/api/pulp/cs-community/api/v3/distributions/python/pypi/01900000-0000-7000-8000-000000000001/",
  "explicit_content_guard": null,
  "effective_content_guard": null,
  "pulp_classification": "pulp_unguarded",
  "gateway_assessment": "not_evaluated"
}
```

`pulp_unguarded` describes the audit's effective-guard classification. It does
not prove that the distribution is publicly reachable: external gateway or
network rules are outside this report's evaluation. Conversely, `pulp_guarded`
does not prove that every gateway path enforces the intended policy.

The audit calculates `effective_content_guard` by using the distribution's
explicit guard when present and otherwise falling back to the domain's current
default guard. This is an inventory classification, not a live authorization
test. In Pulp, a domain default is applied when a distribution is created; do
not treat a current default as proof that a legacy distribution has that guard
assigned. The apply tool must perform its live checks before making changes.

The summary count “domains without content guards” counts domains with no guard
objects, including domains that might have no distributions. A domain can have
guard objects but no default guard, while one or more of its distributions
remain unguarded. Use distribution-level classification when identifying
candidates for guard assignment.

Do not use a report with `complete: false` as a complete migration inventory.
Retry the failed domain operations or run a fresh audit before acting.

## Query the report with `jq`

The following examples use `audit-report.json` as the report path.

Count protected and unprotected distributions in `cs-community`:

```bash
jq '
  [.distributions[] | select(.domain == "cs-community")] as $d
  | {
      total: ($d | length),
      protected: ([$d[] | select(.pulp_classification == "pulp_guarded")] | length),
      unprotected: ([$d[] | select(.pulp_classification == "pulp_unguarded")] | length)
    }
' audit-report.json
```

List unguarded distributions in that domain:

```bash
jq '
  .distributions[]
  | select(.domain == "cs-community" and .pulp_classification == "pulp_unguarded")
  | {type, name, pulp_href, base_path}
' audit-report.json
```

Select the last unguarded distribution in report order:

```bash
jq '
  [.distributions[]
   | select(.domain == "cs-community" and .pulp_classification == "pulp_unguarded")]
  | .[-1] // empty
' audit-report.json
```

This selects the last matching array entry, not the most recently created
distribution. The report does not contain distribution creation timestamps.

Print domains without any content guard objects:

```bash
jq -r '.domains_without_content_guards[].name' audit-report.json
```

Show the report completeness and errors:

```bash
jq '{complete, selected_domain_count: .metadata.selected_domain_count, errors}' \
  audit-report.json
```
