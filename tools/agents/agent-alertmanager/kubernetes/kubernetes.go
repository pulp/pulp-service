package kubernetes

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
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

func summarizePod(pod map[string]any) string {
	var summary strings.Builder
	status, _ := pod["status"].(map[string]any)
	if status == nil {
		return "pod status unavailable"
	}

	phase, _ := status["phase"].(string)
	summary.WriteString(fmt.Sprintf("Phase: %s\n", phase))

	containers, _ := status["containerStatuses"].([]any)
	for _, container := range containers {
		containerMap, ok := container.(map[string]any)
		if !ok {
			continue
		}
		containerName, _ := containerMap["name"].(string)
		restartCount, _ := containerMap["restartCount"].(float64)
		ready, _ := containerMap["ready"].(bool)
		summary.WriteString(fmt.Sprintf("Container %s: ready=%v restarts=%d\n", containerName, ready, int(restartCount)))

		if lastState, ok := containerMap["lastState"].(map[string]any); ok {
			if terminated, ok := lastState["terminated"].(map[string]any); ok {
				reason, _ := terminated["reason"].(string)
				exitCode, _ := terminated["exitCode"].(float64)
				summary.WriteString(fmt.Sprintf("  Last terminated: reason=%s exitCode=%d\n", reason, int(exitCode)))
			}
		}
	}
	return summary.String()
}

func summarizeEvents(events []map[string]any) string {
	var summary strings.Builder
	warningCount := 0
	for _, event := range events {
		eventType, _ := event["type"].(string)
		if eventType != "Warning" {
			continue
		}
		reason, _ := event["reason"].(string)
		message, _ := event["message"].(string)
		count, _ := event["count"].(float64)
		timestamp, _ := event["lastTimestamp"].(string)
		summary.WriteString(fmt.Sprintf("[%s] %s: %s (count: %d)\n", timestamp, reason, message, int(count)))
		warningCount++
	}
	if warningCount == 0 {
		return "No warning events found."
	}
	return summary.String()
}

func (client *Client) apiGet(ctx context.Context, path string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, client.baseURL+path, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	request.Header.Set("Authorization", "Bearer "+client.token)

	response, err := client.HTTPClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("k8s api: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 1*1024*1024))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("k8s api returned %d: %s", response.StatusCode, string(body[:min(len(body), 200)]))
	}
	return body, nil
}

func (client *Client) fetchPodInfo(ctx context.Context, namespace, podName string) (map[string]any, error) {
	path := fmt.Sprintf("/api/v1/namespaces/%s/pods/%s", namespace, podName)
	body, err := client.apiGet(ctx, path)
	if err != nil {
		return nil, err
	}
	var pod map[string]any
	if err := json.Unmarshal(body, &pod); err != nil {
		return nil, fmt.Errorf("parse pod: %w", err)
	}
	return pod, nil
}

func (client *Client) fetchEvents(ctx context.Context, namespace, podName string) ([]map[string]any, error) {
	path := fmt.Sprintf("/api/v1/namespaces/%s/events?fieldSelector=involvedObject.name=%s", namespace, podName)
	body, err := client.apiGet(ctx, path)
	if err != nil {
		return nil, err
	}
	var eventList struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(body, &eventList); err != nil {
		return nil, fmt.Errorf("parse events: %w", err)
	}
	return eventList.Items, nil
}

func (client *Client) listPods(ctx context.Context, namespace string) ([]map[string]any, error) {
	path := fmt.Sprintf("/api/v1/namespaces/%s/pods", namespace)
	body, err := client.apiGet(ctx, path)
	if err != nil {
		return nil, err
	}
	var podList struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(body, &podList); err != nil {
		return nil, fmt.Errorf("parse pod list: %w", err)
	}
	return podList.Items, nil
}

func summarizePodList(pods []map[string]any) string {
	if len(pods) == 0 {
		return "No pods found in namespace."
	}
	var summary strings.Builder
	summary.WriteString(fmt.Sprintf("Pods: %d\n\n", len(pods)))
	for _, pod := range pods {
		metadata, _ := pod["metadata"].(map[string]any)
		podName, _ := metadata["name"].(string)
		summary.WriteString(fmt.Sprintf("--- %s ---\n", podName))
		summary.WriteString(summarizePod(pod))
		summary.WriteString("\n")
	}
	return summary.String()
}

// TODO: RegisterTool takes MCPManager for consistency with alertmanager/jira packages,
// but MCPManager is just a native tool registry here — no MCP protocol involved.
// Refactor into a simpler ToolRegistry interface when consolidating shared packages.
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
			Name:        "k8s_get_pod_info",
			Description: "Fetch pod status and Kubernetes events. Fast (< 1s). Call first for crash/OOM alerts. Without a pod name, lists all pods in the namespace with their status. With a pod name (from alert labels), returns detailed status + events.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"cluster":   map[string]any{"type": "string", "description": fmt.Sprintf("Cluster name. Valid: %s", strings.Join(clusterNames, ", "))},
					"namespace": map[string]any{"type": "string", "description": "Kubernetes namespace (default: from cluster config)"},
					"pod":       map[string]any{"type": "string", "description": "Pod name to inspect"},
					"detail":    map[string]any{"type": "boolean", "description": "Return full event list instead of summary (default: false)"},
				},
				"required": []string{"cluster"},
			},
		},
	}

	handler := func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Cluster   string `json:"cluster"`
			Namespace string `json:"namespace,omitempty"`
			Pod       string `json:"pod,omitempty"`
			Detail    bool   `json:"detail,omitempty"`
		}
		if argsJSON != "" {
			json.Unmarshal([]byte(argsJSON), &args)
		}

		client, found := clientMap[args.Cluster]
		if !found {
			return "", fmt.Errorf("unknown cluster %q, valid: %s", args.Cluster, strings.Join(clusterNames, ", "))
		}

		namespace := args.Namespace
		if namespace == "" {
			namespace = client.namespace
		}
		if namespace == "" {
			return "", fmt.Errorf("namespace required: specify in args or cluster config")
		}

		resolvedParams := map[string]string{
			"cluster":   args.Cluster,
			"namespace": namespace,
		}

		var resultParts []string

		if args.Pod != "" {
			resolvedParams["pod"] = args.Pod

			fetchCtx, fetchCancel := context.WithTimeout(ctx, 10*time.Second)
			pod, podErr := client.fetchPodInfo(fetchCtx, namespace, args.Pod)
			fetchCancel()
			if podErr != nil {
				resultParts = append(resultParts, fmt.Sprintf("Pod status error: %v", podErr))
			} else {
				resultParts = append(resultParts, "=== Pod Status ===\n"+summarizePod(pod))
			}

			fetchCtx2, fetchCancel2 := context.WithTimeout(ctx, 10*time.Second)
			events, eventsErr := client.fetchEvents(fetchCtx2, namespace, args.Pod)
			fetchCancel2()
			if eventsErr != nil {
				resultParts = append(resultParts, fmt.Sprintf("Events error: %v", eventsErr))
			} else {
				resultParts = append(resultParts, "=== Events ===\n"+summarizeEvents(events))
			}
		} else {
			fetchCtx, fetchCancel := context.WithTimeout(ctx, 10*time.Second)
			pods, listErr := client.listPods(fetchCtx, namespace)
			fetchCancel()
			if listErr != nil {
				resultParts = append(resultParts, fmt.Sprintf("Pod listing error: %v", listErr))
			} else {
				resultParts = append(resultParts, "=== Pod Listing ===\n"+summarizePodList(pods))
			}
		}

		combined := strings.Join(resultParts, "\n\n")
		maxSize := 10 * 1024
		if args.Detail {
			maxSize = 30 * 1024
		}
		truncatedText, wasTruncated := types.Truncate(combined, maxSize)

		response := types.ToolResponse{
			ResolvedParams: resolvedParams,
			Results:        truncatedText,
			Truncated:      wasTruncated,
		}
		return response.ToJSON(), nil
	}

	manager.RegisterNativeTool(tool, handler)
	fmt.Fprintf(os.Stderr, "[k8s] registered k8s_get_pod_info tool for %d clusters\n", len(clients))
	return tool
}
