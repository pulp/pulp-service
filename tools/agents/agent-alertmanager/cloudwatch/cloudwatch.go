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
	cloudwatchTypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"

	"github.com/tmc/langchaingo/llms"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/types"
)

type LogsAPI interface {
	StartQuery(ctx context.Context, params *cloudwatchlogs.StartQueryInput, optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.StartQueryOutput, error)
	GetQueryResults(ctx context.Context, params *cloudwatchlogs.GetQueryResultsInput, optFns ...func(*cloudwatchlogs.Options)) (*cloudwatchlogs.GetQueryResultsOutput, error)
}

type Client struct {
	logsAPI   LogsAPI
	logGroups map[string][]string
}

func NewClientFromEnv(ctx context.Context) (*Client, error) {
	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion("us-east-1"))
	if err != nil {
		return nil, fmt.Errorf("load AWS config: %w", err)
	}

	return &Client{
		logsAPI: cloudwatchlogs.NewFromConfig(cfg),
		logGroups: map[string][]string{
			"prod":  {"crcp01ue1.pulp-prod", "pulpp01ue1.pulp-prod"},
			"stage": {"crcs02ue1.pulp-stage", "pulps01ue1.pulp-stage"},
		},
	}, nil
}

func ExpandShortcut(query, environment string) string {
	shortcuts := map[string]string{
		"errors": fmt.Sprintf(
			"filter @logStream like /pulp-%s_pulp-(api|content|worker)/\n"+
				"| filter @message like /ERROR|Exception|Traceback/\n"+
				"| fields @timestamp, @message\n"+
				"| sort @timestamp desc", environment),
		"5xx": fmt.Sprintf(
			"filter @logStream like /pulp-%s_pulp-api/\n"+
				"| filter @message like /\" 5[0-9][0-9] /\n"+
				"| fields @timestamp, @message\n"+
				"| sort @timestamp desc", environment),
		"worker-errors": fmt.Sprintf(
			"filter @logStream like /pulp-%s_pulp-worker/\n"+
				"| filter @message like /ERROR|Exception|Traceback/\n"+
				"| fields @timestamp, @message\n"+
				"| sort @timestamp desc", environment),
		"status-codes": fmt.Sprintf(
			"filter @logStream like /pulp-%s_pulp-api/\n"+
				"| parse @message /\" (?<status>\\d{3}) /\n"+
				"| stats count(*) by status\n"+
				"| sort status", environment),
		"latency": fmt.Sprintf(
			"filter @logStream like /pulp-%s_pulp-api/\n"+
				"| parse @message /(?<latency_ms>\\d+)$/\n"+
				"| stats avg(latency_ms), pct(latency_ms, 95), pct(latency_ms, 99), max(latency_ms) by bin(5m)", environment),
	}
	if expanded, found := shortcuts[query]; found {
		return expanded
	}
	return query
}

func (client *Client) runQuery(ctx context.Context, groups []string, query string, start, end time.Time, limit int32) (string, error) {
	startEpoch := start.Unix()
	endEpoch := end.Unix()

	startOutput, err := client.logsAPI.StartQuery(ctx, &cloudwatchlogs.StartQueryInput{
		LogGroupNames: groups,
		QueryString:   &query,
		StartTime:     &startEpoch,
		EndTime:       &endEpoch,
		Limit:         &limit,
	})
	if err != nil {
		return "", fmt.Errorf("start query: %w", err)
	}

	queryID := startOutput.QueryId
	pollInterval := 2 * time.Second
	timeout := 60 * time.Second
	deadline := time.Now().Add(timeout)

	for time.Now().Before(deadline) {
		resultsOutput, err := client.logsAPI.GetQueryResults(ctx, &cloudwatchlogs.GetQueryResultsInput{
			QueryId: queryID,
		})
		if err != nil {
			return "", fmt.Errorf("get query results: %w", err)
		}

		if resultsOutput.Status == cloudwatchTypes.QueryStatusComplete ||
			resultsOutput.Status == cloudwatchTypes.QueryStatusFailed ||
			resultsOutput.Status == cloudwatchTypes.QueryStatusCancelled {

			if resultsOutput.Status != cloudwatchTypes.QueryStatusComplete {
				return "", fmt.Errorf("query %s", resultsOutput.Status)
			}

			return formatResults(resultsOutput.Results), nil
		}

		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(pollInterval):
		}
	}

	return "", fmt.Errorf("query timed out after %v", timeout)
}

func formatResults(results [][]cloudwatchTypes.ResultField) string {
	if len(results) == 0 {
		return "No log entries found."
	}

	var summary strings.Builder
	summary.WriteString(fmt.Sprintf("Log entries: %d\n\n", len(results)))

	for idx, row := range results {
		if idx >= 20 {
			summary.WriteString(fmt.Sprintf("... and %d more entries\n", len(results)-20))
			break
		}
		for _, field := range row {
			if field.Field != nil && field.Value != nil {
				if *field.Field == "@ptr" {
					continue
				}
				summary.WriteString(fmt.Sprintf("%s: %s\n", *field.Field, *field.Value))
			}
		}
		summary.WriteString("\n")
	}
	return summary.String()
}

// TODO: RegisterTool takes MCPManager for consistency with other tool packages.
// See kubernetes/kubernetes.go for the same pattern and refactoring note.
func RegisterTool(client *Client, manager *mcpClient.MCPManager) llms.Tool {
	envNames := make([]string, 0, len(client.logGroups))
	for envName := range client.logGroups {
		envNames = append(envNames, envName)
	}

	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "cloudwatch_query_logs",
			Description: "Query CloudWatch Log Insights for application logs. Slow (15-30s). Use as last resort when other tools don't provide enough context. Shortcuts: errors, 5xx, worker-errors, status-codes, latency.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"environment": map[string]any{"type": "string", "description": fmt.Sprintf("Environment: %s", strings.Join(envNames, ", "))},
					"query":       map[string]any{"type": "string", "description": "Shortcut name or raw Log Insights query"},
					"time_range":  map[string]any{"type": "string", "description": "Time range (e.g. '5m', '15m', '1h'). Default: 5m."},
					"limit":       map[string]any{"type": "integer", "description": "Max results (default: 50)"},
					"detail":      map[string]any{"type": "boolean", "description": "Return full log entries (default: false)"},
				},
				"required": []string{"environment", "query"},
			},
		},
	}

	handler := func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Environment string `json:"environment"`
			Query       string `json:"query"`
			TimeRange   string `json:"time_range,omitempty"`
			Limit       int32  `json:"limit,omitempty"`
			Detail      bool   `json:"detail,omitempty"`
		}
		if argsJSON != "" {
			json.Unmarshal([]byte(argsJSON), &args)
		}

		groups, found := client.logGroups[args.Environment]
		if !found {
			return "", fmt.Errorf("unknown environment %q, valid: %s", args.Environment, strings.Join(envNames, ", "))
		}

		query := ExpandShortcut(args.Query, args.Environment)
		limit := args.Limit
		if limit == 0 {
			limit = 50
		}

		timeRange := 5 * time.Minute
		if args.TimeRange != "" {
			if parsed, parseErr := time.ParseDuration(args.TimeRange); parseErr == nil {
				timeRange = parsed
			}
		}

		end := time.Now().UTC()
		start := end.Add(-timeRange)

		resolvedParams := map[string]string{
			"environment": args.Environment,
			"query":       query,
			"original":    args.Query,
			"time_range":  timeRange.String(),
			"log_groups":  strings.Join(groups, ", "),
		}

		fmt.Fprintf(os.Stderr, "[cloudwatch] querying %s (%s)\n", args.Environment, timeRange)
		queryStart := time.Now()

		resultText, queryErr := client.runQuery(ctx, groups, query, start, end, limit)

		elapsed := time.Since(queryStart)
		if elapsed > 15*time.Second {
			fmt.Fprintf(os.Stderr, "[cloudwatch] query took %v (>15s)\n", elapsed)
		}

		if queryErr != nil {
			return "", queryErr
		}

		maxSize := 10 * 1024
		if args.Detail {
			maxSize = 30 * 1024
		}
		truncatedText, wasTruncated := types.Truncate(resultText, maxSize)

		response := types.ToolResponse{
			ResolvedParams: resolvedParams,
			Results:        truncatedText,
			Truncated:      wasTruncated,
		}
		return response.ToJSON(), nil
	}

	manager.RegisterNativeTool(tool, handler)
	fmt.Fprintf(os.Stderr, "[cloudwatch] registered cloudwatch_query_logs tool\n")
	return tool
}
