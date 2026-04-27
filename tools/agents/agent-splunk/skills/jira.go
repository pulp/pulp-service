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
// Issues are grouped by three dimensions: Pulp service/origin, HTTP method, and
// endpoint similarity. All three must match for an existing issue to be considered
// a duplicate or related.
func OpenJiraIssue(result, question string) string {
	fmt.Fprintf(os.Stderr, "\n[jira] checking for existing issues before creating a new one...\n")
	return fmt.Sprintf(
		"Based on the following analysis, you need to create a Jira issue or add a comment to an existing one — but first check the preconditions and existing issues.\n\n"+
			"Step 0: Check the analysis for Splunk failures.\n"+
			"  If the analysis indicates that Splunk logs could not be retrieved or that Splunk was inaccessible (e.g. connection errors, authentication failures,\n"+
			"  timeouts, or any indication that log data is missing due to Splunk unavailability), do NOT create or update any Jira issue.\n"+
			"  Instead, report that the analysis is incomplete due to Splunk access failure and stop.\n\n"+
			"Step 1: Extract request attributes from the analysis.\n"+
			"  Identify the following from the analysis:\n"+
			"  a) The Pulp service/origin handling the request: pulp-api, pulp-content, or pulp-worker.\n"+
			"     These are fundamentally different services. For example, pulp-api handles REST API\n"+
			"     operations (POST /modify/, PUT /api/...) while pulp-content-app serves content downloads (GET requests).\n"+
			"  b) The HTTP method(s) involved (GET, POST, PUT, PATCH, DELETE, etc.).\n"+
			"  c) The target domain(s) and endpoint path(s) (e.g. \"packages.redhat.com/api/pulp/...\").\n\n"+
			"  HARD GROUPING RULES — an existing issue is only a match if ALL of these align:\n"+
			"  1. SAME Pulp service/origin (pulp-api vs pulp-content vs pulp-worker are NEVER grouped together).\n"+
			"  2. SAME HTTP method (GET vs POST vs PUT etc. are NEVER grouped together, even on the same endpoint).\n"+
			"  3. Same or similar endpoint path (same base path differing only in IDs, query params, or trailing segments).\n\n"+
			"  If ANY of these three dimensions differ, the issues are SEPARATE — do not group, comment, or treat them as related.\n\n"+
			"  Examples of SEPARATE issues (must NOT be grouped):\n"+
			"  - POST /modify/ on pulp-api  vs  GET /pulp-content/ on pulp-content app  (different service AND method)\n"+
			"  - GET /api/pulp/ on pulp-api  vs  GET /api/pulp-content/ on pulp-content-app  (different service)\n"+
			"  - POST /api/pulp/repos/  vs  GET /api/pulp/repos/ on the same service  (different method)\n\n"+
			"  Examples of issues that SHOULD be grouped:\n"+
			"  - GET /content/copr/  and  GET /content/rhoai/  on pulp-content app  (same service, same method, similar paths)\n"+
			"  - POST /api/pulp/repos/rpm/1/modify/  and  POST /api/pulp/repos/rpm/2/modify/ on pulp-api  (same service, same method, same base path)\n\n"+
			"Step 2: Search for existing issues per service+method+endpoint group.\n"+
			"  For each distinct service+method group identified in Step 1:\n"+
			"  a) Use the jira_search tool to search for existing open issues that match the service/origin,\n"+
			"     HTTP method, AND endpoint pattern from the analysis. Include both the service name and HTTP method\n"+
			"     in your search keywords (e.g. search for \"pulp-content-app GET 504\" not just \"pulp 504\").\n"+
			"  b) Filter for open/unresolved issues.\n\n"+
			"Step 3: Review the search results (apply per service+method+endpoint group).\n"+
			"  CRITICAL: Before evaluating ANY search result, verify that the existing issue matches on ALL THREE\n"+
			"  dimensions: same Pulp service/origin, same HTTP method, and same/similar endpoint. If ANY dimension\n"+
			"  differs, SKIP that issue entirely — it is NOT a match, NOT \"similar but different\", and must NOT\n"+
			"  receive a comment. Proceed as if no matching issue was found.\n\n"+
			"  - If an existing open issue matches all three dimensions exactly, do NOT create a new issue or comment.\n"+
			"    Instead, report the existing issue key and summary.\n"+
			"  - If an existing open issue matches the service and method but has a SIMILAR (not identical) endpoint\n"+
			"    (e.g. same base path, same domain, similar error pattern but different specific path segments or parameters):\n"+
			"    a) First, use the jira_get_issue tool to retrieve the full issue details, including its labels.\n"+
			"       Check the \"labels\" field of the issue. If the labels list contains \"triaged\", do NOT add a comment or modify the issue in any way.\n"+
			"       Instead, report the existing issue key and note that it has been triaged, then STOP — do not proceed to steps b through e.\n"+
			"    b) Use the jira_get_comments tool to retrieve all existing comments on that issue.\n"+
			"    c) Check whether any existing comment already describes the same observations from this analysis.\n"+
			"    d) If NO existing comment covers these observations, use the jira_add_comment tool to add a comment summarizing the new findings from the analysis,\n"+
			"       noting how they relate to (but differ from) the original issue description.\n"+
			"       If the comment contains sensitive information (see Step 4), set its visibility to the group \"Red Hat Employee\" (use visibility type \"group\", not \"role\") so it is not publicly visible.\n"+
			"    e) If a comment with substantially the same content already exists, do NOT add a duplicate. Report the existing issue key and the existing comment.\n"+
			"  - If no matching open issue is found for this service+method+endpoint group, create a new Jira issue using the jira_create_issue tool.\n"+
			"    Include the Pulp service and HTTP method prominently in the issue summary\n"+
			"    (e.g. \"[pulp-content-app] [GET] 504 timeouts on content serving endpoints\").\n"+
			"    Use the analysis to derive an appropriate description, grouping all similar endpoints for this service+method together.\n"+
			"    If the issue contains sensitive information (see Step 4), set the security level to make it visible only to the group \"Red Hat Employee\" (use visibility type \"group\", not \"role\").\n\n"+
			"Step 4: Sensitive information check.\n"+
			"  Before creating or commenting on an issue, inspect the content for sensitive information such as:\n"+
			"  IP addresses, hostnames, internal URLs, credentials, tokens, API keys, usernames, or any infrastructure-specific details.\n"+
			"  If ANY sensitive information is present, the issue or comment MUST be created as private (visible only to the group \"Red Hat Employee\"; always use visibility type \"group\", not \"role\").\n\n"+
			"--- Analysis ---\n%s\n--- End of Analysis ---\n\n"+
			"Original user question: %s",
		result, question,
	)
}
