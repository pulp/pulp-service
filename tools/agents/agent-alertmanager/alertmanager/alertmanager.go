package alertmanager

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
)

// DefaultFilter is the AlertManager API v2 filter for pulp service alerts.
// Uses PromQL-style matcher syntax with curly braces.
// NOTE: This {key="value"} syntax works with most AlertManager versions.
// Some versions require repeated filter[] query params instead. The format
// should be tested against the target AlertManager instance.
const DefaultFilter = `{service="pulp"}`

// Alert represents a single alert from the AlertManager API v2.
type Alert struct {
	Labels       map[string]string `json:"labels"`
	Annotations  map[string]string `json:"annotations"`
	StartsAt     string            `json:"startsAt"`
	EndsAt       string            `json:"endsAt"`
	GeneratorURL string            `json:"generatorURL"`
	Fingerprint  string            `json:"fingerprint"`
	Cluster      string            `json:"cluster"`
	Status       struct {
		State string `json:"state"`
	} `json:"status"`
}

// Client is an HTTP client for the AlertManager API v2.
type Client struct {
	name       string
	BaseURL    string
	token      string
	HTTPClient *http.Client
}

func (client *Client) Name() string {
	return client.name
}

// NewClient creates an Alertmanager client with a 30-second timeout.
// TLS configuration is caller-provided; NewClient does not read env vars.
func NewClient(name, baseURL, token string, insecureSkipVerify bool) *Client {
	transport := &http.Transport{}

	if insecureSkipVerify {
		transport.TLSClientConfig = &tls.Config{
			InsecureSkipVerify: true, //nolint:gosec // opt-in per cluster config
		}
		fmt.Fprintf(os.Stderr, "[alertmanager-debug] TLS certificate verification disabled for cluster %s\n", name)
	}

	return &Client{
		name:    name,
		BaseURL: baseURL,
		token:   token,
		HTTPClient: &http.Client{
			Timeout:   30 * time.Second,
			Transport: transport,
		},
	}
}

func (client *Client) stampCluster(alerts []Alert) {
	for idx := range alerts {
		alerts[idx].Cluster = client.name
	}
}

// CheckAlerts performs a lightweight check for active alerts matching the
// default filter. It returns whether alerts were found, the count after
// latency filtering, and any error. Used by --check-only mode.
func (client *Client) CheckAlerts(ctx context.Context) (bool, int, error) {
	alerts, fetchErr := client.fetchAlerts(ctx, DefaultFilter, true, false, false)
	if fetchErr != nil {
		return false, 0, fmt.Errorf("check alerts: %w", fetchErr)
	}

	filtered := FilterLatencyAlerts(alerts)
	client.stampCluster(filtered)
	found := len(filtered) > 0
	return found, len(filtered), nil
}

// FetchAlerts retrieves active alerts matching the default filter, applies
// latency filtering, and returns the result. This is used for pre-flight
// dedup checks before invoking the LLM pipeline.
func (client *Client) FetchAlerts(ctx context.Context) ([]Alert, error) {
	alerts, fetchErr := client.fetchAlerts(ctx, DefaultFilter, true, false, false)
	if fetchErr != nil {
		return nil, fmt.Errorf("fetch alerts: %w", fetchErr)
	}
	filtered := FilterLatencyAlerts(alerts)
	client.stampCluster(filtered)
	return filtered, nil
}

// fetchAlerts retrieves alerts from the AlertManager API v2.
// The filter parameter uses PromQL-style matcher syntax (e.g. {service="pulp"}).
func (client *Client) fetchAlerts(ctx context.Context, filter string, active, silenced, inhibited bool) ([]Alert, error) {
	endpoint, parseErr := url.Parse(client.BaseURL + "/api/v2/alerts")
	if parseErr != nil {
		return nil, fmt.Errorf("parse URL: %w", parseErr)
	}

	queryParams := endpoint.Query()
	queryParams.Set("filter", filter)
	queryParams.Set("active", fmt.Sprintf("%t", active))
	queryParams.Set("silenced", fmt.Sprintf("%t", silenced))
	queryParams.Set("inhibited", fmt.Sprintf("%t", inhibited))
	endpoint.RawQuery = queryParams.Encode()

	fmt.Fprintf(os.Stderr, "[alertmanager-debug] GET %s\n", endpoint.String())

	request, reqErr := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if reqErr != nil {
		return nil, fmt.Errorf("create request: %w", reqErr)
	}
	request.Header.Set("Authorization", "Bearer "+client.token)

	response, doErr := client.HTTPClient.Do(request)
	if doErr != nil {
		return nil, fmt.Errorf("fetch alerts: %w", doErr)
	}
	defer response.Body.Close()

	// Limit response body to 5MB to prevent unbounded memory usage.
	bodyBytes, readErr := io.ReadAll(io.LimitReader(response.Body, 5*1024*1024))
	if readErr != nil {
		return nil, fmt.Errorf("read response body: %w", readErr)
	}

	if response.StatusCode != http.StatusOK {
		bodyStr := string(bodyBytes)
		bodyStr = strings.ReplaceAll(bodyStr, client.token, "[REDACTED]")
		if len(bodyStr) > 500 {
			bodyStr = bodyStr[:500] + "...(truncated)"
		}
		return nil, fmt.Errorf("alertmanager returned status %d: %s", response.StatusCode, bodyStr)
	}

	var alerts []Alert
	if unmarshalErr := json.Unmarshal(bodyBytes, &alerts); unmarshalErr != nil {
		return nil, fmt.Errorf("decode alerts: %w", unmarshalErr)
	}

	fmt.Fprintf(os.Stderr, "[alertmanager-debug] fetched %d raw alerts\n", len(alerts))
	return alerts, nil
}

// RegisterTool registers alertmanager_get_alerts as a native tool on the
// given MCP manager. The tool fetches alerts from all provided clients,
// filters out latency alerts, groups burn-rate alerts, and returns the
// result as JSON grouped by cluster.
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
			Name:        "alertmanager_get_alerts",
			Description: "Fetch active alerts from Alertmanager clusters, filtered and grouped for triage.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"cluster": map[string]any{
						"type":        "string",
						"description": fmt.Sprintf("Query a specific cluster only. Valid names: %s", strings.Join(clusterNames, ", ")),
					},
					"filter": map[string]any{
						"type":        "string",
						"description": "PromQL-style filter (default: {service=\"pulp\"})",
					},
					"active": map[string]any{
						"type":        "boolean",
						"description": "Include active alerts (default: true)",
					},
					"silenced": map[string]any{
						"type":        "boolean",
						"description": "Include silenced alerts (default: false)",
					},
					"inhibited": map[string]any{
						"type":        "boolean",
						"description": "Include inhibited alerts (default: false)",
					},
				},
			},
		},
	}

	handler := func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Cluster   string `json:"cluster,omitempty"`
			Filter    string `json:"filter,omitempty"`
			Active    *bool  `json:"active,omitempty"`
			Silenced  *bool  `json:"silenced,omitempty"`
			Inhibited *bool  `json:"inhibited,omitempty"`
		}
		if argsJSON != "" {
			if parseErr := json.Unmarshal([]byte(argsJSON), &args); parseErr != nil {
				return "", fmt.Errorf("parse tool args: %w", parseErr)
			}
		}

		targetClients := clients
		if args.Cluster != "" {
			target, found := clientMap[args.Cluster]
			if !found {
				return "", fmt.Errorf("unknown cluster %q, valid clusters: %s", args.Cluster, strings.Join(clusterNames, ", "))
			}
			targetClients = []*Client{target}
		}

		activeFlag := true
		if args.Active != nil {
			activeFlag = *args.Active
		}
		silencedFlag := false
		if args.Silenced != nil {
			silencedFlag = *args.Silenced
		}
		inhibitedFlag := false
		if args.Inhibited != nil {
			inhibitedFlag = *args.Inhibited
		}
		filter := DefaultFilter
		if args.Filter != "" {
			filter = args.Filter
		}

		type clusterResult struct {
			Cluster string       `json:"cluster"`
			Groups  []AlertGroup `json:"groups"`
			Error   string       `json:"error,omitempty"`
		}

		var results []clusterResult
		for _, client := range targetClients {
			fetchCtx, fetchCancel := context.WithTimeout(ctx, 2*time.Minute)
			alerts, fetchErr := client.fetchAlerts(fetchCtx, filter, activeFlag, silencedFlag, inhibitedFlag)
			fetchCancel()

			if fetchErr != nil {
				results = append(results, clusterResult{
					Cluster: client.Name(),
					Error:   fetchErr.Error(),
				})
				continue
			}

			filtered := FilterLatencyAlerts(alerts)
			client.stampCluster(filtered)
			groups := GroupAlerts(filtered)
			results = append(results, clusterResult{
				Cluster: client.Name(),
				Groups:  groups,
			})
		}

		resultBytes, marshalErr := json.Marshal(results)
		if marshalErr != nil {
			return "", fmt.Errorf("marshal result: %w", marshalErr)
		}

		resultStr := string(resultBytes)
		const maxResponseChars = 25000
		if len(resultStr) > maxResponseChars {
			resultStr = fmt.Sprintf(
				"NOTE: Response truncated. Showing partial results out of %d cluster results total.\n\n%s",
				len(results), resultStr[:maxResponseChars],
			)
		}
		return resultStr, nil
	}

	manager.RegisterNativeTool(tool, handler)
	return tool
}
