package glitchtip

import (
	"context"
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

type ProjectConfig struct {
	ID   string `json:"id"`
	Slug string `json:"slug"`
}

type Client struct {
	baseURL    string
	token      string
	orgSlug    string
	projects   map[string]ProjectConfig
	httpClient *http.Client
}

func NewClientFromEnv() (*Client, error) {
	token := os.Getenv("GLITCHTIP_TOKEN")
	if token == "" {
		return nil, fmt.Errorf("GLITCHTIP_TOKEN required")
	}

	return &Client{
		baseURL: "https://glitchtip.devshift.net",
		token:   token,
		orgSlug: "insights",
		projects: map[string]ProjectConfig{
			"stage": {ID: "84", Slug: "pulp-stage"},
			"prod":  {ID: "85", Slug: "pulp-prod"},
		},
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func (client *Client) apiGet(ctx context.Context, path string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, client.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+client.token)
	request.Header.Set("Content-Type", "application/json")

	response, err := client.httpClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("glitchtip api: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 1*1024*1024))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("glitchtip returned %d: %s", response.StatusCode, string(body[:min(len(body), 200)]))
	}
	return body, nil
}

func (client *Client) fetchIssues(ctx context.Context, project ProjectConfig) (string, error) {
	path := fmt.Sprintf("/api/0/projects/%s/%s/issues/", client.orgSlug, project.Slug)
	body, err := client.apiGet(ctx, path)
	if err != nil {
		return "", err
	}

	var issues []map[string]any
	if err := json.Unmarshal(body, &issues); err != nil {
		return "", fmt.Errorf("parse issues: %w", err)
	}

	var summary strings.Builder
	summary.WriteString(fmt.Sprintf("Unresolved issues: %d\n\n", len(issues)))
	for idx, issue := range issues {
		if idx >= 10 {
			summary.WriteString(fmt.Sprintf("... and %d more\n", len(issues)-10))
			break
		}
		title, _ := issue["title"].(string)
		count, _ := issue["count"].(float64)
		lastSeen, _ := issue["lastSeen"].(string)
		culprit, _ := issue["culprit"].(string)
		issueID, _ := issue["id"].(string)
		summary.WriteString(fmt.Sprintf("#%s %s\n  Count: %d | Last: %s | File: %s\n\n",
			issueID, title, int(count), lastSeen, culprit))
	}
	return summary.String(), nil
}

// TODO: RegisterTool takes MCPManager for consistency with other tool packages.
// See kubernetes/kubernetes.go for the same pattern and refactoring note.
func RegisterTool(client *Client, manager *mcpClient.MCPManager) llms.Tool {
	envNames := make([]string, 0, len(client.projects))
	for envName := range client.projects {
		envNames = append(envNames, envName)
	}

	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "glitchtip_get_errors",
			Description: "Query GlitchTip for application errors. Fast (< 2s). Primary source for application exceptions.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"environment": map[string]any{"type": "string", "description": fmt.Sprintf("Environment: %s", strings.Join(envNames, ", "))},
					"action":      map[string]any{"type": "string", "description": "Action: issues (default), latest, issue <id>, events <id>"},
					"detail":      map[string]any{"type": "boolean", "description": "Return full stack traces (default: false)"},
				},
				"required": []string{"environment"},
			},
		},
	}

	handler := func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Environment string `json:"environment"`
			Action      string `json:"action,omitempty"`
			Detail      bool   `json:"detail,omitempty"`
		}
		if argsJSON != "" {
			json.Unmarshal([]byte(argsJSON), &args)
		}

		project, found := client.projects[args.Environment]
		if !found {
			return "", fmt.Errorf("unknown environment %q, valid: %s", args.Environment, strings.Join(envNames, ", "))
		}

		action := args.Action
		if action == "" {
			action = "issues"
		}

		resolvedParams := map[string]string{
			"environment": args.Environment,
			"project":     project.Slug,
			"action":      action,
		}

		resultText, queryErr := client.fetchIssues(ctx, project)
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
	fmt.Fprintf(os.Stderr, "[glitchtip] registered glitchtip_get_errors tool\n")
	return tool
}
