package skills

import (
	"fmt"
	"os"
)

// OpenJiraIssue returns a formated prompt that will create a new issue based on
// "result" (the outputs from another prompt interaction).
// It also verifies if another issue for the same problem is already opened before
// creating it, and adds a comment to an existing issue only when the analysis
// describes the same problem with new evidence.
// Issues are keyed by four dimensions: error origin, HTTP method, HTTP status code,
// and normalized endpoint path. All four must match exactly for an existing issue to
// be considered the same problem. Errors of the same class (e.g. 500) on different
// paths are distinct problems and get distinct issues.
func OpenJiraIssue(result, question string) string {
	fmt.Fprintf(os.Stderr, "\n[jira] checking for existing issues before creating a new one...\n")
	return fmt.Sprintf(
		"Based on the following analysis, you need to create a Jira issue or add a comment to an existing one — but first check the preconditions and existing issues.\n\n"+
			"Step 0: Check the analysis for Akamai log (Sumo Logic) failures.\n"+
			"  If the analysis indicates that the Akamai logs could not be retrieved from Sumo Logic or that Sumo Logic was inaccessible\n"+
			"  (e.g. connection errors, authentication failures, timeouts, query errors, or any indication that log data is missing\n"+
			"  due to Sumo Logic unavailability), do NOT create or update any Jira issue.\n"+
			"  Instead, report that the analysis is incomplete due to Sumo Logic access failure and stop.\n\n"+
			"Step 1: Build an issue key for every error group in the analysis.\n"+
			"  For each error group, extract these four attributes. Together they form the issue key:\n"+
			"  a) The error origin, i.e. the hop that emitted the error, as determined by the request trace in the analysis:\n"+
			"     pulp-api, pulp-content, nsgw (north-south gateway), akamai-edge, or unknown. pulp-worker is never an origin; if the\n"+
			"     analysis lists it as a contributing cause, mention it in the issue but key by the error origin.\n"+
			"     If the analysis reports the origin as unknown (trace unavailable), the origin in the key is \"unknown\".\n"+
			"     Do NOT substitute the Pulp service the endpoint routes to: an unconfirmed origin never matches an issue\n"+
			"     with a confirmed origin. Mention the routed service in the description and say the origin was not confirmed.\n"+
			"  b) The HTTP method (GET, POST, PUT, PATCH, DELETE, etc.).\n"+
			"  c) The exact HTTP status code (500, 502, 503, 504, ...). Do not collapse codes into a class like 5xx.\n"+
			"  d) The normalized endpoint path, built from the request path with these rules and NO others:\n"+
			"     - Drop the scheme, host and query string.\n"+
			"     - Replace a path segment with a placeholder ONLY if it is a generated identifier:\n"+
			"       numeric id -> <id>, UUID -> <uuid>, hex digest or sha256:... -> <digest>.\n"+
			"     - For content download paths (/api/pulp-content/<domain>/<base_path>/...), keep the domain and the\n"+
			"       distribution base path literal and replace the remaining file path with <file>.\n"+
			"     - Every other segment stays literal: domain names, distribution base paths, plugin and resource names\n"+
			"       (rpm, container, python, repositories, distributions, modify, ...) and action names.\n"+
			"     Examples:\n"+
			"       /api/pulp/acme/api/v3/repositories/rpm/rpm/0199a1b2-...-c3d4/modify/  ->  /api/pulp/acme/api/v3/repositories/rpm/rpm/<uuid>/modify/\n"+
			"       /api/pulp-content/copr/fedora-41/Packages/f/foo-1.0.rpm  ->  /api/pulp-content/copr/fedora-41/<file>\n"+
			"       /v2/acme/myimage/blobs/sha256:ab12...  ->  /v2/acme/myimage/blobs/<digest>\n"+
			"  Also note the Pulp error signature from the analysis if there is one (exception type and message, or the\n"+
			"  Envoy flags/details for nsgw errors). It is not part of the key, but it is evidence for Step 3.\n\n"+
			"  HARD MATCHING RULES — an existing issue is the same problem ONLY if ALL FOUR key attributes are identical:\n"+
			"  1. SAME error origin (pulp-api vs pulp-content vs nsgw vs akamai-edge vs unknown are NEVER the same problem).\n"+
			"  2. SAME HTTP method (GET vs POST vs PUT etc. are NEVER the same problem, even on the same path).\n"+
			"  3. SAME HTTP status code (a 500 and a 504 on the same path are NEVER the same problem).\n"+
			"  4. SAME normalized endpoint path, compared segment by segment after normalization.\n"+
			"     A shared prefix or a \"similar looking\" path is NOT a match.\n\n"+
			"  Matching on error class alone (e.g. \"another 500 on pulp-api\") is WRONG. Errors of the same class on\n"+
			"  different paths usually have different root causes and MUST be tracked as separate issues.\n"+
			"  The issue key is the ONLY matching criterion. If all four key attributes match, it is the same issue, even\n"+
			"  when the error signature differs (a different exception type, or different Envoy flags). In that case add a\n"+
			"  comment that explicitly flags the new signature as new evidence; never create a second issue with the same key.\n\n"+
			"  Examples of SEPARATE problems (must NOT be matched, merged or commented on):\n"+
			"  - 500 POST /api/pulp/acme/api/v3/repositories/rpm/rpm/<uuid>/modify/  vs  500 POST /api/pulp/acme/api/v3/content/rpm/packages/upload/  (different path)\n"+
			"  - 500 GET /api/pulp-content/copr/<base_path>/<file>  vs  500 GET /api/pulp-content/rhoai/<base_path>/<file>  (different domain)\n"+
			"  - 500 GET /api/pulp-content/copr/fedora-41/<file>  vs  500 GET /api/pulp-content/copr/epel-9/<file>  (different distribution)\n"+
			"  - 500 GET /api/pulp/acme/api/v3/tasks/  vs  504 GET /api/pulp/acme/api/v3/tasks/  (different status code)\n"+
			"  - POST vs GET on /api/pulp/acme/api/v3/repositories/rpm/rpm/  (different method)\n"+
			"  - pulp-api vs nsgw emitting 503 for the same path  (different origin)\n\n"+
			"  Examples of the SAME problem (should be matched):\n"+
			"  - 500 POST /api/pulp/acme/api/v3/repositories/rpm/rpm/<uuid-1>/modify/  and  500 POST /api/pulp/acme/api/v3/repositories/rpm/rpm/<uuid-2>/modify/ on pulp-api\n"+
			"  - 504 GET /api/pulp-content/copr/fedora-41/Packages/a/a.rpm  and  504 GET /api/pulp-content/copr/fedora-41/Packages/b/b.rpm on pulp-content\n\n"+
			"Step 2: Search for existing issues per issue key.\n"+
			"  For each distinct issue key from Step 1:\n"+
			"  a) Use the jira_search tool to search for existing open issues. Include the origin, the method, the status\n"+
			"     code AND the most specific literal path segments in the search (e.g. text ~ \"pulp-api\" AND text ~ \"POST\"\n"+
			"     AND text ~ \"500\" AND text ~ \"modify\"). A search on class alone (e.g. \"pulp 500\") is not enough.\n"+
			"  b) Filter for open/unresolved issues.\n\n"+
			"Step 3: Review the search results (apply per issue key).\n"+
			"  CRITICAL: For each search result, read its summary and description and derive its issue key\n"+
			"  (origin, method, status code, normalized path). Compare it to the key from Step 1 using the HARD MATCHING RULES.\n"+
			"  If ANY attribute differs, SKIP that issue entirely — it is NOT a match and must NOT receive a comment.\n"+
			"  An older generic issue that names no specific path (e.g. \"500 errors on pulp-api\") is NOT a match for a\n"+
			"  specific path; create a new issue for the path and mention the generic issue key in its description.\n\n"+
			"  - If an existing open issue has the identical issue key:\n"+
			"    a) First, use the jira_get_issue tool to retrieve the full issue details, including its labels.\n"+
			"       Check the \"labels\" field of the issue. If the labels list contains \"triaged\", do NOT add a comment or modify the issue in any way.\n"+
			"       Instead, report the existing issue key and note that it has been triaged, then STOP — do not proceed to steps b through e.\n"+
			"    b) Use the jira_get_comments tool to retrieve all existing comments on that issue.\n"+
			"    c) Check whether the issue description or any existing comment already covers this occurrence\n"+
			"       (same time window, same sample requests or correlation ids, same evidence).\n"+
			"    d) If NOT covered, use the jira_add_comment tool to add a short comment recording the new occurrence:\n"+
			"       time range, count, one sample trace and correlation id, and any new evidence from the analysis.\n"+
			"       If the error signature differs from the one recorded in the issue, say so explicitly in the comment.\n"+
			"       If the comment contains sensitive information (see Step 4), set its visibility to the group \"Red Hat Employee\" (use visibility type \"group\", not \"role\") so it is not publicly visible.\n"+
			"    e) If it is already covered, do NOT add a duplicate. Report the existing issue key.\n"+
			"  - If no open issue has the identical issue key, create a new Jira issue using the jira_create_issue tool.\n"+
			"    The summary MUST contain all four key attributes in this format:\n"+
			"    \"[<origin>] [<METHOD>] [<status>] <normalized path>: <short description>\"\n"+
			"    (e.g. \"[pulp-content] [GET] [504] /api/pulp-content/copr/fedora-41/<file>: timeouts serving content\",\n"+
			"    \"[nsgw] [GET] [503] /api/pulp-content/rhoai/<base_path>/<file>: UH no healthy upstream\").\n"+
			"    If the normalized path is too long for the summary, keep the domain and the most specific literal\n"+
			"    segments and put the full normalized path in the description.\n"+
			"    Create ONE issue per issue key. Never fold several paths into one issue.\n"+
			"    The description MUST start with an \"Issue key\" section listing origin, method, status code and normalized path,\n"+
			"    followed by the confidence and the per-hop trace evidence from the analysis\n"+
			"    (Akamai, NSGW, and Pulp log lines with timestamps, Envoy flags/details, correlation id, and the Pulp error or traceback).\n"+
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
