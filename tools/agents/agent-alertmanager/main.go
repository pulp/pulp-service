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
	"time"

	"github.com/tmc/langchaingo/llms"

	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/alertmanager"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/dedup"
	localSkills "github.com/pulp/pulp-service/tools/agents/agent-alertmanager/skills"
	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/shared/models"
	"github.com/pulp/pulp-service/tools/agents/shared/skills"
)

const (
	defaultModel        = "claude-sonnet-4-6"
	defaultQuestion     = "analyze all firing Pulp alerts and assess their severity and impact"
	defaultRegion       = "us-east5"
	defaultCooldown     = "30m"
	defaultCachePath    = "/tmp/agent-alertmanager-cache.json"
	defaultLockPath     = "/tmp/agent-alertmanager.lock"
	defaultMaxIter      = 20
	defaultTimeout      = 25 * time.Minute
	checkOnlyTimeout    = 10 * time.Second
)

// supportedModels lists the model identifiers this agent can use.
var supportedModels = map[string]bool{
	"claude-sonnet-4-6": true,
	"claude-opus-4-6":   true,
	"gemini-2.5-pro":    true,
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	modelFlag := flag.String("model", defaultModel, "LLM model to use (claude-sonnet-4-6, claude-opus-4-6, gemini-2.5-pro)")
	questionFlag := flag.String("question", defaultQuestion, "question / prompt to send to the model")
	checkOnlyFlag := flag.Bool("check-only", false, "lightweight mode: check for alerts and exit, no LLM")
	dryRunFlag := flag.Bool("dry-run", false, "analysis only: skip Jira mutations")
	flag.Parse()

	// --- Validate required env vars ---
	alertmanagerURL := os.Getenv("ALERTMANAGER_URL")
	if alertmanagerURL == "" {
		return fmt.Errorf("ALERTMANAGER_URL environment variable is required")
	}

	alertmanagerToken := os.Getenv("ALERTMANAGER_TOKEN")
	if alertmanagerToken == "" {
		return fmt.Errorf("ALERTMANAGER_TOKEN environment variable is required")
	}

	amClient := alertmanager.NewClient(alertmanagerURL, alertmanagerToken)

	// --- Check-only mode ---
	if *checkOnlyFlag {
		return runCheckOnly(amClient)
	}

	// --- Full mode: validate additional requirements ---
	vertexProjectID := os.Getenv("ANTHROPIC_VERTEX_PROJECT_ID")
	if vertexProjectID == "" {
		return fmt.Errorf("ANTHROPIC_VERTEX_PROJECT_ID environment variable is required in full mode")
	}

	if !supportedModels[*modelFlag] {
		supported := make([]string, 0, len(supportedModels))
		for modelName := range supportedModels {
			supported = append(supported, modelName)
		}
		return fmt.Errorf("unsupported model %q, supported: %s", *modelFlag, strings.Join(supported, ", "))
	}

	// --- Acquire exclusive file lock ---
	unlock, lockErr := dedup.AcquireLock(defaultLockPath)
	if lockErr != nil {
		// Another instance is running; exit silently.
		fmt.Fprintf(os.Stderr, "[main] lock already held, exiting: %v\n", lockErr)
		return nil
	}
	defer unlock()

	// --- Signal handling + timeout ---
	signalCtx, signalCancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer signalCancel()

	timeoutCtx, timeoutCancel := context.WithTimeout(signalCtx, defaultTimeout)
	defer timeoutCancel()

	// --- Parse optional env vars ---
	region := os.Getenv("CLOUD_ML_REGION")
	if region == "" {
		region = defaultRegion
	}

	cooldownStr := os.Getenv("DEDUP_COOLDOWN")
	if cooldownStr == "" {
		cooldownStr = defaultCooldown
	}
	cooldown, parseErr := time.ParseDuration(cooldownStr)
	if parseErr != nil {
		return fmt.Errorf("parse DEDUP_COOLDOWN %q: %w", cooldownStr, parseErr)
	}

	cachePath := os.Getenv("DEDUP_CACHE_PATH")
	if cachePath == "" {
		cachePath = defaultCachePath
	}

	// --- Initialize dedup cache ---
	dedupCache, cacheErr := dedup.NewCache(cachePath, cooldown)
	if cacheErr != nil {
		return fmt.Errorf("init dedup cache: %w", cacheErr)
	}
	defer func() {
		if saveErr := dedupCache.Save(); saveErr != nil {
			fmt.Fprintf(os.Stderr, "[main] warning: failed to save dedup cache: %v\n", saveErr)
		}
	}()

	// --- Connect MCP (graceful degradation) ---
	mcpAvailable := false
	mcpManager, connectErr := mcpClient.ConnectMCP(timeoutCtx, "agent-alertmanager")
	if connectErr != nil {
		fmt.Fprintf(os.Stderr, "[main] warning: MCP connection failed, continuing without Jira: %v\n", connectErr)
		mcpManager = mcpClient.NewMCPManager()
	} else if mcpManager == nil {
		fmt.Fprintf(os.Stderr, "[main] warning: no MCP servers configured, continuing without Jira\n")
		mcpManager = mcpClient.NewMCPManager()
	} else {
		mcpAvailable = true
		defer mcpManager.Close()
	}

	// --- Discover MCP tools ---
	var allTools []llms.Tool
	if mcpAvailable {
		discoveredTools, discoverErr := mcpManager.DiscoverTools(timeoutCtx)
		if discoverErr != nil {
			fmt.Fprintf(os.Stderr, "[main] warning: MCP tool discovery failed: %v\n", discoverErr)
		} else {
			allTools = append(allTools, discoveredTools...)
			fmt.Fprintf(os.Stderr, "[main] discovered %d MCP tools\n", len(discoveredTools))
		}
	}

	// --- Register AlertManager native tool ---
	alertTool := amClient.RegisterTool(mcpManager)
	allTools = append(allTools, alertTool)

	// --- Load skills ---
	jiraSkill := skills.LoadSkill()
	analysisSkills := localSkills.LoadAnalysisSkills()

	// Build system messages for Phase 1: jira skill + analysis skills
	var phaseOneSystem []llms.MessageContent
	phaseOneSystem = append(phaseOneSystem, jiraSkill)
	phaseOneSystem = append(phaseOneSystem, analysisSkills...)

	// --- Build model ---
	var model models.Model
	if strings.HasPrefix(*modelFlag, "claude-") {
		model = models.Claude{
			Model:     *modelFlag,
			ProjectID: vertexProjectID,
			Region:    region,
		}
	} else {
		model = models.Gemini{
			Model:     *modelFlag,
			ProjectID: vertexProjectID,
			Region:    region,
		}
	}

	// --- Phase 1: Analysis ---
	fmt.Fprintf(os.Stderr, "\n[main] === Phase 1: Analysis (model=%s, tools=%d) ===\n", *modelFlag, len(allTools))

	analysisResult, analysisErr := model.ModelRun(timeoutCtx, models.RunConfig{
		Prompt:         *questionFlag,
		Tools:          allTools,
		MCP:            mcpManager,
		SystemMessages: phaseOneSystem,
		MaxIterations:  defaultMaxIter,
	})
	if analysisErr != nil {
		return fmt.Errorf("phase 1 analysis: %w", analysisErr)
	}

	fmt.Fprintf(os.Stderr, "\n[main] analysis complete (%d bytes)\n", len(analysisResult))

	// --- Dry-run: stop after analysis ---
	if *dryRunFlag {
		fmt.Fprintf(os.Stderr, "[main] dry-run mode: skipping Jira triage\n")
		return nil
	}

	// --- Check MCP availability for Phase 2 ---
	if !mcpAvailable {
		fmt.Fprintf(os.Stderr, "[main] MCP unavailable: cannot run Jira triage\n")
		return fmt.Errorf("MCP unavailable: cannot create Jira issues")
	}

	// --- Check creation cap ---
	if !dedupCache.CanCreateIssue() {
		fmt.Fprintf(os.Stderr, "[main] issue creation cap reached, skipping Jira triage\n")
		return nil
	}

	// --- Phase 2: Jira Triage ---
	fmt.Fprintf(os.Stderr, "\n[main] === Phase 2: Jira Triage ===\n")

	triagePrompt := skills.OpenJiraIssue(analysisResult, *questionFlag, "AlertManager")

	// Phase 2 only gets Jira skill as system message
	phaseTwoSystem := []llms.MessageContent{jiraSkill}

	triageResult, triageErr := model.ModelRun(timeoutCtx, models.RunConfig{
		Prompt:         triagePrompt,
		Tools:          allTools,
		MCP:            mcpManager,
		SystemMessages: phaseTwoSystem,
		MaxIterations:  defaultMaxIter,
	})
	if triageErr != nil {
		return fmt.Errorf("phase 2 jira triage: %w", triageErr)
	}

	fmt.Fprintf(os.Stderr, "\n[main] triage complete (%d bytes)\n", len(triageResult))
	return nil
}

// runCheckOnly performs a lightweight alert check without invoking the LLM.
// It prints a JSON object with alerts_found and count, then exits.
func runCheckOnly(amClient *alertmanager.Client) error {
	checkCtx, checkCancel := context.WithTimeout(context.Background(), checkOnlyTimeout)
	defer checkCancel()

	found, count, checkErr := amClient.CheckAlerts(checkCtx)
	if checkErr != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", checkErr)
		os.Exit(1)
	}

	output := map[string]any{"alerts_found": found, "count": count}
	encoded, _ := json.Marshal(output)
	fmt.Println(string(encoded))
	return nil
}
