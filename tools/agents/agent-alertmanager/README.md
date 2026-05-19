# agent-alertmanager

An AI agent that queries Prometheus Alertmanager for firing Pulp alerts, analyzes them using an LLM (Claude or Gemini via Vertex AI), and automatically creates or updates Jira issues based on the findings.

## Prerequisites

- Go 1.25+
- Google Cloud credentials configured (for Vertex AI access)
- Access to a Prometheus Alertmanager instance
- A running MCP Atlassian server (for Jira integration)

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_VERTEX_PROJECT_ID` | yes | Google Cloud project ID for Vertex AI |
| `CLOUD_ML_REGION` | no | Vertex AI region (default: `us-east5`) |
| `ALERTMANAGER_CLUSTERS` | no* | JSON array of cluster configs (see Multi-Cluster below). *Required if `ALERTMANAGER_URL` not set |
| `ALERTMANAGER_URL` | no* | Alertmanager API base URL (single-cluster fallback). *Required if `ALERTMANAGER_CLUSTERS` not set |
| `ALERTMANAGER_TOKEN` | no* | Bearer token for Alertmanager auth (single-cluster fallback) |
| `ALERTMANAGER_INSECURE_SKIP_VERIFY` | no | Skip TLS verification in single-cluster mode (default: `false`) |
| `JIRA_MCP_URL` | no | Jira MCP server URL |
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
  {"name": "prod", "url": "https://am-prod.example.com", "token": "bearer-token-1"},
  {"name": "stage", "url": "https://am-stage.example.com", "token": "bearer-token-2", "insecure_skip_verify": true}
]'
```

### Cluster config fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Identifier for this cluster (lowercase alphanumeric + hyphens) |
| `url` | yes | Alertmanager API base URL |
| `token` | yes | Bearer token for authentication |
| `insecure_skip_verify` | no | Skip TLS verification (default: `false`) |

### Backwards compatibility

If `ALERTMANAGER_CLUSTERS` is not set, the agent falls back to `ALERTMANAGER_URL` + `ALERTMANAGER_TOKEN` as a single cluster named "default". In single-cluster mode, the global `ALERTMANAGER_INSECURE_SKIP_VERIFY` env var controls TLS.

In multi-cluster mode, `ALERTMANAGER_INSECURE_SKIP_VERIFY` is ignored — configure TLS per cluster.

### Cluster name rules

- Must match `^[a-z0-9][a-z0-9-]*$`
- Must be unique
- Names are identifiers — renaming a cluster causes re-filing of active alerts

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
