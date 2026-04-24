package skills

import (
	"fmt"
	"os"
)

// OpenJiraIssue returns a formated prompt that will create a new issue based on
// "result" (the outputs from another prompt interaction).
// It also verifies if another issue for the same problem is already opened before
// creating it, and adds a comment to an existing issue if the analysis describes
// similar but not identical behavior.
func OpenJiraIssue(result, question, dataSource string) string {
	fmt.Fprintf(os.Stderr, "\n[jira] checking for existing issues before creating a new one...\n")
	return fmt.Sprintf(
		"Based on the following analysis, you need to create a Jira issue or add a comment to an existing one — but first check the preconditions and existing issues.\n\n"+
			"Step 0: Check the analysis for %s failures.\n"+
			"  If the analysis indicates that %s logs could not be retrieved or that %s was inaccessible (e.g. connection errors, authentication failures,\n"+
			"  timeouts, or any indication that log data is missing due to %s unavailability), do NOT create or update any Jira issue.\n"+
			"  Instead, report that the analysis is incomplete due to %s access failure and stop.\n\n"+
			"Step 1: Use the jira_search tool to search for existing open issues that match the problem described in the analysis.\n"+
			"  Search using relevant keywords from the analysis summary. Filter for open/unresolved issues.\n\n"+
			"Step 2: Review the search results.\n"+
			"  - If an existing open issue already covers the EXACT same problem, do NOT create a new issue or comment.\n"+
			"    Instead, report the existing issue key and summary.\n"+
			"  - If an existing open issue covers a SIMILAR but not identical problem (e.g. same component or area but different symptoms, error messages, or conditions):\n"+
			"    a) First, use the jira_get_issue tool to retrieve the full issue details, including its labels.\n"+
			"       Check the \"labels\" field of the issue. If the labels list contains \"triaged\", do NOT add a comment or modify the issue in any way.\n"+
			"       Instead, report the existing issue key and note that it has been triaged, then STOP — do not proceed to steps b through e.\n"+
			"    b) Use the jira_get_comments tool to retrieve all existing comments on that issue.\n"+
			"    c) Check whether any existing comment already describes the same observations from this analysis.\n"+
			"    d) If NO existing comment covers these observations, use the jira_add_comment tool to add a comment summarizing the new findings from the analysis,\n"+
			"       noting how they relate to (but differ from) the original issue description.\n"+
			"       If the comment contains sensitive information (see Step 3), set its visibility to the group \"Red Hat Employee\" (use visibility type \"group\", not \"role\") so it is not publicly visible.\n"+
			"    e) If a comment with substantially the same content already exists, do NOT add a duplicate. Report the existing issue key and the existing comment.\n"+
			"  - If no matching open issue is found, create a new Jira issue using the jira_create_issue tool.\n"+
			"    Use the analysis to derive an appropriate summary and description.\n"+
			"    If the issue contains sensitive information (see Step 3), set the security level to make it visible only to the group \"Red Hat Employee\" (use visibility type \"group\", not \"role\").\n\n"+
			"Step 3: Sensitive information check.\n"+
			"  Before creating or commenting on an issue, inspect the content for sensitive information such as:\n"+
			"  IP addresses, hostnames, internal URLs, credentials, tokens, API keys, usernames, or any infrastructure-specific details.\n"+
			"  If ANY sensitive information is present, the issue or comment MUST be created as private (visible only to the group \"Red Hat Employee\"; always use visibility type \"group\", not \"role\").\n\n"+
			"--- Analysis ---\n%s\n--- End of Analysis ---\n\n"+
			"Original user question: %s",
		dataSource, dataSource, dataSource, dataSource, dataSource, result, question,
	)
}
