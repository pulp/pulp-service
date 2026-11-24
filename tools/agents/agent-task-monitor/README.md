# agent-task-monitor

Monitors the Pulp tasking system of one environment (e.g. packages.redhat.com). Each run polls the admin tasks API, explains *why* tasks are waiting, keeps 30 days of history in S3, publishes an HTML status page, pushes OTLP metrics, and tracks persistent unexplained-wait incidents in Jira.

It replaces the swamp-based [tasking-system-monitor-agent](https://gitlab.cee.redhat.com/hosted-pulp/tasking-system-monitor-agent) (`extensions/models/pulp_task_monitor.ts`), which needs a paid swamp-club account for non-personal use ([PULP-2729](https://redhat.atlassian.net/browse/PULP-2729)).

Polling, classification, metrics and the status page are plain Go: the pipeline has no LLM in it. The LLM is used only to write Jira incident text, through tools that code restricts (see [Jira incident tracking](#jira-incident-tracking)).

## What a sync does

1. Picks the window: from the end of the previous sync (stored in the state) to the last 5-minute boundary. A gap longer than `-max-gap` (24h) is truncated with a warning; use `-mode backfill` for older data.
2. Fetches tasks finished in the window, plus all `running` and `waiting` tasks, and the online worker count from the status API.
3. Classifies every waiting task, in order:
   1. **No resources requested:** capacity blocked.
   2. **Resource blocked:** a running task holds a conflicting lock (exclusive/exclusive, exclusive/shared or shared/exclusive).
   3. **Queue blocked:** an *earlier-created* waiting task contends for one of its resources.
   4. **Worker saturation:** all workers are busy (`running >= workers`). Counted as capacity blocked.
   5. **Scheduler lag:** created less than 30s ago. Counted as capacity blocked.
   6. **Unexplained wait:** none of the above apply. This points to orphaned locks, applock bugs or FIFO starvation. The task's resource queue is marked as an anomaly.
4. Records events: up to 10 `unexplained_wait` events per sync (the third and later are `critical`), and a `worker_saturation` event when tasks wait on busy workers.
5. Adds finished tasks to 5-minute snapshots and to daily summaries (counts, task types with durations, top resources), and stores the live poll on the current snapshot.
6. Saves the state, publishes the status page, pushes metrics, then runs the Jira step.

## Failures are visible

* **Non-zero exit on any failure.** Pulp, the status API, S3, page publishing, the metrics push and Jira all count. The swamp monitor ignored failed status and metrics calls. A missing worker count silently changed the classification.
* **Single-document state.** The state is one JSON document per environment, saved with a conditional write (`If-Match`/`If-None-Match`). A run commits all of its changes or none, and a concurrent run fails with a conflict instead of overwriting. A failed run does not advance the window, so the next run covers it.
* **Staleness metric.** `pulp_monitor_last_success_timestamp` only advances when the poll, the state save and the page publish all succeeded. Alert on it going stale to catch runs that fail or stop entirely.
* **JSON summary.** The last line on stdout is a JSON summary with `status` set to `success` or `failed`.

## Outputs

**State and page:** `s3://<bucket>/<prefix><environment>/state.json` and `.../index.html`. With `-state-dir`, the same paths are used in a local directory.

| State field | Contents | Retention |
|---|---|---|
| `snapshots` | 5-minute buckets of finished counts. Buckets that held a poll also have waiting, running, workers, saturation, classification and (latest only) resource queues | `-retention-days` (30) |
| `events` | `unexplained_wait` and `worker_saturation` events | `-retention-days` (30) |
| `daily` | Per-UTC-day counts, durations, task types, top 50 resources, poll saturation, event count. The monthly table is derived from these | 400 days |
| `resource_names` | Cache of resolved domain and repository names | 24h per entry |
| `jira` | Tracked incident issue and recent creations | n/a |

**Status page:** the same self-contained page as the swamp monitor. It has a health banner, current state panels with the waiting-tasks-by-resource overlay, throughput over six windows, a 30-day calendar with per-day details, unexplained-wait incidents, and monthly throughput. It can also be committed to a GitLab repository for GitLab Pages (`-gitlab-repo`, `-gitlab-file`) and written locally (`-output`).

**Metrics** (OTLP/HTTP JSON to `<collector>/v1/metrics`): the names and the `environment` label match the swamp monitor, so dashboards and alerts keep working.

| Metric | Labels |
|---|---|
| `pulp_monitor_last_success_timestamp` | `environment` |
| `pulp_tasks_waiting`, `pulp_tasks_running`, `pulp_workers_online`, `pulp_worker_saturation`, `pulp_unexplained_wait_tasks` | `environment` |
| `pulp_tasks_completed_total`, `pulp_tasks_failed_total` (per sync window) | `environment`, `task_type` |

## Jira incident tracking

This step is enabled with `-jira-project`. Incidents are `unexplained_wait` detections no more than 10 minutes apart. An incident is ongoing until no unexplained wait has been seen for 10 minutes.

Code decides when to act:

* **open:** an ongoing incident has lasted at least `-jira-min-duration` (15m) and isn't tracked yet.
* **update:** the tracked incident is still ongoing and `-jira-update-interval` (1h) has passed since the last comment.
* **resolve:** the tracked incident ended. The model posts a resolution comment. Closing the issue is left to a human.

The LLM writes the text and decides whether an existing open issue already covers the incident. Its tools are guarded in code:

| Tool | Guardrail |
|---|---|
| `search_incident_issues` | Takes no arguments. The JQL is fixed: `project = <project> AND labels = pulp-task-monitor-<env>-unexplained-wait AND statusCategory != Done` |
| `get_issue_comments` | Only issues returned by the search, or the tracked issue |
| `create_incident_issue` | Requires a prior search with no open issue. Project, issue type and labels (fingerprint, `pulp-task-monitor`, `-jira-labels`) are set by code. At most 1 per run and `-jira-max-creates-per-day` (3) per 24h |
| `add_incident_comment` | Only issues returned by the search, or the tracked issue. At most 2 per run. Visibility is restricted to `-jira-comment-group` ("Red Hat Employee") |

There are no transition, edit or delete tools. The model only sees aggregate counts, task types and task UUIDs. Resource names and domain names in task hrefs are left out. If the model does not do what the action requires (create or comment), the run fails.

## Usage

```bash
# Build and test (from tools/agents)
make build-task-monitor test-task-monitor

# Local development: state in a directory, page written locally
PULP_USERNAME=... PULP_PASSWORD=... ./agent-task-monitor/agent-task-monitor \
  -environment pulp-tasks-stage -base-url https://packages.stage.redhat.com \
  -state-dir /tmp/task-monitor -output /tmp/task-monitor/status.html

# Production-like: state in S3, metrics, GitLab Pages, Jira
./agent-task-monitor -environment pulp-tasks-prod -base-url https://packages.redhat.com \
  -s3-bucket hosted-pulp-tasking-monitor \
  -otel-collector-url http://pulp-task-monitor-otel-collector.pulp-stage.svc.cluster.local:4318 \
  -gitlab-repo https://gitlab.cee.redhat.com/hosted-pulp/tasking-system-monitor-agent \
  -jira-project PULP

# Re-fetch finished tasks for whole days (idempotent; keeps live poll data)
./agent-task-monitor -mode backfill -start 2026-11-01T00:00:00Z ...

# Re-render and publish the page from stored state, without polling
./agent-task-monitor -mode report ...
```

Run `-help` for all flags. Each environment (prod, stage) is a separate run with its own `-environment` and its own state.

### Container

```bash
# from tools/agents (the build needs ./shared and go.work)
podman build -f agent-task-monitor/Containerfile -t agent-task-monitor .
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `PULP_USERNAME`, `PULP_PASSWORD` | Yes | Pulp admin (read-only) credentials, sent with basic auth |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | With S3 | Credentials for the state bucket (standard AWS chain) |
| `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT` | No | Defaults for `-s3-bucket`, `-s3-prefix` (`task-monitor/`), `-s3-endpoint` |
| `MONITOR_ENVIRONMENT`, `PULP_BASE_URL`, `OTEL_COLLECTOR_URL` | No | Defaults for `-environment`, `-base-url`, `-otel-collector-url` |
| `GITLAB_TOKEN` | With `-gitlab-repo` | Token that can push to the Pages repository |
| `JIRA_URL`, `JIRA_API_TOKEN` (`username:token`) | With `-jira-project` | Jira Cloud REST API |
| `ANTHROPIC_API_KEY` + optional `ANTHROPIC_BASE_URL`, or `ANTHROPIC_VERTEX_PROJECT_ID` / `VERTEX_SA_JSON` + optional `CLOUD_ML_REGION` | With `-jira-project` | LLM credentials (see `shared/auth`) |
| `CLAUDE_MODEL` | No | Default for `-model` (`claude-opus-4-6`) |

## Differences from the swamp monitor

* **Window tracking:** stored explicitly as `last_window_end`, instead of being inferred from snapshot ends.
* **Snapshot buckets:** merged across runs instead of overwritten. A bucket split across two syncs used to lose the first part.
* **Daily summaries:** merged in place, one per day, instead of one record per sync. Backfill replaces whole days, so re-running it never double counts.
* **Monthly figures:** derived from daily summaries kept for 400 days. Previously they were recomputed from the 30 days of snapshots, which shrank a month once part of it aged out. Averages are now weighted by task count.
* **Calendar counts:** exact per-day counts by `finished_at`, instead of prorated snapshot counts.
* **Resource names:** cached for 24h instead of being looked up on every run. Snapshots keep raw PRNs, and names are formatted when the page is rendered.
* **Task queries:** request only the fields the monitor uses, retry transient errors, and split long windows into 3-hour chunks.
* **Removed methods:** the swamp `status` and `history` methods were placeholders and are not ported.
