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

	"github.com/pulp/pulp-service/tools/agents/shared/auth"
	"github.com/pulp/pulp-service/tools/agents/shared/models"
	"github.com/pulp/pulp-service/tools/agents/shared/sumo"
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
	inputGitLabRepo := flag.String("gitlab-repo", "", "GitLab repository URL to publish reports to")
	flag.Parse()

	supportedModels := map[string]struct{}{
		"claude-opus-4-6": {},
		"gemini-2.5-pro":  {},
	}
	if _, exists := supportedModels[*inputModel]; !exists {
		return fmt.Errorf("unsupported model: %s. Supported models: claude-opus-4-6, gemini-2.5-pro", *inputModel)
	}

	gitlabToken := ""
	if *inputGitLabRepo != "" {
		gitlabToken = os.Getenv("GITLAB_TOKEN")
		if gitlabToken == "" {
			return fmt.Errorf("GITLAB_TOKEN is required when --gitlab-repo is set")
		}
	}

	// Sumo Logic (Akamai edge logs) is required for this agent.
	// Configured via SUMO_API_URL, SUMO_ACCESS_ID, SUMO_ACCESS_KEY.
	sumoClient, err := sumo.NewClientFromEnv()
	if err != nil {
		return fmt.Errorf("sumo: %w", err)
	}
	fmt.Fprintf(os.Stderr, "[sumo] querying Akamai traffic data for the last 7 days (%s)...\n", sumoClient.Endpoint())

	report, err := queryTraffic(ctx, sumoClient)
	if err != nil {
		return fmt.Errorf("traffic report search: %w", err)
	}
	results, err := report.JSON()
	if err != nil {
		return fmt.Errorf("encode results: %w", err)
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
	promptData, err := report.promptData()
	if err != nil {
		return fmt.Errorf("build prompt data: %w", err)
	}
	prompt := fmt.Sprintf("%s\n\n%s", question, promptData)

	// No tools: the model only summarizes the data above. Giving it a search
	// tool would let request paths in the logs (attacker-controlled text) steer
	// queries against everything the Sumo credentials can read.
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

	analysis, err := model.ModelRun(ctx, models.RunConfig{
		Prompt: prompt,
	})
	if err != nil {
		return fmt.Errorf("model run: %w", err)
	}

	fmt.Println(analysis)

	if *inputGitLabRepo != "" {
		publisher := newGitLabPublisher(*inputGitLabRepo, gitlabToken)
		defer publisher.Cleanup()
		if _, err := publisher.Publish(ctx, outputDir); err != nil {
			return fmt.Errorf("publish to GitLab: %w", err)
		}
	}

	return nil
}
