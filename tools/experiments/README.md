# Production experiment results

`tools/experiments/pulp_experiment_results.sh` summarizes hosted Pulp A/B experiments from:

- CloudWatch Logs Insights experiment events and optional `duration_ms` values.
- RDS Performance Insights SQL digests containing `pexp=<experiment> v=A|B` comments.

The script is intentionally environment-driven. It contains no production log-group,
AWS profile, database identifier, account, or credential values.

## Requirements

- Bash, Python 3, and AWS CLI v2.
- An AWS profile with CloudWatch Logs query permissions.
- An AWS profile with RDS Performance Insights and RDS instance-description permissions.
- Access to the configured CloudWatch log group and RDS instances.

## Usage

```bash
export AWS_REGION=us-east-1
export AWS_LOG_PROFILE=<cloudwatch-read-profile>
export AWS_PI_PROFILE=<rds-performance-insights-profile>
export PULP_LOG_GROUP=<cloudwatch-log-group>
export PULP_PI_DB_INSTANCES=<primary-db-instance>,<read-replica-db-instance>

tools/experiments/pulp_experiment_results.sh
```

The default time range is the previous 24 hours. Override it with Unix timestamps
between 0 and 4102444800 (through 2100):

```bash
START_TIME=$(date -u -d '6 hours ago' +%s) \
END_TIME=$(date -u +%s) \
tools/experiments/pulp_experiment_results.sh
```

Each invocation creates a private run directory under `OUT_DIR` and prints its
path. Set `OUT_DIR` to choose the parent directory for raw CloudWatch and
Performance Insights JSON files. `PI_WINDOW_SECONDS` defaults to one hour and
can be reduced when short-lived SQL digests need to be detected.

## Interpretation

CloudWatch experiment events are nested inside the application log's `@message`,
so the script parses those fields. Results are printed as experiment → event →
variant, with each variant's statistics grouped together. Experiments that record
`duration_ms` receive percentiles; lazy queryset experiments may only provide
dispatch counts.

Performance Insights reports aggregate `db.load.avg` for SQL digests, not
per-request latency. The script prints both per-digest rows and a rollup with
the distinct SQL digest count and aggregate load for each experiment/variant.
The SQL results are limited to annotated digests that appear in the top 25 for
at least one queried time window and should be normalized with experiment
dispatch counts before drawing conclusions. The digest count is not an
execution count, and the aggregate load is not a latency percentile.
