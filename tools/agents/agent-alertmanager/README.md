# agent-alertmanager

An AI agent that queries Prometheus Alertmanager for firing Pulp alerts, gathers diagnostic context from K8s, Prometheus, GlitchTip, and CloudWatch, analyzes them using an LLM (Claude or Gemini via Vertex AI), and automatically creates or updates Jira issues based on the findings.

## Prerequisites

- Go 1.25+
- Google Cloud credentials configured (for Vertex AI access)
- Access to a Prometheus Alertmanager instance

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_VERTEX_PROJECT_ID` | yes | Google Cloud project ID for Vertex AI |
| `CLOUD_ML_REGION` | no | Vertex AI region (default: `us-east5`) |
| `ALERTMANAGER_CLUSTERS` | no* | JSON array of cluster configs (see Multi-Cluster below). *Required if `ALERTMANAGER_URL` not set |
| `ALERTMANAGER_URL` | no* | Alertmanager API base URL (single-cluster fallback). *Required if `ALERTMANAGER_CLUSTERS` not set |
| `ALERTMANAGER_TOKEN` | no* | Bearer token for Alertmanager auth (single-cluster fallback) |
| `ALERTMANAGER_INSECURE_SKIP_VERIFY` | no | Skip TLS verification in single-cluster mode (default: `false`) |
| `JIRA_URL` | no | Jira REST API base URL (e.g. `https://redhat.atlassian.net/`) |
| `JIRA_API_TOKEN` | no | Jira auth as `email:token` (required if `JIRA_URL` is set) |
| `GLITCHTIP_TOKEN` | no | GlitchTip bearer token for application error tracking |
| `GLITCHTIP_CONFIG` | no | Full GlitchTip config JSON (overrides `GLITCHTIP_TOKEN`) |
| `AWS_ACCESS_KEY_ID` | no | AWS credentials for CloudWatch Log Insights (SDK also supports IMDS, SSO, profiles) |
| `AWS_SECRET_ACCESS_KEY` | no | AWS credentials for CloudWatch Log Insights |
| `AWS_DEFAULT_REGION` | no | AWS region (default: `us-east-1`) |
| `DEDUP_COOLDOWN` | no | Alert dedup cache TTL (default: `30m`) |
| `DEDUP_CACHE_PATH` | no | Dedup cache file path (default: `/tmp/agent-alertmanager-cache.json`) |

## Usage

```bash
# Build (from tools/agents/)
make build-alertmanager

# Check for alerts (lightweight, no LLM)
./agent-alertmanager --check-only

# Full analysis with default model (Sonnet)
./agent-alertmanager

# Full analysis with Opus
./agent-alertmanager --model claude-opus-4-6

# Dry run (analysis only, no Jira mutations)
./agent-alertmanager --dry-run

# Custom question
./agent-alertmanager --question "check for OOMKilled alerts in the last 30 minutes"
```

### Container

```bash
cd tools/agents
podman build -f agent-alertmanager/Containerfile -t agent-alertmanager .
podman run --env-file .env agent-alertmanager
```

## Multi-Cluster Configuration

Configure multiple Alertmanager instances via the `ALERTMANAGER_CLUSTERS` env var:

```bash
export ALERTMANAGER_CLUSTERS='[
  {
    "name": "crcp",
    "url": "https://alertmanager.crcp01ue1.devshift.net",
    "token": "sha256~...",
    "api_server_url": "https://api.crcp01ue1.o9m8.p1.openshiftapps.com:6443",
    "prometheus_url": "https://prometheus.crcp01ue1.devshift.net",
    "namespace": "pulp-prod"
  },
  {
    "name": "crcs",
    "url": "https://alertmanager.crcs02ue1.devshift.net",
    "token": "sha256~...",
    "api_server_url": "https://api.crcs02ue1.urby.p1.openshiftapps.com:6443",
    "prometheus_url": "https://prometheus.crcs02ue1.devshift.net",
    "namespace": "pulp-stage"
  }
]'
```

### Cluster config fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Identifier for this cluster (lowercase alphanumeric + hyphens) |
| `url` | yes | Alertmanager API base URL |
| `token` | yes | Bearer token for authentication (shared across Alertmanager, K8s API, and Prometheus) |
| `insecure_skip_verify` | no | Skip TLS verification (default: `false`) |
| `api_server_url` | no | Kubernetes API server URL (enables `k8s_get_pod_info` tool) |
| `prometheus_url` | no | Prometheus URL (enables `prometheus_query` tool) |
| `namespace` | no | Default namespace for K8s and Prometheus queries |

### Backwards compatibility

If `ALERTMANAGER_CLUSTERS` is not set, the agent falls back to `ALERTMANAGER_URL` + `ALERTMANAGER_TOKEN` as a single cluster named "default". In single-cluster mode, the global `ALERTMANAGER_INSECURE_SKIP_VERIFY` env var controls TLS.

In multi-cluster mode, `ALERTMANAGER_INSECURE_SKIP_VERIFY` is ignored — configure TLS per cluster.

The new fields (`api_server_url`, `prometheus_url`, `namespace`) are optional. Without them, the corresponding diagnostic tools are not registered and the agent works with alert metadata only.

### Cluster name rules

- Must match `^[a-z0-9][a-z0-9-]*$`
- Must be unique
- Names are identifiers — renaming a cluster causes re-filing of active alerts

## Diagnostic Data Sources

The agent gathers context from 4 data sources during Phase 1 (analysis). Each source is optional — the agent works with whatever is available.

| Tool | Source | Speed | Use Case |
|---|---|---|---|
| `k8s_get_pod_info` | Kubernetes API | < 1s | Pod status, restarts, OOMKilled details, Warning events. Lists all pods if no pod name given. |
| `prometheus_query` | Prometheus | < 2s | Metrics with shortcuts (`health`, `errors`, `latency`, `connections`, `content`, `tasks`, `request-rate`) or raw PromQL |
| `glitchtip_get_errors` | GlitchTip | < 2s | Application exceptions filtered by alert time window |
| `cloudwatch_query_logs` | CloudWatch | 15-30s | Log Insights with shortcuts (`errors`, `5xx`, `worker-errors`, `status-codes`, `latency`) or raw queries |

### Tool routing

The LLM uses `diagnostic-routing.md` to decide which tools to call based on alert type:

| Alert Type | Primary | Secondary |
|---|---|---|
| OOMKill / Crash | k8s_get_pod_info, cloudwatch_query_logs | prometheus_query |
| High error rate | glitchtip_get_errors, prometheus_query | cloudwatch_query_logs |
| Service down | k8s_get_pod_info | prometheus_query, cloudwatch_query_logs |
| RDS storage | cloudwatch_query_logs | prometheus_query |

### Phase 2 filtering

During Phase 2 (Jira triage), diagnostic tools are filtered out — only Jira tools are available. This prevents the LLM from re-querying data sources during ticket creation.

## Alerts Covered

| Alert | Severity | Description |
|---|---|---|
| PulpApiDown | critical | API pods down |
| PulpContentDown | critical | Content pods down |
| PulpWorkerDown | critical | Worker pods down |
| PulpCrashing | medium | Container restarts |
| PulpOOMKilled | medium | Container OOMKilled |
| PulpApiError*BudgetBurn | medium | API error rate SLO burn |
| PulpContentError*BudgetBurn | medium | Content error rate SLO burn |
| PulpProdServiceRDSLowStorageSpace | critical | RDS storage low |

## Guardrails

- **Dedup cache:** Alerts are tracked by fingerprint to prevent re-analysis within the cooldown window (default: 30 minutes)
- **Concurrency guard:** Only one full agent instance runs at a time via file lock
- **Creation caps:** Max 5 issues/run, 10/hour, 20/day
- **Latency alerts excluded:** PulpApiLatency* and PulpContentLatency* alerts are filtered out in v1
