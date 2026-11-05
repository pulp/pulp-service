package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/tmc/langchaingo/llms"

	"agent-splunk/cloudwatch"
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
	const DEFAULT_QUESTION = "verify all splunk 5xx errors related to pulp in packages.redhat.com found in the last hour, " +
		"trace sample requests through the NSGW gateway and into Pulp, and determine which hop emitted each error"
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Auth mode 1: Proxy (ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL).
	// Gate handles real credentials; the agent sends requests to the proxy.
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	baseURL := normalizeBaseURL(os.Getenv("ANTHROPIC_BASE_URL"))

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
	dryRun := flag.Bool("dry-run", false, "Run the analysis only: Jira tools are not registered and no Jira issue is searched, created, or commented on")
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
	if *dryRun {
		fmt.Fprintf(os.Stderr, "[dry-run] analysis only, Jira disabled\n")
		promptParts = append(promptParts,
			"NOTE: this is a dry run. Jira is not available; only produce the analysis report.")
	}

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

	// --- CloudWatch: NSGW gateway + Pulp application logs (native tool) ---
	// Optional: without AWS credentials the agent still analyzes Splunk, but
	// reports the error origin as unknown.
	cwClient, cwErr := cloudwatch.NewClientFromEnv(ctx)
	if cwErr != nil {
		fmt.Fprintf(os.Stderr, "[cloudwatch] disabled, request tracing past Akamai unavailable: %v\n", cwErr)
		promptParts = append(promptParts,
			"NOTE: the cloudwatch_trace_logs tool is not available in this run (no AWS credentials). "+
				"Report the error origin as \"unknown (trace unavailable)\" for every error group.")
	} else {
		tools = append(tools, cwClient.RegisterTool(mcpMgr))
		fmt.Fprintf(os.Stderr, "[cloudwatch] registered cloudwatch_trace_logs tool\n")
	}

	// --- Jira (native tool) ---
	// Skipped entirely in dry-run so the analysis run cannot touch Jira either.
	if jiraURL := os.Getenv("JIRA_URL"); jiraURL != "" && !*dryRun {
		jiraCredential := os.Getenv("JIRA_API_TOKEN")
		if jiraCredential == "" {
			return fmt.Errorf("JIRA_API_TOKEN is required when JIRA_URL is set (format: email:api_token)")
		}
		jiraUsername, jiraToken, found := strings.Cut(jiraCredential, ":")
		if !found || jiraUsername == "" || jiraToken == "" {
			return fmt.Errorf("JIRA_API_TOKEN must be in email:api_token format")
		}
		jiraClient := jira.NewClient(jiraURL, jiraUsername, jiraToken)
		jiraTools := jiraClient.RegisterTools(mcpMgr)
		tools = append(tools, jiraTools...)
		fmt.Fprintf(os.Stderr, "[jira] registered %d jira tools (%s)\n", len(jiraTools), jiraURL)
	}

	prompt := strings.Join(promptParts, "\n")

	// Load embedded skills
	skillMsg := skills.LoadSkill()
	traceSkillMsg := skills.LoadTraceSkill()
	fmt.Fprintf(os.Stderr, "[skills] loaded jira-workflows.md, trace-requests.md\n")

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
		SystemMessages: []llms.MessageContent{skillMsg, traceSkillMsg},
	})
	if err != nil {
		return fmt.Errorf("model run: %w", err)
	}

	if *dryRun {
		fmt.Fprintf(os.Stderr, "[dry-run] skipping Jira issue creation\n")
		return nil
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

// normalizeBaseURL ensures the base URL ends with /v1, which langchaingo
// expects (it appends "/messages" to form the final endpoint).
func normalizeBaseURL(raw string) string {
	if raw == "" {
		return ""
	}
	u, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	u.Path = strings.TrimSuffix(u.Path, "/")
	u.Path = strings.TrimSuffix(u.Path, "/messages")
	u.Path = strings.TrimSuffix(u.Path, "/")
	if !strings.HasSuffix(u.Path, "/v1") {
		u.Path += "/v1"
	}
	return u.String()
}
