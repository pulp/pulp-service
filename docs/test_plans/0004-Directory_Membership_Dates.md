# Directory membership dates: PULP-2505

[PULP-2505](https://redhat.atlassian.net/browse/PULP-2505) compares the membership
timestamp lookup in HTML directory listings. It follows the hosted Pulp
[A/B experiment guide](https://hosted-pulp.pages.redhat.com/pulp-docs/operations/ab-experiments/).

## Implementation and eligibility

Patch 0077 connects `Handler.list_directory()` to
`pulp_service.app.experiments.directory_membership_dates()`.

- A iterates the version's membership models and filters them in Python.
- B restricts the same membership queryset using the listing's content artifact
  and published artifact subqueries, selecting only `content_id` and `pulp_created`.
  It does not bind a parameter for every listed content ID. Both variants retain
  pulpcore's newest-timestamp-wins behavior when multiple membership rows map to
  the same displayed name.

Both preserve the version's historical membership predicate. Published paths and
pass-through paths remain separate sources. Each eligible call runs one variant;
failed B calls propagate their exception without retrying A.

Calls are eligible only when the content-to-name mapping is nonempty and each
displayed name occurs once. Several contents can collapse into one directory name.
Such calls retain A and emit `ab_experiment_skipped` with `reason=shared_name` to
keep the experiment scope conservative. Empty directory listings never reach the
membership lookup and do not emit an experiment event.

JSON `list_directory_flat()`, plugin directory handlers, and cached responses are
outside the measured section. The sample count is eligible lookups, not total
content requests. No schema or stored-data changes are required.

## Configuration

The experiment is disabled by default. Build the service image with patch 0077
and the accompanying helper before configuring an experiment deployment.

| Setting | Default | Experiment value |
| --- | --- | --- |
| `CONTENT_DIRECTORY_AB_ENABLED` | `False` | `True` |
| `CONTENT_DIRECTORY_AB_PROBABILITY` | `0.5` | `0.5` |
| `CONTENT_DIRECTORY_AB_REVISION` | `""` | Deployed image's Git SHA |

These settings accept the usual `PULP_` environment prefix, for example
`PULP_CONTENT_DIRECTORY_AB_ENABLED=true`. Apply them consistently to all content
workers in the experiment deployment and restart those workers when changing
settings. A missing revision or invalid probability retains A and records a
configuration skip. Keep the probability constant during each analysis window.

Set probability to `0` and restart the workers to stop B while retaining A timing.
Set enabled to `false` to bypass experiment logging and selection entirely.
Deployment and enablement are separate operational steps.

## Measurements

The `pulp.experiment` logger emits a JSON body for each eligible lookup with:

- `event=ab_experiment`, `exp_id=PULP-2505`, `variant`, `p_candidate`, and
  `deployment_revision`;
- `duration_ms`, `outcome`, `correlation_id`, and either `result_count` or
  `error_type`;
- `serving_mode`, `is_root`, `candidate_count`, and `directory_count`.

Timing includes variant query construction, database execution, full result
iteration, and construction of the returned date dictionary. It excludes the
preceding artifact listing, eligibility check, subsequent size queries, HTML
rendering, and log emission. It is not whole-request latency.

SQL is marked `/* pexp=PULP-2505 v=A */` or `v=B` inside an execution wrapper on
the database alias selected by the membership queryset's router. The wrapper is
removed after the lookup, including on exceptions. No request paths, content IDs,
or exception messages are added to experiment logs.

Check CloudWatch ingestion with a small staging run before enabling production
traffic. Confirm both variants, their revision, SQL markers on the routed database,
and correlation IDs. Verify that the JSON body is parsed despite the Pulp logging
prefix. If fields are not extracted automatically, parse the body explicitly:

```text
fields @timestamp, @message
| parse @message /(?<body>\{.*\})/
| fields jsonParse(body) as e
| filter e.event = "ab_experiment" and e.exp_id = "PULP-2505"
| filter e.deployment_revision = "<image-git-sha>" and e.outcome = "success"
| stats count(*) as n,
        pct(e.duration_ms, 50) as p50,
        pct(e.duration_ms, 95) as p95,
        pct(e.duration_ms, 99) as p99
  by e.variant, e.serving_mode, e.is_root
```

Also compare workload buckets using `candidate_count`, `directory_count`, and
`result_count`. Count outcomes independently so failed calls remain visible:

```text
fields @message
| parse @message /(?<body>\{.*\})/
| fields jsonParse(body) as e
| filter e.event = "ab_experiment" and e.exp_id = "PULP-2505"
| filter e.deployment_revision = "<image-git-sha>"
| stats count(*) as attempts by e.variant, e.outcome
```

Inspect `ab_experiment_skipped` events separately to assess coverage and detect
configuration errors. These events are currently unsampled. Use application logs
for A/B attribution and RDS Performance Insights for supporting query evidence.

## Validation and decision

Run `pulp_service/tests/unit/test_directory_experiment.py` through `oci-env` in
an image with the hosted patch stack applied. The tests compare full directory
results across direct repositories, publications, and pass-through publications;
historical removal/re-addition; rewritten and shared paths; domain isolation;
on-demand sizes and empty files. A separate 65,536-content test checks membership
lookup equivalence without a large parameter list. Dispatch, error logging,
database alias selection, and JSON bypass also have focused checks.

For an `oci-env` environment with `pulp_service` installed from this checkout and
the hosted patches applied:

```bash
source ~/devel/pulp/bin/activate
oci-env test -p pulp_service unit -k directory_experiment
```

Collect at least 10,000 successful eligible calls per variant and 24 hours of
traffic, preferably including a weekend. Low eligible volume extends the run.
Compare p50/p95/p99, error rates across all attempts, workload mix, and returned
date counts within a single deployed revision. Stop B for correctness failures
or elevated errors. Record results and the keep/remove decision on PULP-2505,
then remove the temporary experiment machinery.
