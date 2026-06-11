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
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/cloudwatch"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/dedup"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/glitchtip"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/jira"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/kubernetes"
	"github.com/pulp/pulp-service/tools/agents/agent-alertmanager/prometheus"
	localSkills "github.com/pulp/pulp-service/tools/agents/agent-alertmanager/skills"
	mcpClient "github.com/pulp/pulp-service/tools/agents/shared/mcp-client"
	"github.com/pulp/pulp-service/tools/agents/shared/models"
	"github.com/pulp/pulp-service/tools/agents/shared/skills"
)

const (
	defaultModel     = "claude-sonnet-4-6"
	defaultQuestion  = "analyze all firing Pulp alerts and assess their severity and impact"
	defaultRegion    = "us-east5"
	defaultCooldown  = "30m"
	defaultCachePath = "/tmp/agent-alertmanager-cache.json"
	defaultLockPath  = "/tmp/agent-alertmanager.lock"
	defaultMaxIter   = 20
	defaultTimeout   = 25 * time.Minute
	perClusterCheckTimeout = 10 * time.Second
	perClusterFetchTimeout = 2 * time.Minute
)

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

	// --- Parse cluster configuration ---
	clusterConfigs, configErr := parseClusterConfigs()
	if configErr != nil {
		return configErr
	}
	fmt.Fprintf(os.Stderr, "[main] configured %d cluster(s): ", len(clusterConfigs))
	for idx, cfg := range clusterConfigs {
		if idx > 0 {
			fmt.Fprint(os.Stderr, ", ")
		}
		fmt.Fprint(os.Stderr, cfg.Name)
	}
	fmt.Fprintln(os.Stderr)

	var clients []*alertmanager.Client
	for _, cfg := range clusterConfigs {
		clients = append(clients, alertmanager.NewClient(cfg.Name, cfg.URL, cfg.Token, cfg.InsecureSkipVerify))
	}

	// --- Check-only mode ---
	if *checkOnlyFlag {
		return runCheckOnly(clients)
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
		fmt.Fprintf(os.Stderr, "[lock] another instance is running, skipping this run\n")
		skipOutput, _ := json.Marshal(map[string]any{"skipped": true, "reason": "lock_held"})
		fmt.Println(string(skipOutput))
		return nil
	}
	defer unlock()

	runStart := time.Now()

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

	// --- Tool manager (native tools, no external MCP server needed) ---
	mcpManager := mcpClient.NewMCPManager()

	// --- Jira (native REST client, same pattern as agent-splunk) ---
	// TODO: refactor jira package into tools/agents/shared/jira so both
	// agent-alertmanager and agent-splunk share a single Jira client.
	jiraAvailable := false
	var jiraTools []llms.Tool
	if jiraURL := os.Getenv("JIRA_URL"); jiraURL != "" {
		jiraCredential := os.Getenv("JIRA_API_TOKEN")
		if jiraCredential == "" {
			fmt.Fprintf(os.Stderr, "[main] warning: JIRA_URL set but JIRA_API_TOKEN missing\n")
		} else {
			jiraUsername, jiraToken, found := strings.Cut(jiraCredential, ":")
			if !found || jiraUsername == "" || jiraToken == "" {
				fmt.Fprintf(os.Stderr, "[main] warning: JIRA_API_TOKEN must be username:token\n")
			} else {
				jiraClient := jira.NewClient(jiraURL, jiraUsername, jiraToken)
				jiraTools = jiraClient.RegisterTools(mcpManager)
				fmt.Fprintf(os.Stderr, "[jira] registered %d jira tools (%s)\n", len(jiraTools), jiraURL)
				jiraAvailable = true
			}
		}
	} else {
		fmt.Fprintf(os.Stderr, "[main] warning: JIRA_URL not set, continuing without Jira\n")
	}

	var allTools []llms.Tool

	// --- Register native tools ---
	alertTool := alertmanager.RegisterTool(clients, mcpManager)
	allTools = append(allTools, alertTool)
	allTools = append(allTools, jiraTools...)

	// --- Register diagnostic tools (conditional on config/credentials) ---
	var k8sClients []*kubernetes.Client
	for _, cfg := range clusterConfigs {
		if cfg.APIServerURL != "" {
			k8sClients = append(k8sClients, kubernetes.NewClient(cfg.Name, cfg.APIServerURL, cfg.Token, cfg.Namespace))
		}
	}
	if len(k8sClients) > 0 {
		allTools = append(allTools, kubernetes.RegisterTool(k8sClients, mcpManager))
	}

	var promClients []*prometheus.Client
	for _, cfg := range clusterConfigs {
		if cfg.PrometheusURL != "" {
			promClients = append(promClients, prometheus.NewClient(cfg.Name, cfg.PrometheusURL, cfg.Token, cfg.Namespace))
		}
	}
	if len(promClients) > 0 {
		allTools = append(allTools, prometheus.RegisterTool(promClients, mcpManager))
	}

	glitchtipClient, glitchtipErr := glitchtip.NewClientFromEnv()
	if glitchtipErr != nil {
		fmt.Fprintf(os.Stderr, "[main] GlitchTip not configured: %v\n", glitchtipErr)
	} else {
		allTools = append(allTools, glitchtip.RegisterTool(glitchtipClient, mcpManager))
	}

	cloudwatchClient, cwErr := cloudwatch.NewClientFromEnv(timeoutCtx)
	if cwErr != nil {
		fmt.Fprintf(os.Stderr, "[main] CloudWatch not configured: %v\n", cwErr)
	} else {
		allTools = append(allTools, cloudwatch.RegisterTool(cloudwatchClient, mcpManager))
	}

	// --- Multi-cluster pre-flight fetch + dedup (Seen only, no Record) ---
	type clusterFetchResult struct {
		clusterName string
		status      string
		alerts      []alertmanager.Alert
		fetchErr    string
	}

	var fetchResults []clusterFetchResult
	var allNewAlerts []alertmanager.Alert
	alertsFoundPerCluster := make(map[string]int)
	alertsProcessedPerCluster := make(map[string]int)
	clustersHealthy := 0
	clustersErrored := 0

	for _, client := range clients {
		fetchCtx, fetchCancel := context.WithTimeout(timeoutCtx, perClusterFetchTimeout)
		alerts, fetchErr := client.FetchAlerts(fetchCtx)
		fetchCancel()

		if fetchErr != nil {
			fmt.Fprintf(os.Stderr, "[main] cluster %s: fetch failed: %v\n", client.Name(), fetchErr)
			fetchResults = append(fetchResults, clusterFetchResult{
				clusterName: client.Name(),
				status:      "error",
				fetchErr:    fetchErr.Error(),
			})
			clustersErrored++
			continue
		}

		alertsFoundPerCluster[client.Name()] = len(alerts)
		clustersHealthy++

		var newAlerts []alertmanager.Alert
		for _, alert := range alerts {
			if !dedupCache.Seen(client.Name(), alert.Fingerprint, alert.StartsAt) {
				newAlerts = append(newAlerts, alert)
			}
		}
		alertsProcessedPerCluster[client.Name()] = len(newAlerts)
		allNewAlerts = append(allNewAlerts, newAlerts...)

		fetchResults = append(fetchResults, clusterFetchResult{
			clusterName: client.Name(),
			status:      "ok",
			alerts:      newAlerts,
		})
	}

	if clustersHealthy == 0 {
		return fmt.Errorf("all %d clusters failed to fetch alerts", len(clients))
	}

	if len(allNewAlerts) == 0 {
		fmt.Fprintf(os.Stderr, "[main] no new alerts across %d clusters, skipping run\n", clustersHealthy)
		return nil
	}

	fmt.Fprintf(os.Stderr, "[main] %d new alerts to process across %d clusters\n", len(allNewAlerts), clustersHealthy)

	// --- Load skills ---
	jiraSkill := skills.LoadSkill()
	analysisSkills := localSkills.LoadAnalysisSkills()

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

	// Record all processed alerts in dedup cache after successful analysis
	for _, alert := range allNewAlerts {
		dedupCache.Record(alert.Cluster, alert.Fingerprint, alert.StartsAt)
	}

	// --- Dry-run: stop after analysis ---
	if *dryRunFlag {
		fmt.Println(analysisResult)
		fmt.Fprintf(os.Stderr, "[main] dry-run mode: skipping Jira triage\n")
		return nil
	}

	// --- Check Jira availability for Phase 2 ---
	if !jiraAvailable {
		fmt.Fprintf(os.Stderr, "[main] Jira not configured: cannot run triage\n")
		return fmt.Errorf("Jira not configured: set JIRA_URL and JIRA_API_TOKEN")
	}

	// --- Check creation cap ---
	if !dedupCache.CanCreateIssue() {
		fmt.Fprintf(os.Stderr, "[main] issue creation cap reached, skipping Jira triage\n")
		return nil
	}

	// --- Phase 2: Jira Triage ---
	fmt.Fprintf(os.Stderr, "\n[main] === Phase 2: Jira Triage ===\n")

	triagePrompt := skills.OpenJiraIssue(analysisResult, *questionFlag, "Alertmanager")

	phaseTwoSystem := []llms.MessageContent{jiraSkill}

	diagnosticToolNames := map[string]bool{
		"alertmanager_get_alerts": true,
		"k8s_get_pod_info":       true,
		"prometheus_query":       true,
		"glitchtip_get_errors":   true,
		"cloudwatch_query_logs":  true,
	}
	var jiraOnlyTools []llms.Tool
	for _, tool := range allTools {
		if !diagnosticToolNames[tool.Function.Name] {
			jiraOnlyTools = append(jiraOnlyTools, tool)
		}
	}

	triageResult, triageErr := model.ModelRun(timeoutCtx, models.RunConfig{
		Prompt:         triagePrompt,
		Tools:          jiraOnlyTools,
		MCP:            mcpManager,
		SystemMessages: phaseTwoSystem,
		MaxIterations:  defaultMaxIter,
	})
	if triageErr != nil {
		return fmt.Errorf("phase 2 jira triage: %w", triageErr)
	}

	fmt.Fprintf(os.Stderr, "\n[main] triage complete (%d bytes)\n", len(triageResult))

	dedupCache.RecordIssueCreation()
	issuesChecked := 1

	// --- Structured run summary with per-cluster status ---
	type clusterSummary struct {
		Status          string `json:"status"`
		Count           int    `json:"count,omitempty"`
		AlertsProcessed int    `json:"alerts_processed,omitempty"`
		Error           string `json:"error,omitempty"`
	}

	clusterSummaries := make(map[string]clusterSummary)
	for _, result := range fetchResults {
		if result.status == "error" {
			clusterSummaries[result.clusterName] = clusterSummary{Status: "error", Error: result.fetchErr}
		} else {
			clusterSummaries[result.clusterName] = clusterSummary{
				Status:          "ok",
				Count:           alertsFoundPerCluster[result.clusterName],
				AlertsProcessed: alertsProcessedPerCluster[result.clusterName],
			}
		}
	}

	totalFound := 0
	for _, count := range alertsFoundPerCluster {
		totalFound += count
	}

	runSummary := map[string]any{
		"alerts_found":     totalFound > 0,
		"count":            totalFound,
		"alerts_processed": len(allNewAlerts),
		"issues_checked":   issuesChecked,
		"run_duration_sec": time.Since(runStart).Seconds(),
		"clusters":         clusterSummaries,
		"clusters_healthy": clustersHealthy,
		"clusters_errored": clustersErrored,
	}
	summaryJSON, _ := json.Marshal(runSummary)
	fmt.Fprintf(os.Stderr, "[summary] %s\n", string(summaryJSON))

	return nil
}

func runCheckOnly(clients []*alertmanager.Client) error {
	type clusterStatus struct {
		Status string `json:"status"`
		Count  int    `json:"count,omitempty"`
		Error  string `json:"error,omitempty"`
	}

	overallTimeout := time.Duration(len(clients))*perClusterCheckTimeout + 5*time.Second
	overallCtx, overallCancel := context.WithTimeout(context.Background(), overallTimeout)
	defer overallCancel()

	clusters := make(map[string]clusterStatus)
	totalCount := 0
	anyFound := false
	clustersHealthy := 0
	clustersErrored := 0

	for _, client := range clients {
		clusterCtx, clusterCancel := context.WithTimeout(overallCtx, perClusterCheckTimeout)
		found, count, checkErr := client.CheckAlerts(clusterCtx)
		clusterCancel()

		if checkErr != nil {
			clusters[client.Name()] = clusterStatus{Status: "error", Error: checkErr.Error()}
			clustersErrored++
			continue
		}

		clusters[client.Name()] = clusterStatus{Status: "ok", Count: count}
		clustersHealthy++
		totalCount += count
		if found {
			anyFound = true
		}
	}

	output := map[string]any{
		"alerts_found":     anyFound,
		"count":            totalCount,
		"clusters":         clusters,
		"clusters_healthy": clustersHealthy,
		"clusters_errored": clustersErrored,
	}
	encoded, _ := json.Marshal(output)
	fmt.Println(string(encoded))

	if clustersHealthy == 0 {
		return fmt.Errorf("all %d clusters failed health check", len(clients))
	}
	return nil
}
