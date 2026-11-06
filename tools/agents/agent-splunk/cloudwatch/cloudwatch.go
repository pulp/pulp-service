// Package cloudwatch exposes a CloudWatch Logs Insights tool that lets the
// agent follow a request past the Akamai edge: first through the north-south
// gateway (NSGW, Envoy access logs in openshift-ingress) and then into the
// Pulp application logs (pulp-api, pulp-content, pulp-worker).
package cloudwatch

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	cwTypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"
	"github.com/tmc/langchaingo/llms"

	mcpClient "agent-splunk/mcp-client"
	"agent-splunk/utils"
)

const (
	SourceNSGW = "nsgw"
	SourcePulp = "pulp"

	defaultTimeRange = 15 * time.Minute
	maxWindow        = 24 * time.Hour
	defaultLimit     = 50
	maxLimit         = 200
	maxOutputBytes   = 25000
	queryTimeout     = 90 * time.Second
	pollInterval     = 2 * time.Second
)

// nsgwStreamFilter restricts NSGW queries to the gateway pods. The
// openshift-ingress namespace also runs other pods (e.g. the default router).
const nsgwStreamFilter = "filter @logStream like /cluster-default-gateway-openshift-default/"

// envoyParse extracts the fields of the NSGW Envoy access log line.
const envoyParse = `parse message /\[(?<start>[^\]]+)\] "(?<method>\S+) (?<path>\S+) (?<proto>[^"]+)" (?<status>\d+) (?<flags>\S+) (?<details>\S+) (?<term>\S+) "(?<upstream_failure>[^"]*)" (?<bytes_in>\d+) (?<bytes_out>\d+) (?<duration>\d+) (?<upstream_time>\S+) "(?<xff>[^"]*)" "(?<ua>[^"]*)" "(?<request_id>[^"]*)" "(?<authority>[^"]*)" "(?<upstream_host>[^"]*)" (?<upstream_cluster>\S+)/`

// envoyFields selects the output columns after envoyParse. It must use
// "display", not "fields": listing parsed fields in "fields" redefines them and
// Logs Insights rejects the query with "Ephemeral field is already defined".
const envoyFields = "display @timestamp, kubernetes.pod_name, method, path, status, flags, details, upstream_failure, " +
	"duration, upstream_time, upstream_host, upstream_cluster, request_id, xff, ua"

// LogGroups maps source -> environment -> CloudWatch log groups.
var LogGroups = map[string]map[string][]string{
	SourceNSGW: {
		"prod":  {"pulpp01ue1.openshift-ingress"},
		"stage": {"pulps01ue1.openshift-ingress"},
	},
	SourcePulp: {
		"prod":  {"crcp01ue1.pulp-prod", "pulpp01ue1.pulp-prod"},
		"stage": {"crcs02ue1.pulp-stage", "pulps01ue1.pulp-stage"},
	},
}

// LogsAPI is the subset of the CloudWatch Logs client used by this package.
type LogsAPI interface {
	StartQuery(ctx context.Context, params *cloudwatchlogs.StartQueryInput,
		optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.StartQueryOutput, error)
	GetQueryResults(ctx context.Context, params *cloudwatchlogs.GetQueryResultsInput,
		optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.GetQueryResultsOutput, error)
}

// Client runs Logs Insights queries against the NSGW and Pulp log groups.
type Client struct {
	logsAPI LogsAPI
	now     func() time.Time
}

// NewClientFromEnv builds a client using the default AWS credential chain
// (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY for the app-sre-logs account).
func NewClientFromEnv(ctx context.Context) (*Client, error) {
	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion("us-east-1"))
	if err != nil {
		return nil, fmt.Errorf("load AWS config: %w", err)
	}
	if _, err := cfg.Credentials.Retrieve(ctx); err != nil {
		return nil, fmt.Errorf("no AWS credentials: %w", err)
	}
	return NewClient(cloudwatchlogs.NewFromConfig(cfg)), nil
}

// NewClient builds a client around an existing LogsAPI implementation.
func NewClient(api LogsAPI) *Client {
	return &Client{logsAPI: api, now: time.Now}
}

// quote returns s as a Logs Insights double-quoted string literal.
func quote(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return `"` + s + `"`
}

func pulpStreamFilter(env string) string {
	return fmt.Sprintf("filter @logStream like /pulp-%s_pulp-(api|content|worker)/", env)
}

// ExpandQuery turns a shortcut into a full Logs Insights query. Raw queries are
// passed through; for the NSGW source the gateway stream filter is prepended
// when missing so other openshift-ingress pods never leak into the results.
func ExpandQuery(source, env, query, match string, limit int) (string, error) {
	matchFilter := ""
	if match != "" {
		matchFilter = "| filter message like " + quote(match) + "\n"
	}
	needsMatch := func(name string) error {
		if match == "" {
			return fmt.Errorf("shortcut %q requires the 'match' argument", name)
		}
		return nil
	}

	switch source {
	case SourceNSGW:
		switch query {
		case "raw":
			return fmt.Sprintf("%s\n%s| fields @timestamp, kubernetes.pod_name, message\n| sort @timestamp desc\n| limit %d",
				nsgwStreamFilter, matchFilter, limit), nil
		case "errors":
			return fmt.Sprintf("%s\n%s| %s\n| filter status >= 500 or status = 0\n"+
				"| stats count(*) as n by status, flags, details, upstream_cluster\n| sort n desc",
				nsgwStreamFilter, matchFilter, envoyParse), nil
		case "errors-list":
			return fmt.Sprintf("%s\n%s| %s\n| filter status >= 500 or status = 0\n| %s\n| sort @timestamp desc\n| limit %d",
				nsgwStreamFilter, matchFilter, envoyParse, envoyFields, limit), nil
		case "trace":
			if err := needsMatch(query); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s\n%s| %s\n| %s\n| sort @timestamp asc\n| limit %d",
				nsgwStreamFilter, matchFilter, envoyParse, envoyFields, limit), nil
		}
		if !strings.Contains(query, "cluster-default-gateway-openshift-default") {
			return nsgwStreamFilter + "\n| " + strings.TrimPrefix(strings.TrimSpace(query), "|"), nil
		}
		return query, nil

	case SourcePulp:
		stream := pulpStreamFilter(env)
		switch query {
		case "trace":
			if err := needsMatch(query); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s\n%s| fields @timestamp, kubernetes.pod_name, message\n| sort @timestamp asc\n| limit %d",
				stream, matchFilter, limit), nil
		case "5xx":
			return fmt.Sprintf("%s\n%s| filter message like /\" 5\\d\\d /\n"+
				"| fields @timestamp, kubernetes.pod_name, message\n| sort @timestamp asc\n| limit %d",
				stream, matchFilter, limit), nil
		case "errors":
			// Case-sensitive on purpose: a case-insensitive "error" also matches
			// request paths (e.g. ".../errornointernet/...") in access lines.
			return fmt.Sprintf("%s\n%s| filter message like /\\bERROR\\b|\\bCRITICAL\\b|Traceback|Exception|WORKER TIMEOUT|"+
				"Worker exiting|OOMKilled|[Oo]ut of memory|Booting worker/\n"+
				"| fields @timestamp, kubernetes.pod_name, message\n| sort @timestamp asc\n| limit %d",
				stream, matchFilter, limit), nil
		}
		return query, nil
	}
	return "", fmt.Errorf("unknown source %q (valid: %s, %s)", source, SourceNSGW, SourcePulp)
}

// ResolveWindow converts the tool's time arguments into an absolute window.
func ResolveWindow(now time.Time, startStr, endStr, rangeStr string) (time.Time, time.Time, error) {
	end := now.UTC()
	if endStr != "" {
		parsed, err := parseTime(endStr)
		if err != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid end_time: %w", err)
		}
		end = parsed
	}

	var start time.Time
	switch {
	case startStr != "":
		parsed, err := parseTime(startStr)
		if err != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid start_time: %w", err)
		}
		start = parsed
	case rangeStr != "":
		d, err := time.ParseDuration(rangeStr)
		if err != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid time_range: %w", err)
		}
		start = end.Add(-d)
	default:
		start = end.Add(-defaultTimeRange)
	}

	if !start.Before(end) {
		return time.Time{}, time.Time{}, fmt.Errorf("start (%s) must be before end (%s)",
			start.Format(time.RFC3339), end.Format(time.RFC3339))
	}
	if end.Sub(start) > maxWindow {
		return time.Time{}, time.Time{}, fmt.Errorf("window %s exceeds the maximum of %s; narrow it around the failing request",
			end.Sub(start), maxWindow)
	}
	return start, end, nil
}

func parseTime(s string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("%q is not RFC3339 (e.g. 2026-01-02T15:04:05Z)", s)
}

// Run executes a Logs Insights query and waits for the results.
func (c *Client) Run(ctx context.Context, groups []string, query string, start, end time.Time, limit int32) (string, error) {
	startEpoch, endEpoch := start.Unix(), end.Unix()
	out, err := c.logsAPI.StartQuery(ctx, &cloudwatchlogs.StartQueryInput{
		LogGroupNames: groups,
		QueryString:   &query,
		StartTime:     &startEpoch,
		EndTime:       &endEpoch,
		Limit:         &limit,
	})
	if err != nil {
		return "", fmt.Errorf("start query: %w", err)
	}

	deadline := time.Now().Add(queryTimeout)
	for time.Now().Before(deadline) {
		res, err := c.logsAPI.GetQueryResults(ctx, &cloudwatchlogs.GetQueryResultsInput{QueryId: out.QueryId})
		if err != nil {
			return "", fmt.Errorf("get query results: %w", err)
		}
		switch res.Status {
		case cwTypes.QueryStatusComplete:
			return FormatResults(res.Results), nil
		case cwTypes.QueryStatusFailed, cwTypes.QueryStatusCancelled, cwTypes.QueryStatusTimeout:
			return "", fmt.Errorf("query %s", res.Status)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(pollInterval):
		}
	}
	return "", fmt.Errorf("query timed out after %s", queryTimeout)
}

// FormatResults renders result rows as one line per row ("field=value | ...").
func FormatResults(rows [][]cwTypes.ResultField) string {
	if len(rows) == 0 {
		return "No log entries found."
	}
	var sb strings.Builder
	fmt.Fprintf(&sb, "rows: %d\n", len(rows))
	for _, row := range rows {
		var parts []string
		for _, f := range row {
			if f.Field == nil || f.Value == nil || *f.Field == "@ptr" {
				continue
			}
			parts = append(parts, *f.Field+"="+strings.TrimSpace(*f.Value))
		}
		sb.WriteString(strings.Join(parts, " | "))
		sb.WriteString("\n")
	}
	return sb.String()
}

type toolArgs struct {
	Source      string `json:"source"`
	Environment string `json:"environment"`
	Query       string `json:"query"`
	Match       string `json:"match"`
	StartTime   string `json:"start_time"`
	EndTime     string `json:"end_time"`
	TimeRange   string `json:"time_range"`
	Limit       int    `json:"limit"`
}

// Handle executes one tool call.
func (c *Client) Handle(ctx context.Context, argsJSON string) (string, error) {
	var args toolArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return "", fmt.Errorf("parse cloudwatch_trace_logs arguments: %w", err)
	}
	if args.Environment == "" {
		args.Environment = "prod"
	}
	envs, ok := LogGroups[args.Source]
	if !ok {
		return "", fmt.Errorf("unknown source %q (valid: %s, %s)", args.Source, SourceNSGW, SourcePulp)
	}
	groups, ok := envs[args.Environment]
	if !ok {
		return "", fmt.Errorf("unknown environment %q (valid: prod, stage)", args.Environment)
	}
	if strings.TrimSpace(args.Query) == "" {
		return "", fmt.Errorf("query is required")
	}

	limit := args.Limit
	if limit <= 0 {
		limit = defaultLimit
	}
	limit = min(limit, maxLimit)

	start, end, err := ResolveWindow(c.now(), args.StartTime, args.EndTime, args.TimeRange)
	if err != nil {
		return "", err
	}
	query, err := ExpandQuery(args.Source, args.Environment, args.Query, args.Match, limit)
	if err != nil {
		return "", err
	}

	fmt.Fprintf(os.Stderr, "[cloudwatch] %s/%s %s..%s groups=%s\n[cloudwatch] query: %s\n",
		args.Source, args.Environment, start.Format(time.RFC3339), end.Format(time.RFC3339),
		strings.Join(groups, ","), query)

	results, err := c.Run(ctx, groups, query, start, end, int32(limit))
	if err != nil {
		return "", err
	}
	header := fmt.Sprintf("source=%s environment=%s log_groups=%s window=%s..%s\n",
		args.Source, args.Environment, strings.Join(groups, ","), start.Format(time.RFC3339), end.Format(time.RFC3339))
	return utils.Truncate(header+results, maxOutputBytes), nil
}

// RegisterTool registers the cloudwatch_trace_logs tool on the MCPManager.
func (c *Client) RegisterTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name: "cloudwatch_trace_logs",
			Description: "Query CloudWatch Logs Insights to trace a request after it leaves Akamai. " +
				"source=nsgw reads the north-south gateway Envoy access logs (openshift-ingress, cluster-default-gateway); " +
				"source=pulp reads the Pulp application logs (pulp-api, pulp-content, pulp-worker pods). " +
				"Shortcuts for nsgw: raw, errors (5xx breakdown by status/flags/details), errors-list, trace (requires match). " +
				"Shortcuts for pulp: trace (requires match), 5xx (5xx access lines), errors (ERROR/Traceback/WORKER TIMEOUT/OOM lines). " +
				"'match' is a literal substring filter (e.g. a request path without query string, a request id, or a correlation id) " +
				"that is applied to every shortcut. Any other query value is run as a raw Logs Insights query " +
				"(use 'message', not '@message', since records are JSON-wrapped). " +
				"Always pass an absolute start_time/end_time window of a few minutes around the Akamai timestamp. " +
				"Slow (5-30s per call).",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"source": map[string]any{
						"type": "string", "enum": []string{SourceNSGW, SourcePulp},
						"description": "nsgw = gateway Envoy access logs; pulp = Pulp application logs",
					},
					"environment": map[string]any{
						"type": "string", "enum": []string{"prod", "stage"},
						"description": "Environment. Default: prod (packages.redhat.com)",
					},
					"query": map[string]any{
						"type":        "string",
						"description": "Shortcut name (see tool description) or a raw Logs Insights query",
					},
					"match": map[string]any{
						"type":        "string",
						"description": "Literal substring to filter log lines by (path, request id, correlation id, client IP)",
					},
					"start_time": map[string]any{
						"type":        "string",
						"description": "Window start, RFC3339 UTC (e.g. 2026-01-02T15:04:05Z)",
					},
					"end_time": map[string]any{
						"type":        "string",
						"description": "Window end, RFC3339 UTC. Default: now",
					},
					"time_range": map[string]any{
						"type":        "string",
						"description": "Relative window ending at end_time, used when start_time is empty (e.g. 15m, 1h). Default: 15m. Max window: 24h",
					},
					"limit": map[string]any{
						"type":        "integer",
						"description": fmt.Sprintf("Max rows (default %d, max %d)", defaultLimit, maxLimit),
					},
				},
				"required": []string{"source", "query"},
			},
		},
	}
	mgr.RegisterNativeTool(tool, c.Handle)
	return tool
}
