# Interval content query: PULP-1996

[PULP-1996-CONTENT-MEMBERSHIP](https://github.com/pulp/pulpcore/pull/8167) compares the
content membership query backing `RepositoryVersion.get_content()`. It follows the hosted
Pulp [A/B experiment guide](https://hosted-pulp.pages.redhat.com/pulp-docs/operations/ab-experiments/).
Related tracking: LWLP-3268.

## Implementation and eligibility

Patch 0081 connects `RepositoryVersion.get_content()` to
`pulp_service.app.experiments.run_lazy_experiment()`.

- A filters `content_qs` by the legacy `unnest(content_ids)` subquery.
- B filters the same queryset by `content_pks_subquery()`, which restricts
  `core_repositorycontent` through the existing `_content_relationships()`
  interval predicate (`version_added.number <= number`, not removed at or before
  this version).

Both variants return an equivalent lazy queryset; callers can keep chaining
filters exactly as before. Every call dispatches one variant — there are no
eligibility filters and no settings. The experiment is enabled by default at
`p_candidate=0.5`, so deploying the image enables it with no configuration
change or follow-up deployment.

Variant B exists because `unnest` is a set-returning function with a hard-coded
`rows=10` planner estimate, so PostgreSQL picks nested-loop index probes scaling
with the full version content array instead of the result set. The interval join
plans against real btree indexes and table statistics. Local repro (PG16, ~10k
content version): 197 ms (A) vs 8 ms (B).

## Measurements

`get_content()` returns a lazily evaluated queryset: constructing it executes no
SQL, and callers may chain further filters before evaluation. Timing the call
would measure queryset construction, not the query, and forcing evaluation would
run the query twice (once for the experiment, once for the caller). The
experiment therefore records no duration.

The `pulp.experiment` logger emits one JSON dispatch line per call:

- `event=ab_experiment_dispatch`, `exp_id=PULP-1996-CONTENT-MEMBERSHIP`,
  `variant`, `p_candidate`, and `correlation_id`.

Latency and load are attributed per variant in RDS Performance Insights Top SQL
through marker comments embedded in the SQL:

- A: `/* pexp=PULP-1996-CONTENT-MEMBERSHIP v=A */`
- B: `/* pexp=PULP-1996-CONTENT-MEMBERSHIP v=B */`

Application dispatch logs are the source of truth for variant volume and
request correlation; RDS Performance Insights is the source of truth for query
latency and database load.

Check CloudWatch ingestion with a small staging run before production traffic.
Confirm both variants appear, dispatch volume roughly matches content request
volume, and correlation IDs are present. If fields are not extracted
automatically, parse the JSON body explicitly:

```text
fields @timestamp, @message
| parse @message /(?<body>\{.*\})/
| fields jsonParse(body) as e
| filter e.event = "ab_experiment_dispatch" and e.exp_id = "PULP-1996-CONTENT-MEMBERSHIP"
| stats count(*) as n by e.variant
```

In RDS Performance Insights, filter Top SQL by the marker text and compare
average latency, calls, and total load between the `v=A` and `v=B` statements
over identical time windows. Because each call runs exactly one variant and
dispatch is uniform at random, the two SQL populations see the same workload
mix.

## Validation and decision

Run `pulp_service/tests/unit/test_interval_content_experiment.py` through
`oci-env` in an image with the hosted patch stack applied. The tests compare
variant A and variant B content UUID sets across versions with adds and
removes, check cross-repository and empty-version behavior, verify the dispatch
wiring and dispatch log line, and assert both SQL markers reach the database.

For an `oci-env` environment with `pulp_service` installed from this checkout
and the hosted patches applied:

```bash
source ~/devel/pulp/bin/activate
oci-env test -p pulp_service unit -k interval_content_experiment
```

Decide on variant B by comparing RDS Performance Insights latency and load for
the two marked statements over a representative window, with dispatch volumes
from CloudWatch as the denominator. Correctness signals come from the unit
equivalence tests plus production error rates and content API responses during
the experiment window.
