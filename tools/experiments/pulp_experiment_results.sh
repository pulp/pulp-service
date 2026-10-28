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
umask 077

: "${PULP_LOG_GROUP:?Set PULP_LOG_GROUP to the CloudWatch Logs group}"
: "${AWS_LOG_PROFILE:?Set AWS_LOG_PROFILE to the AWS CLI CloudWatch profile}"
: "${AWS_PI_PROFILE:?Set AWS_PI_PROFILE to the AWS CLI Performance Insights profile}"
: "${AWS_REGION:?Set AWS_REGION to the AWS region}"
: "${PULP_PI_DB_INSTANCES:?Set PULP_PI_DB_INSTANCES to comma-separated RDS instance identifiers}"

for command in aws mktemp python3; do
    command -v "$command" >/dev/null || {
        printf 'Missing required command: %s\n' "$command" >&2
        exit 1
    }
done

current_epoch=$(python3 -c 'import time; print(int(time.time()))')
START=${START_TIME:-$((current_epoch - 86400))}
END=${END_TIME:-$current_epoch}
PI_WINDOW_SECONDS=${PI_WINDOW_SECONDS:-3600}
OUTPUT_ROOT=${OUT_DIR:-/tmp/pulp-experiment-results}

validate_epoch() {
    local name=$1 value=$2
    if ! [[ "$value" =~ ^[0-9]+$ ]] || (( ${#value} > 10 )) || (( value > 4102444800 )); then
        printf '%s must be a Unix timestamp between 0 and 4102444800\n' "$name" >&2
        exit 1
    fi
}

validate_epoch START_TIME "$START"
validate_epoch END_TIME "$END"
if ! [[ "$PI_WINDOW_SECONDS" =~ ^[1-9][0-9]*$ ]] ||
    (( ${#PI_WINDOW_SECONDS} > 5 )) || (( PI_WINDOW_SECONDS > 86400 )); then
    printf 'PI_WINDOW_SECONDS must be a positive integer no greater than 86400\n' >&2
    exit 1
fi
if (( START >= END )); then
    printf 'START_TIME must be earlier than END_TIME\n' >&2
    exit 1
fi

mkdir -p "$OUTPUT_ROOT"
chmod 700 "$OUTPUT_ROOT"
OUT_DIR=$(mktemp -d "$OUTPUT_ROOT/run.XXXXXX")

format_time() {
    python3 - "$1" <<'PY'
import datetime
import sys

print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
PY
}

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
            Complete)
                printf '%s\n' "$output"
                return 0
                ;;
            Failed|Cancelled|Timeout)
                printf 'CloudWatch query %s: %s\n' "$status" "$output" >&2
                return 1
                ;;
        esac
        sleep 2
    done

    aws logs stop-query \
        --profile "$AWS_LOG_PROFILE" \
        --region "$AWS_REGION" \
        --query-id "$query_id" >/dev/null 2>&1 || true
    printf 'CloudWatch query timed out after 180 seconds: %s\n' "$query_id" >&2
    return 1
}

# Experiment fields are nested in the JSON application log's @message field.
# Parse them instead of relying on CloudWatch's top-level JSON fields.
CLOUDWATCH_QUERY='fields @timestamp, @message
| filter @message like /ab_experiment/
| parse @message /exp_id\\": \\"(?<exp_id>[^\\"]+)/
| parse @message /event\\": \\"(?<event>[^\\"]+)/
| parse @message /variant\\": \\"(?<variant>[AB])/
| parse @message /repository_id\\": \\"(?<repository_id>[^\\"]+)/
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
  by exp_id, event, variant, repository_id
| sort exp_id asc, event asc, variant asc, repository_id asc'

printf '=== CloudWatch experiment events (%s to %s UTC) ===\n' \
    "$(format_time "$START")" \
    "$(format_time "$END")"
printf 'Results directory: %s\n' "$OUT_DIR"
run_logs_query "$CLOUDWATCH_QUERY" \
    | tee "$OUT_DIR/cloudwatch.json" \
    | python3 -c '
import json
import sys
from collections import defaultdict

payload = json.load(sys.stdin)
experiments = defaultdict(lambda: defaultdict(dict))

for row in payload.get("results", []):
    values = {item["field"]: item["value"] for item in row}
    experiment = values.pop("exp_id", "unknown")
    event = values.pop("event", "unknown")
    variant = values.pop("variant", None)
    repository = values.pop("repository_id", None) or "unattributed"

    for field in ("events", "timed"):
        if field in values:
            values[field] = int(values[field])
    for field in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
        if field in values:
            values[field] = float(values[field])

    # CloudWatch returns zero-like percentile values when no duration was
    # recorded. Do not present those as measured latency statistics.
    if not values.get("timed"):
        for field in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
            values.pop(field, None)

    event_data = experiments[experiment][event]
    if variant:
        event_data.setdefault(variant, {"repositories": {}})["repositories"][repository] = values
    else:
        event_data.setdefault("statistics", {})[repository] = values

print(json.dumps(experiments, indent=2))
print("CloudWatch status:", payload.get("status"))
print("CloudWatch statistics:", json.dumps(payload.get("statistics", {})))
'

# Performance Insights returns only the top SQL digests for a requested
# window. Query hourly windows so short-lived annotated SQL is less likely to
# be hidden by the full-range top-25 list, then aggregate duplicate SQL IDs.
IFS=',' read -r -a db_instances <<< "$PULP_PI_DB_INSTANCES"
for db_instance in "${db_instances[@]}"; do
    db_instance=${db_instance//[[:space:]]/}
    if ! [[ "$db_instance" =~ ^[A-Za-z0-9-]+$ ]]; then
        printf 'Invalid RDS instance identifier: %s\n' "$db_instance" >&2
        exit 1
    fi
    resource_id=$(aws rds describe-db-instances \
        --profile "$AWS_PI_PROFILE" \
        --region "$AWS_REGION" \
        --db-instance-identifier "$db_instance" \
        --query 'DBInstances[0].DbiResourceId' \
        --output text)
    if [[ -z "$resource_id" || "$resource_id" == "None" ]]; then
        printf 'Could not resolve RDS resource ID for %s\n' "$db_instance" >&2
        exit 1
    fi

    pi_dir="$OUT_DIR/pi-$db_instance"
    mkdir -p "$pi_dir"
    rm -f "$pi_dir"/window-*.json "$pi_dir"/window-*.err

    window_start=$START
    window_number=0
    while [ "$window_start" -lt "$END" ]; do
        window_end=$((window_start + PI_WINDOW_SECONDS))
        [ "$window_end" -gt "$END" ] && window_end=$END
        pi_file="$pi_dir/window-$window_number.json"

        if ! aws pi describe-dimension-keys \
            --profile "$AWS_PI_PROFILE" \
            --region "$AWS_REGION" \
            --service-type RDS \
            --identifier "$resource_id" \
            --start-time "$window_start" \
            --end-time "$window_end" \
            --period-in-seconds 60 \
            --metric db.load.avg \
            --group-by '{"Group":"db.sql_tokenized","Dimensions":["db.sql_tokenized.id","db.sql_tokenized.statement"],"Limit":25}' \
            --output json >"$pi_file" 2>"$pi_file.err"; then
            printf 'Performance Insights query failed for %s, window %s-%s:\n' \
                "$db_instance" "$window_start" "$window_end" >&2
            cat "$pi_file.err" >&2
            exit 1
        fi

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
marker_re = re.compile(
    r"/\*\s*pexp=(?P<experiment>[^ *]+)\s+v=(?P<variant>[AB])\s*\*/"
    r"(?:\s*/\*\s*repo=(?P<repository>[^ *]+)\s*\*/)?"
)
for filename in glob.glob(sys.argv[1] + "/window-*.json"):
    payload = json.load(open(filename))
    for key in payload.get("Keys", []):
        dimensions = key.get("Dimensions", {})
        statement = dimensions.get("db.sql_tokenized.statement", "")
        for marker in marker_re.finditer(statement):
            experiment = marker.group("experiment")
            variant = marker.group("variant")
            repository = marker.group("repository") or "unattributed"
            sql_id = dimensions.get("db.sql_tokenized.id")
            item = aggregate[(experiment, variant, repository, sql_id)]
            item["total"] += float(key.get("Total") or 0)
            item["windows"] += 1
            item["statement"] = statement[:500]

if not aggregate:
    print("No annotated experiment SQL appeared in any hourly top-25 window.")
else:
    for (experiment, variant, repository, sql_id), item in sorted(
        aggregate.items(),
        key=lambda pair: (pair[0][0], pair[0][1], pair[0][2], -pair[1]["total"]),
    ):
        print(json.dumps({
            "experiment": experiment,
            "variant": variant,
            "repository_id": repository,
            "sql_id": sql_id,
            "db_load_total_sum": round(item["total"], 6),
            "windows_seen": item["windows"],
            "statement_prefix": item["statement"],
        }))

    rollup = defaultdict(lambda: {"digest_count": 0, "load": 0.0, "windows": 0})
    for (experiment, variant, repository, _), item in aggregate.items():
        summary = rollup[(experiment, variant, repository)]
        summary["digest_count"] += 1
        summary["load"] += item["total"]
        summary["windows"] += item["windows"]

    print("\n=== Performance Insights rollup by experiment/variant/repository ===")
    for (experiment, variant, repository), summary in sorted(rollup.items()):
        print(json.dumps({
            "experiment": experiment,
            "variant": variant,
            "repository_id": repository,
            "sql_digest_count": summary["digest_count"],
            "db_load_total_sum": round(summary["load"], 6),
            "digest_window_observations": summary["windows"],
        }))
PY
done
