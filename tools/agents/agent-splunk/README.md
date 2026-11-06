# agent-splunk

An AI agent that queries the Akamai edge logs in Sumo Logic for 5xx errors, analyzes them using an LLM (Claude or Gemini via Vertex AI), and automatically creates or updates Jira issues based on the findings.

## Request tracing

For each 5xx error group seen at the Akamai edge, the agent traces sample requests across the three hops
and reports which one emitted the error (`akamai-edge`, `nsgw`, `pulp-api`, `pulp-content`). `pulp-worker` never
answers HTTP requests, so it is never the origin; it is reported as a contributing cause when a worker task in the
same domain overlaps a timed-out request:

```
client → Akamai (Sumo Logic: _index=rh_akamai) → NSGW Envoy gateway (CloudWatch: <cluster>.openshift-ingress) → Pulp (CloudWatch: <cluster>.pulp-<env>)
```

| Tool | Data source |
|---|---|
| `sumo_search` (`shared/sumo`) | Akamai DataStream access logs (Sumo Logic `_index=rh_akamai`; lowercase fields `reqhost`, `reqpath`, `statuscode`) |
| `cloudwatch_trace_logs` `source=nsgw` | `pulpp01ue1.openshift-ingress` / `pulps01ue1.openshift-ingress` (gateway pods only) |
| `cloudwatch_trace_logs` `source=pulp` | `crcp01ue1.pulp-prod`, `pulpp01ue1.pulp-prod` / `crcs02ue1.pulp-stage`, `pulps01ue1.pulp-stage` |

Requests are correlated by method, path, and timestamp (± 2 min); the Akamai client IP appears in the
Envoy `x-forwarded-for` field, and pulp-api correlation IDs link the access line to tracebacks.
The origin is decided from the Envoy response flags and details (see `skills/trace-requests.md`,
which is embedded into the system prompt). Jira issues are grouped by error origin, HTTP method, and endpoint.

CloudWatch is optional: without AWS credentials, the agent still analyzes the Akamai logs but reports the origin as
`unknown (trace unavailable)`.

## Prerequisites

- Go 1.25+
- Google Cloud credentials configured (for Vertex AI access)
- A Sumo Logic access ID/key with read access to `_index=rh_akamai`
- A Jira Cloud instance with API token access

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_VERTEX_PROJECT_ID` | Google Cloud project ID for Vertex AI |
| `CLOUD_ML_REGION` | Vertex AI region (default: `us-east5`) |
| `SUMO_API_URL` | Sumo Logic API endpoint for the deployment (e.g. `https://api.sumologic.com`, `https://api.us2.sumologic.com`) |
| `SUMO_ACCESS_ID` | Sumo Logic access ID |
| `SUMO_ACCESS_KEY` | Sumo Logic access key |
| `JIRA_URL` | Jira instance URL (e.g. `https://redhat.atlassian.net`) |
| `JIRA_API_TOKEN` | Jira credentials in `email:api_token` format (e.g. `user@company.com:ATATT3x...`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Optional. Read-only credentials for the `app-sre-logs` AWS account (`us-east-1`) to trace requests through the NSGW and Pulp logs. Locally: `rh-aws-saml-login app-sre-logs` |

## Usage

```bash
# Build (agent-splunk is not part of tools/agents/go.work)
GOWORK=off go build -o agent-splunk .

# Test
GOWORK=off go test ./...

# Run with defaults (Claude, Akamai 5xx errors in the last hour)
./agent-splunk

# Run with a custom question
./agent-splunk -question "check for 5xx errors in the last 30 minutes"

# Dry run: analysis and request tracing only, no Jira issues searched/created/commented
# (Jira tools are not registered, so JIRA_URL / JIRA_API_TOKEN are not needed)
./agent-splunk --dry-run

# Run with Gemini
./agent-splunk -model gemini-2.5-pro
```

### Container

```bash
# from tools/agents (the build needs ../shared)
podman build -f agent-splunk/Containerfile -t agent-splunk .
podman run --env-file .env agent-splunk
```
