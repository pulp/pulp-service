package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/tmc/langchaingo/llms"

	"agent-splunk/jira"
	mcpClient "agent-splunk/mcp-client"
	"agent-splunk/models"
	"agent-splunk/skills"
	"agent-splunk/splunk"
)

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	const DEFAULT_QUESTION = "verify all splunk 5xx errors related to pulp in packages.redhat.com found in the last hour"
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Auth mode 1: Proxy (ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL).
	// Gate handles real credentials; the agent sends requests to the proxy.
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	baseURL := os.Getenv("ANTHROPIC_BASE_URL")

	// Auth mode 2: Service account JSON for direct Vertex AI access.
	var saCredential []byte
	if saJSON := os.Getenv("VERTEX_SA_JSON"); saJSON != "" {
		saCredential = []byte(saJSON)
		fmt.Fprintf(os.Stderr, "[auth] using VERTEX_SA_JSON for Vertex AI authentication\n")
	}

	// Resolve project ID (required for Vertex AI modes, not for proxy mode).
	projectID := os.Getenv("ANTHROPIC_VERTEX_PROJECT_ID")
	if projectID == "" && len(saCredential) > 0 {
		var sa struct {
			ProjectID string `json:"project_id"`
		}
		if err := json.Unmarshal(saCredential, &sa); err != nil {
			return fmt.Errorf("VERTEX_SA_JSON is not valid JSON: %w", err)
		}
		if sa.ProjectID == "" {
			return fmt.Errorf("VERTEX_SA_JSON does not contain a project_id field")
		}
		projectID = sa.ProjectID
	}

	if apiKey == "" && projectID == "" {
		return fmt.Errorf("set ANTHROPIC_API_KEY (proxy mode) or ANTHROPIC_VERTEX_PROJECT_ID / VERTEX_SA_JSON (Vertex AI mode)")
	}

	if apiKey != "" {
		fmt.Fprintf(os.Stderr, "[auth] using proxy mode (ANTHROPIC_BASE_URL=%s)\n", baseURL)
	}

	region := os.Getenv("CLOUD_ML_REGION")
	if region == "" {
		region = "us-east5"
	}

	defaultModel := "claude-opus-4-6"
	if envModel := os.Getenv("CLAUDE_MODEL"); envModel != "" {
		defaultModel = envModel
	}

	inputModel := flag.String("model", defaultModel, fmt.Sprintf("Define the model (claude-opus-4-6,gemini-2.5-pro). Default: %s", defaultModel))
	inputQuestion := flag.String("question", "", "Question to ask the model")
	flag.Parse()

	supportedModels := map[string]struct{}{
		"claude-opus-4-6": {},
		"gemini-2.5-pro":  {},
	}
	if _, exists := supportedModels[*inputModel]; !exists {
		return fmt.Errorf("unsupported model: %s. Supported models: claude-opus-4-6, gemini-2.5-pro", *inputModel)
	}

	question := *inputQuestion

	var promptParts []string
	if question == "" {
		question = DEFAULT_QUESTION
	}
	promptParts = append(promptParts, question)
	prompt := strings.Join(promptParts, "\n")

	// MCPManager routes tool calls to the correct handler (native or MCP).
	mcpMgr := mcpClient.NewMCPManager()
	var tools []llms.Tool

	// --- Splunk (native tool) ---
	if splunkURL := os.Getenv("SPLUNK_URL"); splunkURL != "" {
		splunkToken := os.Getenv("SPLUNK_TOKEN")
		if splunkToken == "" {
			return fmt.Errorf("SPLUNK_TOKEN is required when SPLUNK_URL is set")
		}
		splunkClient := splunk.NewClient(splunkURL, splunkToken)
		tools = append(tools, splunkClient.RegisterTool(mcpMgr))
		fmt.Fprintf(os.Stderr, "[splunk] registered splunk_search tool (%s)\n", splunkURL)
	}

	// --- Jira (native tool) ---
	if jiraURL := os.Getenv("JIRA_URL"); jiraURL != "" {
		jiraUsername := os.Getenv("JIRA_USERNAME")
		jiraToken := os.Getenv("JIRA_API_TOKEN")
		if jiraUsername == "" || jiraToken == "" {
			return fmt.Errorf("JIRA_USERNAME and JIRA_API_TOKEN are required when JIRA_URL is set")
		}
		jiraClient := jira.NewClient(jiraURL, jiraUsername, jiraToken)
		jiraTools := jiraClient.RegisterTools(mcpMgr)
		tools = append(tools, jiraTools...)
		fmt.Fprintf(os.Stderr, "[jira] registered %d jira tools (%s)\n", len(jiraTools), jiraURL)
	}

	// Load embedded skill
	skillMsg := skills.LoadSkill()
	fmt.Fprintf(os.Stderr, "[skills] loaded jira-workflow.md\n")

	var model models.Model
	switch *inputModel {
	case "claude-opus-4-6":
		model = models.Claude{
			Model:        *inputModel,
			ProjectID:    projectID,
			Region:       region,
			SACredential: saCredential,
			APIKey:       apiKey,
			BaseURL:      baseURL,
		}
	case "gemini-2.5-pro":
		model = models.Gemini{
			Model:     *inputModel,
			ProjectID: projectID,
			Region:    region,
		}
	}

	// execute the prompt
	result, err := model.ModelRun(ctx, models.RunConfig{
		Prompt:         prompt,
		Tools:          tools,
		MCP:            mcpMgr,
		SystemMessages: []llms.MessageContent{skillMsg},
	})
	if err != nil {
		return fmt.Errorf("model run: %w", err)
	}

	// open Jira ticket if an issue is found
	jiraPrompt := skills.OpenJiraIssue(result, question)
	_, err = model.ModelRun(ctx, models.RunConfig{
		Prompt:         jiraPrompt,
		Tools:          tools,
		MCP:            mcpMgr,
		SystemMessages: []llms.MessageContent{skillMsg},
	})
	if err != nil {
		return fmt.Errorf("jira model run: %w", err)
	}

	return nil
}
