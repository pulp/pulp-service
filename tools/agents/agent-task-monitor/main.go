// agent-task-monitor polls the Pulp tasking system of one environment, explains
// why tasks are waiting, keeps 30 days of history in S3, publishes an HTML
// status page and OTLP metrics, and tracks persistent unexplained-wait
// incidents in Jira. Any failure exits non-zero.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/tmc/langchaingo/llms"

	"github.com/pulp/pulp-service/tools/agents/shared/auth"
	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/shared/models"
)

type options struct {
	mode          string
	environment   string
	baseURL       string
	taskAPIPath   string
	statusPath    string
	retentionDays int
	maxGap        time.Duration
	start, end    string

	stateDir   string
	s3Bucket   string
	s3Prefix   string
	s3Endpoint string

	otelURL    string
	gitlabRepo string
	gitlabFile string
	outputFile string

	jira  JiraConfig
	model string
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func parseFlags() (*options, error) {
	o := &options{}
	var jiraLabels string
	flag.StringVar(&o.mode, "mode", "sync", "sync: poll Pulp and publish; backfill: re-fetch finished tasks for -start..-end; report: re-render the page from stored state")
	flag.StringVar(&o.environment, "environment", os.Getenv("MONITOR_ENVIRONMENT"), "environment name, used as the state key and the metrics 'environment' label (e.g. pulp-tasks-prod)")
	flag.StringVar(&o.baseURL, "base-url", os.Getenv("PULP_BASE_URL"), "Pulp base URL (e.g. https://packages.redhat.com)")
	flag.StringVar(&o.taskAPIPath, "task-api-path", "/api/pulp/admin/tasks/", "tasks API path")
	flag.StringVar(&o.statusPath, "status-path", "/api/pulp/api/v3/status/", "status API path")
	flag.IntVar(&o.retentionDays, "retention-days", 30, "days of snapshots and events to keep")
	flag.DurationVar(&o.maxGap, "max-gap", 24*time.Hour, "longest gap since the last sync that a sync fills; older data needs -mode backfill")
	flag.StringVar(&o.start, "start", "", "RFC 3339 window start (sync: override; backfill: required)")
	flag.StringVar(&o.end, "end", "", "RFC 3339 window end (default: now, aligned to 5 minutes for sync)")

	flag.StringVar(&o.stateDir, "state-dir", "", "keep state in this local directory instead of S3 (development)")
	flag.StringVar(&o.s3Bucket, "s3-bucket", os.Getenv("S3_BUCKET"), "S3 bucket for state and the status page")
	flag.StringVar(&o.s3Prefix, "s3-prefix", envOr("S3_PREFIX", "task-monitor/"), "key prefix inside the bucket")
	flag.StringVar(&o.s3Endpoint, "s3-endpoint", os.Getenv("S3_ENDPOINT"), "custom S3 endpoint (S3-compatible stores)")

	flag.StringVar(&o.otelURL, "otel-collector-url", os.Getenv("OTEL_COLLECTOR_URL"), "OTLP/HTTP collector base URL (metrics are pushed to <url>/v1/metrics)")
	flag.StringVar(&o.gitlabRepo, "gitlab-repo", "", "GitLab repository to publish the status page to (needs GITLAB_TOKEN)")
	flag.StringVar(&o.gitlabFile, "gitlab-file", "index.html", "path of the status page inside -gitlab-repo")
	flag.StringVar(&o.outputFile, "output", "", "also write the status page to this local file")

	flag.StringVar(&o.jira.Project, "jira-project", "", "enable Jira incident tracking in this project (needs JIRA_URL, JIRA_API_TOKEN=user:token and LLM credentials)")
	flag.StringVar(&o.jira.IssueType, "jira-issue-type", "Bug", "issue type for new incident issues")
	flag.StringVar(&jiraLabels, "jira-labels", "", "extra comma-separated labels for new incident issues")
	flag.StringVar(&o.jira.CommentGroup, "jira-comment-group", "Red Hat Employee", "restrict comment visibility to this group (empty: public)")
	flag.DurationVar(&o.jira.MinDuration, "jira-min-duration", 15*time.Minute, "how long an incident must persist before it is filed")
	flag.DurationVar(&o.jira.UpdateInterval, "jira-update-interval", time.Hour, "minimum time between progress comments")
	flag.IntVar(&o.jira.MaxCreatesPerDay, "jira-max-creates-per-day", 3, "cap on issues created in any 24h")
	flag.StringVar(&o.jira.StatusPageURL, "status-page-url", "", "public status page URL, linked from Jira")
	flag.StringVar(&o.model, "model", envOr("CLAUDE_MODEL", "claude-opus-4-6"), "LLM for Jira incident text (claude-* or gemini-*)")
	flag.Parse()

	if o.environment == "" || o.baseURL == "" {
		return nil, errors.New("-environment and -base-url are required")
	}
	if o.stateDir == "" && o.s3Bucket == "" {
		return nil, errors.New("set -s3-bucket (or S3_BUCKET), or -state-dir for local development")
	}
	switch o.mode {
	case "sync", "backfill", "report":
	default:
		return nil, fmt.Errorf("unknown -mode %q", o.mode)
	}
	if o.mode == "backfill" && o.start == "" {
		return nil, errors.New("-mode backfill needs -start")
	}
	for l := range strings.SplitSeq(jiraLabels, ",") {
		if l = strings.TrimSpace(l); l != "" {
			o.jira.Labels = append(o.jira.Labels, l)
		}
	}
	if o.jira.MaxCreatesPerDay < 1 {
		return nil, errors.New("-jira-max-creates-per-day must be at least 1")
	}
	return o, nil
}

func parseTimeFlag(name, value string) (*time.Time, error) {
	if value == "" {
		return nil, nil
	}
	t, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return nil, fmt.Errorf("-%s: %w", name, err)
	}
	return &t, nil
}

func main() {
	if err := run(); err != nil {
		log.Fatalf("agent-task-monitor failed: %v", err)
	}
}

func run() error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	o, err := parseFlags()
	if err != nil {
		return err
	}
	start, err := parseTimeFlag("start", o.start)
	if err != nil {
		return err
	}
	end, err := parseTimeFlag("end", o.end)
	if err != nil {
		return err
	}

	username, password := os.Getenv("PULP_USERNAME"), os.Getenv("PULP_PASSWORD")
	if username == "" || password == "" {
		return errors.New("PULP_USERNAME and PULP_PASSWORD are required")
	}
	pulp := newPulpClient(o.baseURL, o.taskAPIPath, o.statusPath, username, password)

	gitlabToken := os.Getenv("GITLAB_TOKEN")
	if o.gitlabRepo != "" && gitlabToken == "" {
		return errors.New("GITLAB_TOKEN is required with -gitlab-repo")
	}

	// Resolve Jira and LLM configuration up front so misconfiguration fails
	// before anything is written.
	var jira *jiraClient
	var runLLM llmRunner
	if o.jira.Project != "" && o.mode == "sync" {
		if jira, err = jiraFromEnv(); err != nil {
			return err
		}
		if runLLM, err = llmFromEnv(o.model); err != nil {
			return err
		}
	}

	var store Store
	if o.stateDir != "" {
		store = &localStore{dir: o.stateDir}
	} else if store, err = newS3Store(ctx, o.s3Bucket, o.s3Prefix, o.s3Endpoint); err != nil {
		return err
	}

	st, version, err := LoadState(ctx, store, o.environment, o.baseURL)
	if err != nil {
		return err
	}
	st.BaseURL = o.baseURL
	now := time.Now().UTC()
	retention := time.Duration(o.retentionDays) * 24 * time.Hour

	var res *SyncResult
	switch o.mode {
	case "sync":
		if res, err = Sync(ctx, pulp, st, SyncOptions{Start: start, End: end, MaxGap: o.maxGap, Now: now}); err != nil {
			return fmt.Errorf("sync: %w", err)
		}
		fmt.Fprintf(os.Stderr, "[sync] %s..%s: %d finished, %d waiting, %d running, %d workers, %d unexplained waits\n",
			res.WindowStart.Format(time.RFC3339), res.WindowEnd.Format(time.RFC3339), res.Finished, res.Waiting, res.Running, res.OnlineWorkers, res.UnexplainedWait)
	case "backfill":
		backfillEnd := now
		if end != nil {
			backfillEnd = *end
		}
		if err := Backfill(ctx, pulp, st, *start, backfillEnd, now); err != nil {
			return err
		}
	}

	// The page resolves resource names (cached in the state), so render it
	// before saving.
	Prune(st, now, retention)
	page, err := statusPage(ctx, pulp, st, now)
	if err != nil {
		return fmt.Errorf("render status page: %w", err)
	}
	st.UpdatedAt = now
	if version, err = SaveState(ctx, store, st, version); err != nil {
		return err
	}

	// Everything below runs even if an earlier step fails; any failure makes
	// the run exit non-zero.
	var errs []error
	publishErr := publishPage(ctx, store, o, gitlabToken, page)
	if publishErr != nil {
		errs = append(errs, publishErr)
	}

	if res != nil && o.otelURL != "" {
		// last_success only advances when the poll, state and page all succeeded.
		payload := buildOTLPPayload(res, o.environment, now, publishErr == nil)
		if err := pushMetrics(ctx, o.otelURL, payload); err != nil {
			errs = append(errs, err)
		} else {
			fmt.Fprintf(os.Stderr, "[metrics] pushed to %s\n", o.otelURL)
		}
	}

	if jira != nil && res != nil {
		before, _ := json.Marshal(st.Jira)
		if err := RunJira(ctx, o.jira, jira, runLLM, st, res, now); err != nil {
			errs = append(errs, err)
		}
		// Persist whatever happened (including partial progress such as a
		// created issue) so the next run does not repeat it.
		if after, _ := json.Marshal(st.Jira); string(after) != string(before) {
			if _, err := SaveState(ctx, store, st, version); err != nil {
				errs = append(errs, err)
			}
		}
	}

	summary := map[string]any{"environment": o.environment, "mode": o.mode, "status": "success", "jira_issue": st.Jira.IssueKey}
	if res != nil {
		summary["sync"] = res
	}
	if len(errs) > 0 {
		summary["status"] = "failed"
		summary["error"] = errors.Join(errs...).Error()
	}
	out, _ := json.Marshal(summary)
	fmt.Println(string(out))
	return errors.Join(errs...)
}

func publishPage(ctx context.Context, store Store, o *options, gitlabToken string, page []byte) error {
	var errs []error
	if err := store.Put(ctx, statusKey(o.environment), page, "text/html; charset=utf-8"); err != nil {
		errs = append(errs, fmt.Errorf("upload status page: %w", err))
	} else {
		fmt.Fprintf(os.Stderr, "[page] wrote %s\n", store.Describe(statusKey(o.environment)))
	}
	if o.outputFile != "" {
		if err := os.WriteFile(o.outputFile, page, 0o644); err != nil {
			errs = append(errs, fmt.Errorf("write %s: %w", o.outputFile, err))
		}
	}
	if o.gitlabRepo != "" {
		if err := publishToGitLab(ctx, o.gitlabRepo, gitlabToken, o.gitlabFile, o.environment, page); err != nil {
			errs = append(errs, fmt.Errorf("publish to GitLab: %w", err))
		}
	}
	return errors.Join(errs...)
}

func jiraFromEnv() (*jiraClient, error) {
	jiraURL := os.Getenv("JIRA_URL")
	username, token, ok := strings.Cut(os.Getenv("JIRA_API_TOKEN"), ":")
	if jiraURL == "" || !ok || username == "" || token == "" {
		return nil, errors.New("-jira-project needs JIRA_URL and JIRA_API_TOKEN (username:token)")
	}
	return newJiraClient(jiraURL, username, token), nil
}

func llmFromEnv(model string) (llmRunner, error) {
	authConfig, err := auth.ResolveFromEnv()
	if err != nil {
		return nil, fmt.Errorf("LLM auth: %w", err)
	}
	var m models.Model
	switch {
	case strings.HasPrefix(model, "claude-"):
		m = models.Claude{Model: model, ProjectID: authConfig.ProjectID, Region: authConfig.Region, APIKey: authConfig.APIKey, BaseURL: authConfig.BaseURL}
	case strings.HasPrefix(model, "gemini-"):
		m = models.Gemini{Model: model, ProjectID: authConfig.ProjectID, Region: authConfig.Region}
	default:
		return nil, fmt.Errorf("unsupported model %q (claude-* or gemini-*)", model)
	}
	return func(ctx context.Context, prompt string, tools []llms.Tool, mgr *mcpClient.MCPManager) (string, error) {
		ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
		defer cancel()
		return m.ModelRun(ctx, models.RunConfig{Prompt: prompt, Tools: tools, MCP: mgr, MaxIterations: 12})
	}, nil
}
