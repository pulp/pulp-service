package main

import (
	"context"
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

	projectID := os.Getenv("ANTHROPIC_VERTEX_PROJECT_ID")
	if projectID == "" {
		return fmt.Errorf("ANTHROPIC_VERTEX_PROJECT_ID environment variable is required")
	}

	region := os.Getenv("CLOUD_ML_REGION")
	if region == "" {
		region = "us-east5"
	}

	inputModel := flag.String("model", "claude-opus-4-6", "Define the model (claude-opus-4-6,gemini-2.5-pro). Default: claude-opus-4-6")
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
			Model:     *inputModel,
			ProjectID: projectID,
			Region:    region,
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
