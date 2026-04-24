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

func LoadAnalysisSkills() []llms.MessageContent {
	combined := pulpSOPs + "\n\n" + pulpAlertRules
	totalBytes := len(combined)
	fmt.Fprintf(os.Stderr, "[skills] loaded pulp-sops.md + pulp-alert-rules.md (%d bytes)\n", totalBytes)
	if totalBytes > 40000 {
		fmt.Fprintf(os.Stderr, "[skills] WARNING: combined skill content exceeds 40K chars (%d bytes)\n", totalBytes)
	}

	return []llms.MessageContent{
		llms.TextParts(llms.ChatMessageTypeSystem, pulpSOPs),
		llms.TextParts(llms.ChatMessageTypeSystem, pulpAlertRules),
	}
}
