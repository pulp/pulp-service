# agent-splunk

An AI agent that queries Splunk for 5xx errors, analyzes them using an LLM (Claude or Gemini via Vertex AI), and automatically creates or updates Jira issues based on the findings.

## Prerequisites

- Go 1.25+
- Authentication configured for your chosen provider (see below)
- Access to a Splunk instance
- MCP servers: either a running [MCP Atlassian](Containerfile.mcp-atlassian) server (`JIRA_MCP_URL`) or an `ALCOVE_MCP_CONFIG` with stdio-based MCP servers

## Environment Variables

The agent supports two authentication methods for Claude models:

**Option 1: Direct Anthropic API** — set `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_BASE_URL`).

**Option 2: Vertex AI** — set `ANTHROPIC_VERTEX_PROJECT_ID` and configure Google Cloud credentials.

If both `ANTHROPIC_API_KEY` and `ANTHROPIC_VERTEX_PROJECT_ID` are set, the direct API is used. Gemini models always require Vertex AI.

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for direct API access |
| `ANTHROPIC_BASE_URL` | Custom base URL for the Anthropic API (optional) |
| `ANTHROPIC_VERTEX_PROJECT_ID` | Google Cloud project ID for Vertex AI |
| `CLOUD_ML_REGION` | Vertex AI region (default: `us-east5`) |
| `SPLUNK_URL` | Splunk instance URL |
| `SPLUNK_TOKEN` | Splunk auth token |
| `ALCOVE_MCP_CONFIG` | JSON config for stdio MCP servers (used by Alcove, takes precedence over `JIRA_MCP_URL`) |
| `JIRA_MCP_URL` | URL of the Jira MCP server (standalone mode) |

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
