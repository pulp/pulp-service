#!/usr/bin/env bash
# Collect CloudWatch experiment events and annotated RDS Performance Insights SQL.
#
# Required environment:
#   PULP_LOG_GROUP          CloudWatch Logs group to query
#   AWS_LOG_PROFILE         AWS CLI profile with CloudWatch Logs read access
#   AWS_PI_PROFILE          AWS CLI profile with RDS Performance Insights access
#   AWS_REGION              AWS region containing the logs and RDS instances
#   PULP_PI_DB_INSTANCES    Comma-separated RDS DB instance identifiers
#
# Optional environment:
#   START_TIME              Unix timestamp; defaults to 24 hours ago
#   END_TIME                Unix timestamp; defaults to now
#   PI_WINDOW_SECONDS       Performance Insights window size; defaults to 3600
#   OUT_DIR                 Output directory; defaults to /tmp/pulp-experiment-results
set -euo pipefail

: "${PULP_LOG_GROUP:?Set PULP_LOG_GROUP to the CloudWatch Logs group}"
: "${AWS_LOG_PROFILE:?Set AWS_LOG_PROFILE to the AWS CLI CloudWatch profile}"
: "${AWS_PI_PROFILE:?Set AWS_PI_PROFILE to the AWS CLI Performance Insights profile}"
: "${AWS_REGION:?Set AWS_REGION to the AWS region}"
: "${PULP_PI_DB_INSTANCES:?Set PULP_PI_DB_INSTANCES to comma-separated RDS instance identifiers}"

START=${START_TIME:-$(date -u -d '24 hours ago' +%s)}
END=${END_TIME:-$(date -u +%s)}
PI_WINDOW_SECONDS=${PI_WINDOW_SECONDS:-3600}
OUT_DIR=${OUT_DIR:-/tmp/pulp-experiment-results}
mkdir -p "$OUT_DIR"

for command in aws date python3; do
    command -v "$command" >/dev/null || {
        printf 'Missing required command: %s\n' "$command" >&2
        exit 1
    }
done

run_logs_query() {
    local query=$1
    local query_id status output

    query_id=$(aws logs start-query \
        --profile "$AWS_LOG_PROFILE" \
        --region "$AWS_REGION" \
        --log-group-name "$PULP_LOG_GROUP" \
        --start-time "$START" \
        --end-time "$END" \
        --query-string "$query" \
        --query queryId \
        --output text)

    for _ in $(seq 1 90); do
        output=$(aws logs get-query-results \
            --profile "$AWS_LOG_PROFILE" \
            --region "$AWS_REGION" \
            --query-id "$query_id" \
            --output json)
        status=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$output")
        case "$status" in
            Complete|Failed|Cancelled|Timeout)
                printf '%s\n' "$output"
                return 0
                ;;
        esac
        sleep 2
    done

    printf '%s\n' "$output"
    return 1
}

# Experiment fields are nested in the JSON application log's @message field.
# Parse them instead of relying on CloudWatch's top-level JSON fields.
CLOUDWATCH_QUERY='fields @timestamp, @message
| filter @message like /ab_experiment/
| parse @message /exp_id\\": \\"(?<exp_id>[^\\"]+)/
| parse @message /event\\": \\"(?<event>[^\\"]+)/
| parse @message /variant\\": \\"(?<variant>[AB])/
| parse @message /duration_ms\\": (?<duration_ms>[0-9.]+)/
| stats count() as events,
        count(duration_ms) as timed,
        avg(duration_ms) as avg_ms,
        pct(duration_ms, 50) as p50_ms,
        pct(duration_ms, 95) as p95_ms,
        pct(duration_ms, 99) as p99_ms,
        max(duration_ms) as max_ms,
        min(@timestamp) as first,
        max(@timestamp) as last
  by exp_id, event, variant
| sort exp_id asc, event asc, variant asc'

printf '=== CloudWatch experiment events (%s to %s UTC) ===\n' \
    "$(date -u -d "@$START" '+%F %T')" \
    "$(date -u -d "@$END" '+%F %T')"
run_logs_query "$CLOUDWATCH_QUERY" \
    | tee "$OUT_DIR/cloudwatch.json" \
    | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps(payload.get("results", []), indent=2))
print("CloudWatch status:", payload.get("status"))
print("CloudWatch statistics:", json.dumps(payload.get("statistics", {})))
'

# Performance Insights returns only the top SQL digests for a requested
# window. Query hourly windows so short-lived annotated SQL is less likely to
# be hidden by the full-range top-25 list, then aggregate duplicate SQL IDs.
IFS=',' read -r -a db_instances <<< "$PULP_PI_DB_INSTANCES"
for db_instance in "${db_instances[@]}"; do
    db_instance=${db_instance//[[:space:]]/}
    resource_id=$(aws rds describe-db-instances \
        --profile "$AWS_PI_PROFILE" \
        --region "$AWS_REGION" \
        --db-instance-identifier "$db_instance" \
        --query 'DBInstances[0].DbiResourceId' \
        --output text)

    pi_dir="$OUT_DIR/pi-$db_instance"
    mkdir -p "$pi_dir"
    rm -f "$pi_dir"/window-*.json "$pi_dir"/window-*.err

    window_start=$START
    window_number=0
    while [ "$window_start" -lt "$END" ]; do
        window_end=$((window_start + PI_WINDOW_SECONDS))
        [ "$window_end" -gt "$END" ] && window_end=$END
        pi_file="$pi_dir/window-$window_number.json"

        aws pi describe-dimension-keys \
            --profile "$AWS_PI_PROFILE" \
            --region "$AWS_REGION" \
            --service-type RDS \
            --identifier "$resource_id" \
            --start-time "$window_start" \
            --end-time "$window_end" \
            --period-in-seconds 60 \
            --metric db.load.avg \
            --group-by '{"Group":"db.sql_tokenized","Dimensions":["db.sql_tokenized.id","db.sql_tokenized.statement"],"Limit":25}' \
            --output json >"$pi_file" 2>"$pi_file.err" || rm -f "$pi_file"

        window_start=$window_end
        window_number=$((window_number + 1))
    done

    printf '\n=== Performance Insights annotated SQL: %s (%s) ===\n' \
        "$db_instance" "$resource_id"
    python3 - "$pi_dir" <<'PY'
import glob
import json
import re
import sys
from collections import defaultdict

aggregate = defaultdict(lambda: {"total": 0.0, "windows": 0, "statement": ""})
for filename in glob.glob(sys.argv[1] + "/window-*.json"):
    payload = json.load(open(filename))
    for key in payload.get("Keys", []):
        dimensions = key.get("Dimensions", {})
        statement = dimensions.get("db.sql_tokenized.statement", "")
        markers = re.findall(
            r"/\*\s*pexp=([^ *]+)\s+v=([AB])\s*\*/", statement
        )
        for experiment, variant in markers:
            sql_id = dimensions.get("db.sql_tokenized.id")
            item = aggregate[(experiment, variant, sql_id)]
            item["total"] += float(key.get("Total") or 0)
            item["windows"] += 1
            item["statement"] = statement[:500]

if not aggregate:
    print("No annotated experiment SQL appeared in any hourly top-25 window.")
else:
    for (experiment, variant, sql_id), item in sorted(
        aggregate.items(),
        key=lambda pair: (pair[0][0], pair[0][1], -pair[1]["total"]),
    ):
        print(json.dumps({
            "experiment": experiment,
            "variant": variant,
            "sql_id": sql_id,
            "db_load_total_sum": round(item["total"], 6),
            "windows_seen": item["windows"],
            "statement_prefix": item["statement"],
        }))
PY
done
