package prometheus

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/types"
)

type Client struct {
	name       string
	baseURL    string
	token      string
	namespace  string
	HTTPClient *http.Client
}

func NewClient(name, baseURL, token, namespace string) *Client {
	return &Client{
		name:      name,
		baseURL:   baseURL,
		token:     token,
		namespace: namespace,
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
			},
		},
	}
}

func (client *Client) Name() string { return client.name }

func ExpandShortcut(query, namespace string) string {
	shortcuts := map[string]string{
		"health":       fmt.Sprintf(`up{namespace="%s"}`, namespace),
		"errors":       "pulp_api_errors_bucket:rate1h",
		"latency":      "histogram_quantile(0.95, rate(pulp_api_request_duration_milliseconds_bucket[5m]))",
		"connections":  "pulp_api_active_connections",
		"content":      "histogram_quantile(0.95, rate(pulp_content_request_duration_milliseconds_bucket[5m]))",
		"tasks":        "pulp_waiting_tasks",
		"request-rate": "rate(pulp_api_request_duration_milliseconds_count[5m])",
	}
	if expanded, found := shortcuts[query]; found {
		return expanded
	}
	return query
}

func summarizeResults(data map[string]any) string {
	dataSection, _ := data["data"].(map[string]any)
	if dataSection == nil {
		return "No data returned."
	}
	resultType, _ := dataSection["resultType"].(string)
	results, _ := dataSection["result"].([]any)
	if len(results) == 0 {
		return "No results."
	}

	var summary strings.Builder
	summary.WriteString(fmt.Sprintf("Type: %s, Series: %d\n", resultType, len(results)))

	for idx, series := range results {
		if idx >= 10 {
			summary.WriteString(fmt.Sprintf("... and %d more series\n", len(results)-10))
			break
		}
		seriesMap, ok := series.(map[string]any)
		if !ok {
			continue
		}
		metric, _ := seriesMap["metric"].(map[string]any)
		metricName, _ := metric["__name__"].(string)
		if metricName == "" {
			metricName = "value"
		}

		if resultType == "vector" {
			value, _ := seriesMap["value"].([]any)
			if len(value) >= 2 {
				summary.WriteString(fmt.Sprintf("%s = %v\n", metricName, value[1]))
			}
		} else if resultType == "matrix" {
			values, _ := seriesMap["values"].([]any)
			summary.WriteString(fmt.Sprintf("%s: %d datapoints\n", metricName, len(values)))
			if len(values) > 0 {
				first, _ := values[0].([]any)
				last, _ := values[len(values)-1].([]any)
				if len(first) >= 2 && len(last) >= 2 {
					summary.WriteString(fmt.Sprintf("  first=%v last=%v\n", first[1], last[1]))
				}
			}
		}
	}
	return summary.String()
}

func (client *Client) doQuery(ctx context.Context, endpoint string) (map[string]any, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	request.Header.Set("Authorization", "Bearer "+client.token)

	response, err := client.HTTPClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("prometheus query: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 2*1024*1024))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("prometheus returned %d: %s", response.StatusCode, string(body[:min(len(body), 200)]))
	}

	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}
	if status, _ := result["status"].(string); status != "success" {
		errMsg, _ := result["error"].(string)
		return nil, fmt.Errorf("prometheus error: %s", errMsg)
	}
	return result, nil
}

func (client *Client) queryInstant(ctx context.Context, promql string) (map[string]any, error) {
	endpoint := client.baseURL + "/api/v1/query?query=" + url.QueryEscape(promql)
	return client.doQuery(ctx, endpoint)
}

func (client *Client) queryRange(ctx context.Context, promql string, start, end time.Time, step string) (map[string]any, error) {
	params := url.Values{
		"query": {promql},
		"start": {start.UTC().Format(time.RFC3339)},
		"end":   {end.UTC().Format(time.RFC3339)},
		"step":  {step},
	}
	endpoint := client.baseURL + "/api/v1/query_range?" + params.Encode()
	return client.doQuery(ctx, endpoint)
}

// TODO: RegisterTool takes MCPManager for consistency with other tool packages.
// See kubernetes/kubernetes.go for the same pattern and refactoring note.
func RegisterTool(clients []*Client, manager *mcpClient.MCPManager) llms.Tool {
	clusterNames := make([]string, len(clients))
	clientMap := make(map[string]*Client, len(clients))
	for idx, client := range clients {
		clusterNames[idx] = client.Name()
		clientMap[client.Name()] = client
	}

	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "prometheus_query",
			Description: "Query Prometheus metrics. Fast (< 2s). Use shortcuts: health, errors, latency, connections, content, tasks, request-rate. Or pass raw PromQL.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"cluster":    map[string]any{"type": "string", "description": fmt.Sprintf("Cluster name. Valid: %s", strings.Join(clusterNames, ", "))},
					"query":      map[string]any{"type": "string", "description": "Shortcut name or raw PromQL query"},
					"mode":       map[string]any{"type": "string", "description": "Query mode: 'instant' or 'range' (default: 'range')"},
					"time_range": map[string]any{"type": "string", "description": "Time range for range queries (e.g. '5m', '15m', '1h'). Default: 5m."},
					"detail":     map[string]any{"type": "boolean", "description": "Return full metric series instead of summary (default: false)"},
				},
				"required": []string{"cluster", "query"},
			},
		},
	}

	handler := func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Cluster   string `json:"cluster"`
			Query     string `json:"query"`
			Mode      string `json:"mode,omitempty"`
			TimeRange string `json:"time_range,omitempty"`
			Detail    bool   `json:"detail,omitempty"`
		}
		if argsJSON != "" {
			json.Unmarshal([]byte(argsJSON), &args)
		}

		client, found := clientMap[args.Cluster]
		if !found {
			return "", fmt.Errorf("unknown cluster %q, valid: %s", args.Cluster, strings.Join(clusterNames, ", "))
		}

		promql := ExpandShortcut(args.Query, client.namespace)

		resolvedParams := map[string]string{
			"cluster":  args.Cluster,
			"query":    promql,
			"original": args.Query,
		}

		mode := args.Mode
		if mode == "" {
			mode = "range"
		}

		var result map[string]any
		var queryErr error

		if mode == "instant" {
			result, queryErr = client.queryInstant(ctx, promql)
			resolvedParams["mode"] = "instant"
		} else {
			timeRange := 5 * time.Minute
			if args.TimeRange != "" {
				if parsed, parseErr := time.ParseDuration(args.TimeRange); parseErr == nil {
					timeRange = parsed
				}
			}
			end := time.Now().UTC()
			start := end.Add(-timeRange)
			result, queryErr = client.queryRange(ctx, promql, start, end, "60s")
			resolvedParams["mode"] = "range"
			resolvedParams["time_range"] = timeRange.String()
		}

		if queryErr != nil {
			return "", queryErr
		}

		summary := summarizeResults(result)
		maxSize := 10 * 1024
		if args.Detail {
			maxSize = 30 * 1024
		}
		truncatedText, wasTruncated := types.Truncate(summary, maxSize)

		response := types.ToolResponse{
			ResolvedParams: resolvedParams,
			Results:        truncatedText,
			Truncated:      wasTruncated,
		}
		return response.ToJSON(), nil
	}

	manager.RegisterNativeTool(tool, handler)
	fmt.Fprintf(os.Stderr, "[prometheus] registered prometheus_query tool for %d clusters\n", len(clients))
	return tool
}
