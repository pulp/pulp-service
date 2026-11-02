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

OUTPUT_FORMAT=human
if [[ "${1:-}" == "--output" ]]; then
    if (( $# < 2 )); then
        printf '%s requires json or human\n' "$1" >&2
        exit 2
    fi
    OUTPUT_FORMAT=$2
    shift 2
fi
case "$OUTPUT_FORMAT" in
    json|human) ;;
    *)
        printf 'Invalid output format: %s (expected json or human)\n' "$OUTPUT_FORMAT" >&2
        exit 2
        ;;
esac
if (( $# > 0 )); then
    if [[ "$1" == "--help" || "$1" == "-h" ]]; then
        printf 'Usage: %s [--output json|human]\n' "$0"
        exit 0
    fi
    printf 'Unexpected argument: %s\nUsage: %s [--output json|human]\n' "$1" "$0" >&2
    exit 2
fi

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
OUT_DIR=$(mktemp -d "$OUTPUT_ROOT/run.XXXXXX")
chmod 700 "$OUT_DIR"

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

    if ! query_id=$(aws logs start-query \
        --profile "$AWS_LOG_PROFILE" \
        --region "$AWS_REGION" \
        --log-group-name "$PULP_LOG_GROUP" \
        --start-time "$START" \
        --end-time "$END" \
        --query-string "$query" \
        --query queryId \
        --output text); then
        printf 'CloudWatch start-query failed\n' >&2
        return 1
    fi

    for _ in $(seq 1 90); do
        if ! output=$(aws logs get-query-results \
            --profile "$AWS_LOG_PROFILE" \
            --region "$AWS_REGION" \
            --query-id "$query_id" \
            --output json); then
            printf 'CloudWatch get-query-results failed for %s\n' "$query_id" >&2
            return 1
        fi
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

CLOUDWATCH_OVERALL_QUERY='fields @timestamp, @message
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
    "$(format_time "$START")" \
    "$(format_time "$END")"
printf 'Results directory: %s\n' "$OUT_DIR"
run_logs_query "$CLOUDWATCH_QUERY" > "$OUT_DIR/cloudwatch.json"
run_logs_query "$CLOUDWATCH_OVERALL_QUERY" > "$OUT_DIR/cloudwatch-overall.json"
OUTPUT_FORMAT="$OUTPUT_FORMAT" python3 - "$OUT_DIR/cloudwatch.json" "$OUT_DIR/cloudwatch-overall.json" <<'PY'

import json
import os
import sys
from collections import defaultdict

payload = json.load(open(sys.argv[1]))
overall_payload = json.load(open(sys.argv[2]))
experiments = defaultdict(lambda: defaultdict(dict))
overall = defaultdict(lambda: defaultdict(dict))

def parse_values(row):
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
    if not values.get("timed"):
        for field in ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
            values.pop(field, None)
    return experiment, event, variant, repository, values

for row in payload.get("results", []):
    experiment, event, variant, repository, values = parse_values(row)
    event_data = experiments[experiment][event]
    if variant:
        event_data.setdefault(variant, {"repositories": {}})["repositories"][repository] = values
    else:
        event_data.setdefault("statistics", {})[repository] = values

for row in overall_payload.get("results", []):
    experiment, event, variant, _, values = parse_values(row)
    if variant:
        overall[experiment][event][variant] = values

if os.environ.get("OUTPUT_FORMAT") == "json":
    print(json.dumps(experiments, indent=2))
    print("CloudWatch status:", payload.get("status"))
    print("CloudWatch statistics:", json.dumps(payload.get("statistics", {})))
    print("CloudWatch overall statistics:", json.dumps(overall_payload.get("statistics", {})))
else:
    def value(data, field):
        return data.get(field, "-") if data else "-"

    def percent_change(control, candidate):
        control_avg = control.get("avg_ms") if control else None
        candidate_avg = candidate.get("avg_ms") if candidate else None
        if not control_avg or candidate_avg is None:
            return "-"
        return f"{(candidate_avg - control_avg) / control_avg * 100:+.1f}%"

    print("\n=== CloudWatch repository-by-repository A/B comparison ===")
    headers = ["Experiment", "Event", "Repository", "A n", "B n", "A avg", "B avg", "Change", "A p50", "B p50", "A p95", "B p95", "A p99", "B p99"]
    table = []
    for experiment, events in sorted(experiments.items()):
        for event, variants in sorted(events.items()):
            repositories = set()
            for variant in ("A", "B"):
                repositories.update(variants.get(variant, {}).get("repositories", {}))
            for repository in sorted(repositories):
                if repository == "unattributed":
                    continue
                control = variants.get("A", {}).get("repositories", {}).get(repository, {})
                candidate = variants.get("B", {}).get("repositories", {}).get(repository, {})
                a_events, b_events = value(control, "events"), value(candidate, "events")
                a_avg, b_avg = value(control, "avg_ms"), value(candidate, "avg_ms")
                a_p50, b_p50 = value(control, "p50_ms"), value(candidate, "p50_ms")
                a_p95, b_p95 = value(control, "p95_ms"), value(candidate, "p95_ms")
                a_p99, b_p99 = value(control, "p99_ms"), value(candidate, "p99_ms")
                table.append([
                    experiment, event, repository, a_events, b_events, a_avg, b_avg,
                    percent_change(control, candidate), a_p50, b_p50, a_p95, b_p95,
                    a_p99, b_p99,
                ])
    overall_table = []
    for experiment, events in sorted(overall.items()):
        for event, variants in sorted(events.items()):
            control = variants.get("A", {})
            candidate = variants.get("B", {})
            if not control and not candidate:
                continue
            overall_table.append([
                experiment,
                event,
                value(control, "events"),
                value(candidate, "events"),
                value(control, "avg_ms"),
                value(candidate, "avg_ms"),
                percent_change(control, candidate),
                value(control, "p50_ms"),
                value(candidate, "p50_ms"),
                value(control, "p95_ms"),
                value(candidate, "p95_ms"),
                value(control, "p99_ms"),
                value(candidate, "p99_ms"),
            ])

    if table:
        widths = [max(len(str(row[index])) for row in [headers, *table]) for index in range(len(headers))]
        print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
        print("  ".join("-" * width for width in widths))
        for row in table:
            print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))

    if overall_table:
        print("\n=== CloudWatch overall A/B comparison ===")
        overall_headers = ["Experiment", "Event", "A n", "B n", "A avg", "B avg", "Change", "A p50", "B p50", "A p95", "B p95", "A p99", "B p99"]
        widths = [max(len(str(row[index])) for row in [overall_headers, *overall_table]) for index in range(len(overall_headers))]
        print("  ".join(header.ljust(widths[index]) for index, header in enumerate(overall_headers)))
        print("  ".join("-" * width for width in widths))
        for row in overall_table:
            print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))

    if not table and not overall_table:
        print("No A/B metrics were found.")
PY

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
    OUTPUT_FORMAT="$OUTPUT_FORMAT" python3 - "$pi_dir" <<'PY'
import glob
import json
import os
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
    rollup = defaultdict(lambda: {"digest_count": 0, "load": 0.0, "windows": 0})
    for (experiment, variant, repository, _), item in aggregate.items():
        summary = rollup[(experiment, variant, repository)]
        summary["digest_count"] += 1
        summary["load"] += item["total"]
        summary["windows"] += item["windows"]

    if os.environ.get("OUTPUT_FORMAT") == "json":
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
    else:
        comparison = []
        experiment_repositories = {(experiment, repository) for experiment, _, repository in rollup}
        for experiment, repository in sorted(experiment_repositories):
            control = rollup.get((experiment, "A", repository), {})
            candidate = rollup.get((experiment, "B", repository), {})
            change = "-"
            if control.get("load") and candidate.get("load") is not None:
                change = f"{(candidate['load'] - control['load']) / control['load'] * 100:+.1f}%"
            comparison.append([
                experiment,
                repository,
                control.get("digest_count", "-"),
                candidate.get("digest_count", "-"),
                round(control["load"], 6) if control else "-",
                round(candidate["load"], 6) if candidate else "-",
                change,
                control.get("windows", "-"),
                candidate.get("windows", "-"),
            ])
        headers = ["Experiment", "Repository", "A digests", "B digests", "A load", "B load", "Change", "A windows", "B windows"]
        print("\n=== Performance Insights rollup A/B comparison ===")
        if comparison:
            widths = [max(len(str(row[index])) for row in [headers, *comparison]) for index in range(len(headers))]
            print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
            print("  ".join("-" * width for width in widths))
            for row in comparison:
                print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
        else:
            print("No A/B rollup rows were found.")
PY
done
