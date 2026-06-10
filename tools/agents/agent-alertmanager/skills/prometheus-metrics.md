# Prometheus Metrics — pulp-service

## Access

```bash
TOKEN=$(oc whoami -t)
PROM="https://prometheus.crcs02ue1.devshift.net"  # Stage

# Instant query
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$PROM/api/v1/query?query=<PROMQL>"

# Range query
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$PROM/api/v1/query_range?query=<PROMQL>&start=<ISO8601>&end=<ISO8601>&step=60s"

# List all pulp metrics
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$PROM/api/v1/label/__name__/values" | jq -r '.data[] | select(test("pulp|content_sources"))'
```

Or use the helper script:
```bash
uv run scripts/prom-query.py <shortcut|promql> [--range 1h] [--cluster stage|prod]
```

## Core Metrics (46 total, namespace prefix: `pulp_`)

### API Performance

| Metric | Type | Description | Key Labels |
|--------|------|-------------|------------|
| `pulp_api_active_connections` | Gauge | Concurrent API connections | `pod`, `worker` |
| `pulp_api_request_duration_milliseconds_bucket` | Histogram | API latency distribution | `pod`, `http_method`, `http_status_code` |
| `pulp_api_request_duration_milliseconds_count` | Counter | Total API request count | `pod`, `http_method`, `http_status_code` |
| `pulp_api_request_duration_milliseconds_sum` | Counter | Total API request time | `pod`, `http_method`, `http_status_code` |

**Histogram buckets** (API): `[100, 250, 500, 1000, 2500, 5000]` milliseconds

### Content Delivery

| Metric | Type | Description | Key Labels |
|--------|------|-------------|------------|
| `pulp_content_request_duration_milliseconds_bucket` | Histogram | Content delivery latency | `pod` |
| `pulp_content_request_duration_milliseconds_count` | Counter | Total content requests | `pod` |
| `pulp_content_request_duration_milliseconds_sum` | Counter | Total content request time | `pod` |
| `pulp_artifacts_size_counter_Bytes_total` | Counter | Total artifact bytes served | `pod` |

### Task Queue & Auto-Scaling

| Metric | Type | Description | Key Labels |
|--------|------|-------------|------------|
| `pulp_waiting_tasks` | Gauge | Tasks waiting in queue (KEDA trigger) | `pod` |

### External Integrations

| Metric | Type | Description | Key Labels |
|--------|------|-------------|------------|
| `content_sources_pulp_connectivity` | Gauge | Content Sources health | `source` |
| `content_sources_pulp_transform_logs_days_since_success` | Gauge | Data freshness | `source` |

### Infrastructure

| Metric | Type | Description |
|--------|------|-------------|
| `up` | Gauge | Target scrape status (1=up, 0=down) |

## Pre-Computed Recording Rules

Available at **9 time windows**: `5m`, `15m`, `1h`, `2h`, `3h`, `6h`, `12h`, `1d`, `3d`

| Recording Rule Pattern | Description |
|----------------------|-------------|
| `pulp_api_errors_bucket:rate{window}` | API error rates by time window |
| `pulp_api_latency_bucket:rate{window}` | API latency rates by time window |
| `pulp_content_errors_bucket:rate{window}` | Content error rates by time window |
| `pulp_content_latency_bucket:rate{window}` | Content latency rates by time window |

**Examples**:
- `pulp_api_errors_bucket:rate1h` — API errors over last 1 hour
- `pulp_content_latency_bucket:rate5m` — Content latency over last 5 minutes
- `pulp_api_errors_bucket:rate3d` — API errors over last 3 days

## Key Query Recipes

### Service Health

```promql
# Are all pods up?
up{namespace="pulp-stage"}

# Which pods are down?
up{namespace="pulp-stage"} == 0
```

### API Latency

```promql
# P50 API latency (5m window)
histogram_quantile(0.5, rate(pulp_api_request_duration_milliseconds_bucket[5m]))

# P95 API latency (5m window)
histogram_quantile(0.95, rate(pulp_api_request_duration_milliseconds_bucket[5m]))

# P99 API latency (5m window)
histogram_quantile(0.99, rate(pulp_api_request_duration_milliseconds_bucket[5m]))
```

### Request Rates

```promql
# API request rate (requests/sec, 5m window)
rate(pulp_api_request_duration_milliseconds_count[5m])

# API request rate by HTTP status code
sum by (http_status_code) (rate(pulp_api_request_duration_milliseconds_count[5m]))

# Content request rate
rate(pulp_content_request_duration_milliseconds_count[5m])
```

### Error Analysis

```promql
# API error rate (1h recording rule — most convenient)
pulp_api_errors_bucket:rate1h

# API 5xx error rate
sum(rate(pulp_api_request_duration_milliseconds_count{http_status_code=~"5.."}[5m]))

# API error ratio (errors / total)
sum(rate(pulp_api_request_duration_milliseconds_count{http_status_code=~"5.."}[5m]))
  /
sum(rate(pulp_api_request_duration_milliseconds_count[5m]))
```

### Active Connections

```promql
# Current active connections per pod
pulp_api_active_connections

# Total active connections
sum(pulp_api_active_connections)

# Connection trend (5m rate of change)
deriv(pulp_api_active_connections[5m])
```

### Auto-Scaling (Workers)

```promql
# Waiting tasks (KEDA trigger metric)
pulp_waiting_tasks

# KEDA's actual query (from ScaledObject config)
clamp_min(pulp_waiting_tasks and on(pod) topk(1, max(timestamp(pulp_waiting_tasks)) by (pod)), 0)
```

### Content Delivery

```promql
# P95 content delivery latency
histogram_quantile(0.95, rate(pulp_content_request_duration_milliseconds_bucket[5m]))

# Total bytes served
pulp_artifacts_size_counter_Bytes_total

# Bytes served rate (bytes/sec)
rate(pulp_artifacts_size_counter_Bytes_total[5m])
```

### External Health

```promql
# Content Sources connectivity
content_sources_pulp_connectivity

# Data freshness (days since last successful transform)
content_sources_pulp_transform_logs_days_since_success
```

## Shortcuts Reference

The `prom-query.py` script supports these shortcuts:

| Shortcut | PromQL |
|----------|--------|
| `health` | `up{namespace="<ns>"}` |
| `connections` | `pulp_api_active_connections` |
| `latency` | P95 API latency (5m histogram_quantile) |
| `errors` | `pulp_api_errors_bucket:rate1h` |
| `content` | P95 content latency (5m histogram_quantile) |
| `tasks` | `pulp_waiting_tasks` |
| `request-rate` | API request rate (5m) |
| `artifacts` | `pulp_artifacts_size_counter_Bytes_total` |
| `connectivity` | `content_sources_pulp_connectivity` |
| `all-metrics` | List all pulp_* metric names |
