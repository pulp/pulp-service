package skills

import (
	_ "embed"

	"github.com/tmc/langchaingo/llms"
)

//go:embed jira-workflows.md
var jiraWorkflowsSkill string

// LoadSkill returns the embedded jira-workflows.md content as an llms.MessageContent
// that can be prepended to the LLM conversation history.
func LoadSkill() llms.MessageContent {
	return llms.TextParts(llms.ChatMessageTypeSystem, jiraWorkflowsSkill)
}
