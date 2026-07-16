package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/tmc/langchaingo/llms"

	"github.com/pulp/pulp-service/tools/agents/shared/auth"
	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/shared/models"
	"github.com/pulp/pulp-service/tools/agents/shared/skills/splunk"
)

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	authConfig, err := auth.ResolveFromEnv()
	if err != nil {
		return fmt.Errorf("LLM auth: %w", err)
	}
	if authConfig.APIKey != "" {
		fmt.Fprintf(os.Stderr, "[auth] using proxy mode (ANTHROPIC_BASE_URL=%s)\n", authConfig.BaseURL)
	}

	llmModel := "claude-opus-4-6"
	if envModel := os.Getenv("CLAUDE_MODEL"); envModel != "" {
		llmModel = envModel
	}

	inputModel := flag.String("model", llmModel, "LLM model (claude-opus-4-6, gemini-2.5-pro)")
	inputQuestion := flag.String("question", "", "Question to ask the model about the traffic data")
	inputOutputDir := flag.String("output-dir", "", "Output directory for results (default: /tmp/traffic-report-<timestamp>)")
	flag.Parse()

	supportedModels := map[string]struct{}{
		"claude-opus-4-6": {},
		"gemini-2.5-pro":  {},
	}
	if _, exists := supportedModels[*inputModel]; !exists {
		return fmt.Errorf("unsupported model: %s. Supported models: claude-opus-4-6, gemini-2.5-pro", *inputModel)
	}

	// Splunk is required for this agent.
	splunkURL := os.Getenv("SPLUNK_URL")
	if splunkURL == "" {
		return fmt.Errorf("SPLUNK_URL is required")
	}
	splunkToken := os.Getenv("SPLUNK_TOKEN")
	if splunkToken == "" {
		return fmt.Errorf("SPLUNK_TOKEN is required")
	}

	splunkClient := splunk.NewClient(splunkURL, splunkToken)
	fmt.Fprintf(os.Stderr, "[splunk] querying Akamai traffic data for the last 7 days...\n")

	// Query Splunk for traffic data.
	results, err := splunkClient.Search(ctx, splunk.SPLUNK_TRAFFIC_QUERY, "-7d", "now", 0)
	if err != nil {
		return fmt.Errorf("traffic report search: %w", err)
	}

	// Save results to file.
	outputDir := *inputOutputDir
	if outputDir == "" {
		outputDir = fmt.Sprintf("/tmp/traffic-report-%d", time.Now().Unix())
	}
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return fmt.Errorf("create output directory: %w", err)
	}
	outPath := filepath.Join(outputDir, "traffic.json")
	if err := os.WriteFile(outPath, []byte(results), 0o644); err != nil {
		return fmt.Errorf("write results: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[traffic] results written to %s\n", outPath)

	htmlPath, err := generateHTMLReport(outputDir, results)
	if err != nil {
		return fmt.Errorf("generate HTML report: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[traffic] HTML report written to %s\n", htmlPath)

	// Build the LLM prompt.
	question := *inputQuestion
	if question == "" {
		question = "Analyze the following 7-day Akamai traffic report for hosted Pulp. " +
			"Present the data as a formatted table with columns: reqHost, base_path, count. " +
			"Sort by count descending. After the table, summarize notable patterns, " +
			"top consumers, and any anomalies."
	}
	prompt := fmt.Sprintf("%s\n\n%s", question, results)

	// Register the splunk_search tool so the LLM can run follow-up queries.
	mcpMgr := mcpClient.NewMCPManager()
	var tools []llms.Tool
	tools = append(tools, splunkClient.RegisterTool(mcpMgr))

	var model models.Model
	switch *inputModel {
	case "claude-opus-4-6":
		model = models.Claude{
			Model:     *inputModel,
			ProjectID: authConfig.ProjectID,
			Region:    authConfig.Region,
			APIKey:    authConfig.APIKey,
			BaseURL:   authConfig.BaseURL,
		}
	case "gemini-2.5-pro":
		model = models.Gemini{
			Model:     *inputModel,
			ProjectID: authConfig.ProjectID,
			Region:    authConfig.Region,
		}
	}

	report, err := model.ModelRun(ctx, models.RunConfig{
		Prompt: prompt,
		Tools:  tools,
		MCP:    mcpMgr,
	})
	if err != nil {
		return fmt.Errorf("model run: %w", err)
	}

	fmt.Println(report)
	return nil
}
