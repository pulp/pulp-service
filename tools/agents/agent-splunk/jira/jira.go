package jira

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/tmc/langchaingo/llms"

	mcpClient "agent-splunk/mcp-client"
	"agent-splunk/utils"
)

// Client handles communication with the Jira REST API v3.
type Client struct {
	BaseURL    string
	Username   string
	Token      string
	HTTPClient *http.Client
}

// NewClient creates a Jira client using basic auth (username + API token).
func NewClient(baseURL, username, token string) *Client {
	return &Client{
		BaseURL:  strings.TrimRight(baseURL, "/"),
		Username: username,
		Token:    token,
		HTTPClient: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

func (c *Client) authHeader() string {
	creds := c.Username + ":" + c.Token
	return "Basic " + base64.StdEncoding.EncodeToString([]byte(creds))
}

func (c *Client) doRequest(ctx context.Context, method, path string, body any) ([]byte, error) {
	url := c.BaseURL + path

	var reqBody io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("marshal request body: %w", err)
		}
		reqBody = bytes.NewReader(b)
	}

	req, err := http.NewRequestWithContext(ctx, method, url, reqBody)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", c.authHeader())
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("Jira API %s %s returned %d: %s", method, path, resp.StatusCode, string(respBody))
	}

	// 204 No Content is valid for DELETE operations.
	if resp.StatusCode == http.StatusNoContent {
		return []byte(`{"status":"ok"}`), nil
	}

	return respBody, nil
}

// RegisterTools registers all Jira tools on the given MCPManager and returns
// the langchaingo tool definitions.
func (c *Client) RegisterTools(mgr *mcpClient.MCPManager) []llms.Tool {
	tools := []llms.Tool{
		c.searchTool(mgr),
		c.getIssueTool(mgr),
		c.getCommentsTool(mgr),
		c.addCommentTool(mgr),
		c.createIssueTool(mgr),
		c.editIssueTool(mgr),
		c.createIssueLinkTool(mgr),
		c.deleteIssueLinkTool(mgr),
		c.getTransitionsTool(mgr),
		c.transitionIssueTool(mgr),
	}
	return tools
}

func (c *Client) searchTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_search",
			Description: "Search for Jira issues using JQL (Jira Query Language). Returns matching issues with key, summary, status, assignee, and other fields.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"jql": map[string]any{
						"type":        "string",
						"description": "The JQL query string to search for issues.",
					},
					"max_results": map[string]any{
						"type":        "integer",
						"description": "Maximum number of results to return. Default: 20.",
					},
				},
				"required": []string{"jql"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			JQL        string `json:"jql"`
			MaxResults int    `json:"max_results"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_search arguments: %w", err)
		}
		if args.MaxResults <= 0 {
			args.MaxResults = 20
		}

		body := map[string]any{
			"jql":        args.JQL,
			"maxResults": args.MaxResults,
			"fields":     []string{"summary", "status", "assignee", "labels", "priority", "components", "issuetype", "created", "updated", "description"},
		}
		resp, err := c.doRequest(ctx, http.MethodPost, "/rest/api/3/search/jql", body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) getIssueTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_get_issue",
			Description: "Retrieve full details for a single Jira issue by its key (e.g. PULP-1234). Optionally specify which fields to return.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
					"fields": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "Optional list of field names to return. If omitted, all fields are returned.",
					},
				},
				"required": []string{"issue_key"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey string   `json:"issue_key"`
			Fields   []string `json:"fields"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_get_issue arguments: %w", err)
		}

		path := "/rest/api/3/issue/" + args.IssueKey
		if len(args.Fields) > 0 {
			path += "?fields=" + strings.Join(args.Fields, ",")
		}
		resp, err := c.doRequest(ctx, http.MethodGet, path, nil)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) getCommentsTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_get_comments",
			Description: "Retrieve all comments on a Jira issue.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
				},
				"required": []string{"issue_key"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey string `json:"issue_key"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_get_comments arguments: %w", err)
		}

		resp, err := c.doRequest(ctx, http.MethodGet, "/rest/api/3/issue/"+args.IssueKey+"/comment", nil)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) addCommentTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_add_comment",
			Description: "Add a comment to a Jira issue. Optionally restrict visibility to a specific group.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
					"comment": map[string]any{
						"type":        "string",
						"description": "The comment text to add.",
					},
					"visibility_type": map[string]any{
						"type":        "string",
						"description": "Visibility restriction type: 'group' or 'role'. Optional.",
					},
					"visibility_value": map[string]any{
						"type":        "string",
						"description": "The group or role name for visibility restriction, e.g. 'Red Hat Employee'. Optional.",
					},
				},
				"required": []string{"issue_key", "comment"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey        string `json:"issue_key"`
			Comment         string `json:"comment"`
			VisibilityType  string `json:"visibility_type"`
			VisibilityValue string `json:"visibility_value"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_add_comment arguments: %w", err)
		}

		body := map[string]any{
			"body": adfTextBlock(args.Comment),
		}
		if args.VisibilityType != "" && args.VisibilityValue != "" {
			body["visibility"] = map[string]any{
				"type":       args.VisibilityType,
				"identifier": args.VisibilityValue,
			}
		}

		resp, err := c.doRequest(ctx, http.MethodPost, "/rest/api/3/issue/"+args.IssueKey+"/comment", body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) createIssueTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_create_issue",
			Description: "Create a new Jira issue. Note: the 'components' and 'parent' fields are silently ignored at creation time by Jira Cloud — use jira_edit_issue after creation to set them.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"project": map[string]any{
						"type":        "string",
						"description": "The project key, e.g. PULP.",
					},
					"issue_type": map[string]any{
						"type":        "string",
						"description": "The issue type: Task, Bug, Epic, Feature.",
					},
					"summary": map[string]any{
						"type":        "string",
						"description": "The issue summary/title.",
					},
					"description": map[string]any{
						"type":        "string",
						"description": "The issue description text.",
					},
					"priority": map[string]any{
						"type":        "string",
						"description": "Priority: Blocker, Critical, Major, Normal, Minor.",
					},
					"labels": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "Labels to apply to the issue.",
					},
					"components": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "Component names, e.g. ['services']. Note: may need to be set via jira_edit_issue after creation.",
					},
					"additional_fields": map[string]any{
						"type":        "string",
						"description": "JSON string of additional fields to set, e.g. '{\"parent\": \"PULP-1030\", \"customfield_10011\": \"Epic name\"}'.",
					},
				},
				"required": []string{"project", "issue_type", "summary"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Project          string   `json:"project"`
			IssueType        string   `json:"issue_type"`
			Summary          string   `json:"summary"`
			Description      string   `json:"description"`
			Priority         string   `json:"priority"`
			Labels           []string `json:"labels"`
			Components       []string `json:"components"`
			AdditionalFields string   `json:"additional_fields"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_create_issue arguments: %w", err)
		}

		fields := map[string]any{
			"project":   map[string]any{"key": args.Project},
			"issuetype": map[string]any{"name": args.IssueType},
			"summary":   args.Summary,
		}
		if args.Description != "" {
			fields["description"] = adfTextBlock(args.Description)
		}
		if args.Priority != "" {
			fields["priority"] = map[string]any{"name": args.Priority}
		}
		if len(args.Labels) > 0 {
			fields["labels"] = args.Labels
		}
		if len(args.Components) > 0 {
			var comps []map[string]any
			for _, name := range args.Components {
				comps = append(comps, map[string]any{"name": name})
			}
			fields["components"] = comps
		}

		// Merge additional fields.
		if args.AdditionalFields != "" {
			var extra map[string]any
			if err := json.Unmarshal([]byte(args.AdditionalFields), &extra); err != nil {
				return "", fmt.Errorf("parse additional_fields: %w", err)
			}
			for k, v := range extra {
				fields[k] = v
			}
		}

		body := map[string]any{"fields": fields}

		fmt.Fprintf(os.Stderr, "[jira] creating issue in project %s\n", args.Project)
		resp, err := c.doRequest(ctx, http.MethodPost, "/rest/api/3/issue", body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) editIssueTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_edit_issue",
			Description: "Edit fields on an existing Jira issue. Use this to set components, parent (epic link), custom fields, or any other editable field.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
					"summary": map[string]any{
						"type":        "string",
						"description": "New summary/title. Optional.",
					},
					"description": map[string]any{
						"type":        "string",
						"description": "New description text. Optional.",
					},
					"priority": map[string]any{
						"type":        "string",
						"description": "New priority. Optional.",
					},
					"labels": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "Labels to set. Optional.",
					},
					"components": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "Component objects, e.g. [{\"name\": \"services\"}]. Pass as JSON array of objects.",
					},
					"additional_fields": map[string]any{
						"type":        "string",
						"description": "JSON string of additional fields to set, e.g. '{\"parent\": {\"key\": \"PULP-1030\"}, \"customfield_10517\": {\"id\": \"10852\"}}'.",
					},
				},
				"required": []string{"issue_key"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey         string   `json:"issue_key"`
			Summary          string   `json:"summary"`
			Description      string   `json:"description"`
			Priority         string   `json:"priority"`
			Labels           []string `json:"labels"`
			Components       []any    `json:"components"`
			AdditionalFields string   `json:"additional_fields"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_edit_issue arguments: %w", err)
		}

		fields := map[string]any{}
		if args.Summary != "" {
			fields["summary"] = args.Summary
		}
		if args.Description != "" {
			fields["description"] = adfTextBlock(args.Description)
		}
		if args.Priority != "" {
			fields["priority"] = map[string]any{"name": args.Priority}
		}
		if args.Labels != nil {
			fields["labels"] = args.Labels
		}
		if args.Components != nil {
			fields["components"] = args.Components
		}

		if args.AdditionalFields != "" {
			var extra map[string]any
			if err := json.Unmarshal([]byte(args.AdditionalFields), &extra); err != nil {
				return "", fmt.Errorf("parse additional_fields: %w", err)
			}
			for k, v := range extra {
				fields[k] = v
			}
		}

		body := map[string]any{"fields": fields}

		fmt.Fprintf(os.Stderr, "[jira] editing issue %s\n", args.IssueKey)
		resp, err := c.doRequest(ctx, http.MethodPut, "/rest/api/3/issue/"+args.IssueKey, body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) createIssueLinkTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_create_issue_link",
			Description: "Create a link between two Jira issues. For 'Blocks' links: inwardIssue is the blocker, outwardIssue is the blocked issue.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"type": map[string]any{
						"type":        "string",
						"description": "The link type name, e.g. 'Blocks', 'Cloners', 'Duplicate'.",
					},
					"inward_issue": map[string]any{
						"type":        "string",
						"description": "The inward issue key (e.g. the blocker in a Blocks relationship).",
					},
					"outward_issue": map[string]any{
						"type":        "string",
						"description": "The outward issue key (e.g. the blocked issue in a Blocks relationship).",
					},
				},
				"required": []string{"type", "inward_issue", "outward_issue"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			Type         string `json:"type"`
			InwardIssue  string `json:"inward_issue"`
			OutwardIssue string `json:"outward_issue"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_create_issue_link arguments: %w", err)
		}

		body := map[string]any{
			"type":         map[string]any{"name": args.Type},
			"inwardIssue":  map[string]any{"key": args.InwardIssue},
			"outwardIssue": map[string]any{"key": args.OutwardIssue},
		}

		resp, err := c.doRequest(ctx, http.MethodPost, "/rest/api/3/issueLink", body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) deleteIssueLinkTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_delete_issue_link",
			Description: "Delete an issue link by its ID.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"link_id": map[string]any{
						"type":        "string",
						"description": "The issue link ID to delete.",
					},
				},
				"required": []string{"link_id"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			LinkID string `json:"link_id"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_delete_issue_link arguments: %w", err)
		}

		resp, err := c.doRequest(ctx, http.MethodDelete, "/rest/api/3/issueLink/"+args.LinkID, nil)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) getTransitionsTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_get_transitions",
			Description: "Get available status transitions for a Jira issue.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
				},
				"required": []string{"issue_key"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey string `json:"issue_key"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_get_transitions arguments: %w", err)
		}

		resp, err := c.doRequest(ctx, http.MethodGet, "/rest/api/3/issue/"+args.IssueKey+"/transitions", nil)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

func (c *Client) transitionIssueTool(mgr *mcpClient.MCPManager) llms.Tool {
	tool := llms.Tool{
		Type: "function",
		Function: &llms.FunctionDefinition{
			Name:        "jira_transition_issue",
			Description: "Transition a Jira issue to a new status. Use jira_get_transitions first to get the transition ID.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"issue_key": map[string]any{
						"type":        "string",
						"description": "The Jira issue key, e.g. PULP-1234.",
					},
					"transition_id": map[string]any{
						"type":        "string",
						"description": "The transition ID from jira_get_transitions.",
					},
					"fields": map[string]any{
						"type":        "string",
						"description": "Optional JSON string of fields to set during transition, e.g. '{\"resolution\": {\"name\": \"Duplicate\"}}'.",
					},
				},
				"required": []string{"issue_key", "transition_id"},
			},
		},
	}

	mgr.RegisterNativeTool(tool, func(ctx context.Context, argsJSON string) (string, error) {
		var args struct {
			IssueKey     string `json:"issue_key"`
			TransitionID string `json:"transition_id"`
			Fields       string `json:"fields"`
		}
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return "", fmt.Errorf("parse jira_transition_issue arguments: %w", err)
		}

		body := map[string]any{
			"transition": map[string]any{"id": args.TransitionID},
		}
		if args.Fields != "" {
			var fields map[string]any
			if err := json.Unmarshal([]byte(args.Fields), &fields); err != nil {
				return "", fmt.Errorf("parse transition fields: %w", err)
			}
			body["fields"] = fields
		}

		resp, err := c.doRequest(ctx, http.MethodPost, "/rest/api/3/issue/"+args.IssueKey+"/transitions", body)
		if err != nil {
			return "", err
		}
		return utils.Truncate(string(resp), 30000), nil
	})

	return tool
}

// adfTextBlock converts Jira wiki markup to Atlassian Document Format (ADF),
// which is required by Jira Cloud REST API v3 for description and comment bodies.
// Supported: headings (h1.-h6.), bold (*text*), inline code ({{text}}),
// bullet lists (* item), tables (||header|| and |cell|), horizontal rules (----).
// Only ---- is recognized as a rule, matching Jira's documented syntax.
func adfTextBlock(text string) map[string]any {
	lines := strings.Split(text, "\n")
	var content []map[string]any

	i := 0
	for i < len(lines) {
		trimmed := strings.TrimSpace(lines[i])

		if trimmed == "" {
			i++
			continue
		}

		if level, title, ok := parseWikiHeading(trimmed); ok {
			content = append(content, map[string]any{
				"type":    "heading",
				"attrs":   map[string]any{"level": level},
				"content": parseWikiInline(title),
			})
			i++
			continue
		}

		if isWikiTableRow(trimmed) {
			var rows []map[string]any
			for i < len(lines) {
				t := strings.TrimSpace(lines[i])
				if !isWikiTableRow(t) {
					break
				}
				rows = append(rows, parseWikiTableRow(t))
				i++
			}
			content = append(content, map[string]any{
				"type":    "table",
				"content": rows,
			})
			continue
		}

		if strings.HasPrefix(trimmed, "* ") {
			var items []map[string]any
			for i < len(lines) {
				t := strings.TrimSpace(lines[i])
				if !strings.HasPrefix(t, "* ") {
					break
				}
				items = append(items, map[string]any{
					"type": "listItem",
					"content": []map[string]any{
						{
							"type":    "paragraph",
							"content": parseWikiInline(t[2:]),
						},
					},
				})
				i++
			}
			content = append(content, map[string]any{
				"type":    "bulletList",
				"content": items,
			})
			continue
		}

		if trimmed == "----" {
			content = append(content, map[string]any{"type": "rule"})
			i++
			continue
		}

		content = append(content, map[string]any{
			"type":    "paragraph",
			"content": parseWikiInline(trimmed),
		})
		i++
	}

	if len(content) == 0 {
		content = []map[string]any{
			{
				"type":    "paragraph",
				"content": []map[string]any{{"type": "text", "text": text}},
			},
		}
	}

	return map[string]any{
		"type":    "doc",
		"version": 1,
		"content": content,
	}
}

func parseWikiHeading(line string) (int, string, bool) {
	for _, p := range [6]string{"h1. ", "h2. ", "h3. ", "h4. ", "h5. ", "h6. "} {
		if strings.HasPrefix(line, p) {
			return int(p[1] - '0'), line[len(p):], true
		}
	}
	return 0, "", false
}

func isWikiTableRow(line string) bool {
	return (strings.HasPrefix(line, "||") && strings.HasSuffix(line, "||")) ||
		(strings.HasPrefix(line, "|") && strings.HasSuffix(line, "|"))
}

func parseWikiTableRow(line string) map[string]any {
	isHeader := strings.HasPrefix(line, "||")
	var cells []string
	var cellType string

	if isHeader {
		cellType = "tableHeader"
		inner := line[2 : len(line)-2]
		cells = strings.Split(inner, "||")
	} else {
		cellType = "tableCell"
		inner := line[1 : len(line)-1]
		cells = strings.Split(inner, "|")
	}

	var rowContent []map[string]any
	for _, cell := range cells {
		rowContent = append(rowContent, map[string]any{
			"type": cellType,
			"content": []map[string]any{
				{
					"type":    "paragraph",
					"content": parseWikiInline(strings.TrimSpace(cell)),
				},
			},
		})
	}

	return map[string]any{
		"type":    "tableRow",
		"content": rowContent,
	}
}

func parseWikiInline(text string) []map[string]any {
	var nodes []map[string]any
	var buf strings.Builder

	flushBuf := func() {
		if buf.Len() > 0 {
			nodes = append(nodes, map[string]any{
				"type": "text",
				"text": unescapeWiki(buf.String()),
			})
			buf.Reset()
		}
	}

	i := 0
	for i < len(text) {
		// Escape sequence — accumulate literally so unescapeWiki handles it later.
		if text[i] == '\\' && i+1 < len(text) && (text[i+1] == '{' || text[i+1] == '}' || text[i+1] == '\\') {
			buf.WriteByte(text[i])
			buf.WriteByte(text[i+1])
			i += 2
			continue
		}

		// Inline code: {{...}}
		if i+1 < len(text) && text[i] == '{' && text[i+1] == '{' {
			if end := strings.Index(text[i+2:], "}}"); end >= 0 {
				flushBuf()
				nodes = append(nodes, map[string]any{
					"type":  "text",
					"text":  unescapeWiki(text[i+2 : i+2+end]),
					"marks": []map[string]any{{"type": "code"}},
				})
				i = i + 2 + end + 2
				continue
			}
		}

		// Bold: *...*  (word-boundary rules matching Jira's renderer)
		if text[i] == '*' {
			if end := strings.Index(text[i+1:], "*"); end > 0 {
				bold := text[i+1 : i+1+end]
				closeIdx := i + 1 + end
				beforeOK := i == 0 || !isAlnum(text[i-1])
				afterOK := closeIdx+1 >= len(text) || !isAlnum(text[closeIdx+1])
				if bold[0] != ' ' && bold[len(bold)-1] != ' ' && beforeOK && afterOK {
					flushBuf()
					nodes = append(nodes, map[string]any{
						"type":  "text",
						"text":  unescapeWiki(bold),
						"marks": []map[string]any{{"type": "strong"}},
					})
					i = i + 1 + end + 1
					continue
				}
			}
		}

		buf.WriteByte(text[i])
		i++
	}

	flushBuf()

	if len(nodes) == 0 {
		return []map[string]any{{"type": "text", "text": ""}}
	}
	return nodes
}

// unescapeWiki resolves wiki escape sequences: \{ → {, \} → }, \\ → \.
// This means \\{ in the input produces a literal \{ in the output.
func unescapeWiki(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			if next := s[i+1]; next == '{' || next == '}' || next == '\\' {
				b.WriteByte(next)
				i++
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

func isAlnum(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
}
