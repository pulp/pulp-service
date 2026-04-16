# agent-splunk

An AI agent that queries Splunk for 5xx errors, analyzes them using an LLM (Claude or Gemini via Vertex AI), and automatically creates or updates Jira issues based on the findings.

## Prerequisites

- Go 1.25+
- Google Cloud credentials configured (for Vertex AI access)
- Access to a Splunk instance
- A Jira Cloud instance with API token access

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_VERTEX_PROJECT_ID` | Google Cloud project ID for Vertex AI |
| `CLOUD_ML_REGION` | Vertex AI region (default: `us-east5`) |
| `SPLUNK_URL` | Splunk instance URL |
| `SPLUNK_TOKEN` | Splunk auth token |
| `JIRA_URL` | Jira instance URL (e.g. `https://redhat.atlassian.net`) |
| `JIRA_USERNAME` | Jira username (email) for API authentication |
| `JIRA_API_TOKEN` | Jira API token for authentication |

## Usage

```bash
# Build
go build -o agent-splunk .

# Run with defaults (Claude, default Splunk 5xx errors in the last hour query)
./agent-splunk

# Run with a custom question
./agent-splunk -question "check for 5xx errors in the last 30 minutes"

# Run with Gemini
./agent-splunk -model gemini-2.5-pro
```

### Container

```bash
podman build -f Containerfile -t agent-splunk .
podman run --env-file .env agent-splunk
```
