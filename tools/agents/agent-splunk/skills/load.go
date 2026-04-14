package skills

import (
	"fmt"
	"os"

	"github.com/tmc/langchaingo/llms"
)

const JIRA_STRUCTURE_SKILL_FILE = "./skills/jira-workflows.md"

// LoadSkill reads the jira-workflows.md file and returns its content as an llms.MessageContent
// that can be prepended to the LLM conversation history.
func LoadSkill() (llms.MessageContent, error) {
	content, err := os.ReadFile(JIRA_STRUCTURE_SKILL_FILE)
	if err != nil {
		return llms.MessageContent{}, fmt.Errorf("reading skill file: %w", err)
	}

	return llms.TextParts(llms.ChatMessageTypeSystem, string(content)), nil
}
