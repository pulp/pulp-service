package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"agent-scan/models"
	"agent-scan/skills"
	"agent-scan/tools"

	"github.com/tmc/langchaingo/llms"
)

const INPUT = "Scan this package archive for malware and vulnerabilities. " +
	"Use the list_archive_files tool to see the files, then read_archive_file to inspect them. " +
	"Return a JSON report as described in your instructions."

func main() {
	// get model credentials
	projectID := os.Getenv("ANTHROPIC_VERTEX_PROJECT_ID")
	if projectID == "" {
		log.Fatal("ANTHROPIC_VERTEX_PROJECT_ID env var is required")
	}

	region := os.Getenv("CLOUD_ML_REGION")
	if region == "" {
		region = "us-east5"
	}

	// get program arguments
	inputModel := flag.String("model", "claude", "Define the model (claude, gemini). Default: claude")
	inputPackages := flag.String("files", "", "Comma-separated list of .tar.gz or .whl package paths to scan")
	flag.Parse()

	supportedModels := map[string]string{
		"claude": "claude-opus-4-6",
		"gemini": "gemini-2.5-pro",
	}
	userModel, exists := supportedModels[*inputModel]
	if !exists {
		log.Fatalf("unsupported model: %s. Supported models: claude, gemini\n", *inputModel)
	}

	//build the prompt from file flags
	if *inputPackages == "" {
		log.Fatal("no package provided")
	}
	filePaths := strings.Split(*inputPackages, ",")

	// Load the skill and tools
	skillMsg := skills.MalwareAndVulnSkill()
	scanTools := tools.ScanTools()

	// define the model
	var model models.Model
	switch *inputModel {
	case "claude":
		model = models.Claude{Model: userModel, ProjectID: projectID, Region: region}
	case "gemini":
		model = models.Gemini{Model: userModel, ProjectID: projectID, Region: region}
	}

	// Scan each archive using the agentic tool-calling loop.
	for _, pkg := range filePaths {
		fmt.Printf("\n=== Scanning archive: %s ===\n", filepath.Base(pkg))

		prompt := fmt.Sprintf("Package archive path: %s\n\n%s", pkg, INPUT)

		_, err := model.ModelRun(context.TODO(), models.RunConfig{
			Prompt:         prompt,
			Tools:          scanTools,
			SystemMessages: []llms.MessageContent{skillMsg},
			ExecuteTool:    tools.ExecuteTool,
		})
		if err != nil {
			log.Printf("error scanning %s: %v", pkg, err)
		}
	}
}
