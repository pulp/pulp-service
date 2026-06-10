package skills

import (
	_ "embed"
	"fmt"
	"os"

	"github.com/tmc/langchaingo/llms"
)

//go:embed pulp-sops.md
var pulpSOPs string

//go:embed pulp-alert-rules.md
var pulpAlertRules string

//go:embed prometheus-metrics.md
var prometheusMetrics string

//go:embed diagnostic-routing.md
var diagnosticRouting string

func LoadAnalysisSkills() []llms.MessageContent {
	combined := pulpSOPs + "\n\n" + pulpAlertRules + "\n\n" + prometheusMetrics + "\n\n" + diagnosticRouting
	totalBytes := len(combined)
	fmt.Fprintf(os.Stderr, "[skills] loaded 4 skill files (%d bytes)\n", totalBytes)
	if totalBytes > 60000 {
		fmt.Fprintf(os.Stderr, "[skills] WARNING: combined skill content exceeds 60K chars (%d bytes)\n", totalBytes)
	}

	return []llms.MessageContent{
		llms.TextParts(llms.ChatMessageTypeSystem, pulpSOPs),
		llms.TextParts(llms.ChatMessageTypeSystem, pulpAlertRules),
		llms.TextParts(llms.ChatMessageTypeSystem, prometheusMetrics),
		llms.TextParts(llms.ChatMessageTypeSystem, diagnosticRouting),
	}
}
