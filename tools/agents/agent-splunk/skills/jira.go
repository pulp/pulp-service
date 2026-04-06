package skills

import (
	"fmt"
	"os"
)

// OpenJiraIssue returns a formated prompt that will create a new issue based on
// "result" (the outputs from another prompt interaction).
// It also verifies if another issue for the same problem is already opened before creating it.
func OpenJiraIssue(result, question string) string {
	fmt.Fprintf(os.Stderr, "\n[jira] checking for existing issues before creating a new one...\n")
	return fmt.Sprintf(
		"Based on the following analysis, you need to DRAFT a Jira issue (DONT CREATE IT) — but first check if one already exists.\n\n"+
			"Step 1: Use the jira_search tool to search for existing open issues that match the problem described in the analysis.\n"+
			"  Search using relevant keywords from the analysis summary. Filter for open/unresolved issues.\n\n"+
			"Step 2: Review the search results.\n"+
			"  - If an existing open issue already covers the same problem, do NOT create a new issue.\n"+
			"    Instead, report the existing issue key and summary.\n"+
			"  - If no matching open issue is found, based on the following analysis, draft a Jira issue using the jira_create_issue tool.\n"+
			"    Use the analysis to derive an appropriate summary and description.\n\n"+
			"--- Analysis ---\n%s\n--- End of Analysis ---\n\n"+
			"Original user question: %s",
		result, question,
	)
}
