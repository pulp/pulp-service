# agent-akamai-report

An AI agent that queries the Akamai access logs in Sumo Logic (`_index=rh_akamai`) to produce 7-day traffic analysis reports for hosted Pulp. It fetches traffic data (reqHost, base_path, request count), saves the raw JSON, generates an interactive HTML report, and uses an LLM (Claude or Gemini via Vertex AI) to produce analysis.

When configured with `--gitlab-repo`, the agent automatically publishes reports to a GitLab repository for serving via GitLab Pages, with a date-based index for browsing historical reports.

## Prerequisites

- Go 1.25+
- Google Cloud credentials configured (for Vertex AI access), or an Anthropic API key (proxy mode)
- A Sumo Logic access ID/key with read access to `_index=rh_akamai`
- (Optional) GitLab access token for publishing reports

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SUMO_API_URL` | Yes | Sumo Logic API endpoint for the deployment (e.g. `https://api.sumologic.com`, `https://api.us2.sumologic.com`) |
| `SUMO_ACCESS_ID` | Yes | Sumo Logic access ID |
| `SUMO_ACCESS_KEY` | Yes | Sumo Logic access key |
| `GITLAB_TOKEN` | With `--gitlab-repo` | GitLab personal access token for publishing reports |
| `ANTHROPIC_API_KEY` | One of | API key for proxy mode |
| `ANTHROPIC_BASE_URL` | No | Base URL for proxy mode |
| `ANTHROPIC_VERTEX_PROJECT_ID` | One of | Google Cloud project ID for Vertex AI |
| `VERTEX_SA_JSON` | No | Service account JSON for Vertex AI (alternative to project ID) |
| `CLOUD_ML_REGION` | No | Vertex AI region (default: `global`) |
| `CLAUDE_MODEL` | No | Override default model (default: `claude-opus-4-6`) |

**Anthropic / Vertex AI configuration:**
Provide either Anthropic proxy configuration or Vertex AI configuration:
- Anthropic proxy: `ANTHROPIC_API_KEY` (required) and optional `ANTHROPIC_BASE_URL`
- Vertex AI: `ANTHROPIC_VERTEX_PROJECT_ID` (required) and optional `VERTEX_SA_JSON` and `CLOUD_ML_REGION`

## Usage

```bash
# Build
go build -o agent-akamai-report .

# Run with defaults (queries Sumo Logic, saves to /tmp/traffic-report-<timestamp>/traffic.json)
./agent-akamai-report

# Run with a custom output directory
./agent-akamai-report -output-dir /tmp/my-report

# Run and publish to GitLab Pages
./agent-akamai-report -gitlab-repo https://gitlab.cee.redhat.com/hosted-pulp/traffic-report

# Run with a custom question about the data
./agent-akamai-report -question "which base paths have the most traffic on packages.redhat.com?"

# Run with Gemini
./agent-akamai-report -model gemini-2.5-pro
```

### Container

```bash
# from tools/agents (the build needs ./shared and go.work)
podman build -f agent-akamai-report/Containerfile -t agent-akamai-report .
podman run --env-file .env agent-akamai-report
```

## What It Does

1. Queries Sumo Logic for Akamai access logs from the last 7 days (see `trafficQuery` in `traffic.go`)
2. Aggregates traffic by `reqhost`, `base_path` (first three path segments) and `path_type`, sorted by request count (all groups; the LLM receives the top 100 plus overall totals)
3. Saves the results to `traffic.json` in the output directory (keys `reqHost`, `base_path`, `path_type`, `count`, unchanged from the Splunk-based reports, plus `warnings` when Sumo Logic returned any)
4. Generates an interactive HTML report with sorting, filtering, and dark mode
5. Feeds the data to an LLM that produces a formatted table and analysis
6. The LLM gets no tools: it only summarizes the data it is given (request paths in the logs are untrusted text)
7. (Optional) Publishes HTML report and JSON data to a GitLab repository

## GitLab Pages Publishing

When `--gitlab-repo` is provided (along with `GITLAB_TOKEN`), the agent:

1. Clones the target GitLab repository
2. Copies `traffic.html` and `traffic.json` to `public/reports/YYYY-MM-DD/`
3. Generates an `index.html` at `public/index.html` listing all available report dates
4. Ensures a `.gitlab-ci.yml` exists for GitLab Pages deployment
5. Commits and pushes changes

Reports are organized by date and accessible through the index page. The agent is scheduled to run daily via Alcove, building up a browsable history of traffic reports.
