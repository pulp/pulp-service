package skills

import (
	_ "embed"

	"github.com/tmc/langchaingo/llms"
)

//go:embed jira-workflows.md
var jiraWorkflowsSkill string

//go:embed trace-requests.md
var traceRequestsSkill string

// LoadSkill returns the embedded jira-workflows.md content as an llms.MessageContent
// that can be prepended to the LLM conversation history.
func LoadSkill() llms.MessageContent {
	return llms.TextParts(llms.ChatMessageTypeSystem, jiraWorkflowsSkill)
}

// LoadTraceSkill returns the embedded trace-requests.md content, which teaches
// the model how to follow a request from Akamai through the NSGW into Pulp and
// decide which hop emitted the error.
func LoadTraceSkill() llms.MessageContent {
	// Leading newlines: langchaingo concatenates system messages without a separator.
	return llms.TextParts(llms.ChatMessageTypeSystem, "\n\n"+traceRequestsSkill)
}
