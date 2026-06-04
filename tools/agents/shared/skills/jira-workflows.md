# PULP Jira Workflows

This document covers Jira configuration, workflows, and templates for the PULP project.

## Issue Number Links

When displaying any PULP issue number, always render it as a hyperlink:

```
[PULP-1234](https://redhat.atlassian.net/browse/PULP-1234)
```

This applies everywhere: responses, summaries, descriptions, and comments.

## MR Number Links

When displaying any MR number, always render it as a hyperlink to that MR. Use the appropriate base URL for the repo:

```
# pulp-docs
[!44](https://gitlab.cee.redhat.com/hosted-pulp/pulp-docs/-/merge_requests/44)

# akamai-packages.redhat.com
[!12](https://gitlab.cee.redhat.com/hosted-pulp/akamai-packages.redhat.com/-/merge_requests/12)

# app-interface
[!1234](https://gitlab.cee.redhat.com/service/app-interface/-/merge_requests/1234)

# pulp-service (GitHub PR)
[#456](https://github.com/pulp/pulp-service/pull/456)
```

This applies everywhere: responses, summaries, descriptions, and comments.


## Project Defaults

| Field | Default Value |
|-------|---------------|
| Project | `PULP` |
| Component | `services` (ID: 72219) |
| Board | Kanban (no sprints) |

**Key links:**
- [Kanban Board](https://redhat.atlassian.net/jira/software/c/projects/PULP/boards/6881)
- [Roadmap](https://redhat.atlassian.net/jira/plans/2860/scenarios/2859/timeline?vid=434&isPlanShare=true)
- [Metrics Dashboard](https://redhat.atlassian.net/plugins/servlet/ac/com.kretar.jira.plugin.user-story-map/eausm-link-retro?board.id=6881#!/agile-insights/board/6881?startDate=2026-01-01&endDate=2026-03-23)

Story points are not used anywhere in the PULP project if the  Component = 'services'.

## Custom Field IDs

| Field | Custom Field ID |
|-------|-----------------|
| Epic Name | `customfield_10011` |
| Target start | `customfield_10022` |
| Target end | `customfield_10023` |
| Blocked | `customfield_10517` |
| Blocked Reason | `customfield_10483` |

Note: The "Epic Link" field (`customfield_10014`) is deprecated in Jira Cloud. Child issues are linked to epics via the `parent` field instead.

**Date rule:** Target start and target end dates must always fall on a weekday (Monday–Friday). Never set them to a Saturday or Sunday.

## Kanban Board Statuses

| Status | Meaning |
|--------|---------|
| New | Initial status when created |
| Refinement | On the kanban board, ready for working |
| In Progress | Actively being worked on |
| Closed | Completed |

Setting status to **Refinement** is all that's required to add an issue to the kanban board.

### Typical Issue Lifecycle

The standard flow for a non-epic issue is:

**New → Refinement → In Progress → Closed**

- **New**: Issue is filed but not yet ready for work.
- **Refinement**: Issue is on the Kanban board, groomed, and ready to be picked up. Associates should always pick new work from issues in this state.
- **In Progress**: Issue is actively being worked on by an assigned associate.
- **Closed**: Work is complete.

**Variations:**
- Issues are sometimes filed directly as Refinement when they are immediately ready for the board.
- Occasionally an issue is taken directly to In Progress (skipping Refinement), but this should be rare.

When picking up new work, **assign the issue to yourself and transition it to In Progress**.


## Issue Type Classification

Before filing an issue, determine the type using these definitions:

| Type | Definition | 
|------|------------|
| **Feature** | A major new capability or "win" for the product | 
| **Epic** | High-level functionality or long-term initiative | 
| **Bug** | A software defect — functionality that is claimed but not working correctly | 
| **Task** | An individual work item, such as technical implementation, documentation, or configuration | 

**Decision rules:**
1. Is it a significant piece of work requiring a group of related items like Tasks and Bugs to be delivered?  → **Epic**
2. Is it a broken/missing behavior that was supposed to work? → **Bug**
3. Is it a specific small piece of work like technical implementation, documentation, or configuration? → **Task**
4. Do not use issue type **Story**
5. If unsure, **prompt the user** to clarify before filing.

Non-epic issues (Tasks, Bugs) are typically filed as sub-issues under an Epic. This is not required, but it is the norm — standalone issues without a parent epic are the exception.

## Epic Conventions

Epics follow different conventions from tasks and bugs:

- **Epics do not appear on the Kanban board.** They are managed separately and are never transitioned to Refinement.
- **Valid statuses for epics are: New, In Progress, and Closed.** Refinement is not used for epics.
- **Each epic is assigned to the lead responsible for that epic.**
- **When all sub-issues under an epic are closed, the epic itself should also be closed.**

## Creating an Epic

Epics use the `Epic` issue type.

```
jira_create_issue:
  project: PULP
  issue_type: Epic
  summary: "Epic title"
  description: "Epic description"
  priority: Major  # Blocker, Critical, Major, Normal, Minor
  epic_name: "Short epic name"  # Maps to customfield_10011
  components: [services]
```

### Epic Description Template

Based on the RHELBU-2715 format. Uses Jira Wiki Markup.

```
h2. Functionality Overview

[High-level description of what this epic delivers]

h2. Goals

* [Goal 1]
* [Goal 2]
* [Goal 3]

h2. Requirements

||requirement||Notes||isMvp||
|[Requirement 1]|[Notes]|yes/no|
|[Requirement 2]|[Notes]|yes/no|

h2. Use Cases

* [Use case 1 - who will use this and how]
* [Use case 2]

h2. Out of Scope

* [What is explicitly NOT included]

h2. Background, and strategic fit

[Why this epic matters strategically, alignment with product/company goals]

h2. Assumptions

* [Key assumption 1]
* [Key assumption 2]

h2. Customer Considerations

[How this impacts customers, migration paths, compatibility notes]

h2. Documentation Considerations

[What documentation needs to be created or updated]

h2. Questions

|Question|Outcome|
|[Open question 1]|[Answer when resolved]|

h1. *Stakeholders sign-offs*

||Name||Sign-off date||Comments||
|[Stakeholder 1]| | |
```

## Creating a Task Linked to an Epic

In Jira Cloud, child issues are linked to an epic via the `parent` field at creation time (the old Epic Link field is deprecated).

```
jira_create_issue:
  project: PULP
  issue_type: Task  # or Story, Bug
  summary: "Task title"
  description: "Task description"
  priority: Major
  components: [services]
  additional_fields: '{"parent": "PULP-1030"}'  # parent epic key
```

## Marking an Issue as Blocked

An issue can be marked as blocked using three complementary fields:

1. **Blocked** (`customfield_10517`) — a yes/no flag indicating the issue is currently blocked
2. **Blocked Reason** (`customfield_10483`) — a text explanation of why it is blocked
3. **Issue link** — a "Blocks" relationship linking this issue to the issue that is blocking it

All three should be set together for a complete blocked state.

### Setting blocked status and reason

```
jira_edit_issue:
  issue_key: PULP-1234
  additional_fields: '{
    "customfield_10517": {"id": "10852"},
    "customfield_10483": "Blocked pending resolution of PULP-5678."
  }'
```

To clear the blocked state:

```
jira_edit_issue:
  issue_key: PULP-1234
  additional_fields: '{
    "customfield_10517": {"id": "10853"},
    "customfield_10483": ""
  }'
```

| Value | Option ID |
|-------|-----------|
| True (blocked) | `10852` |
| False (not blocked) | `10853` |

### Creating the blocking issue link

Use `jira_create_issue_link` with type `"Blocks"`:

- `inwardIssue` = the issue **doing the blocking** (the blocker)
- `outwardIssue` = the issue **being blocked**

```
jira_create_issue_link:
  type: Blocks
  inwardIssue: PULP-5678   # this issue is blocking PULP-1234
  outwardIssue: PULP-1234  # this issue is blocked by PULP-5678
```

Reading the link: "PULP-1234 is blocked by PULP-5678."

### Removing a blocking link (when unblocked)

First, retrieve the link ID from the issue:

```
jira_get_issue:
  issue_key: PULP-1234
  fields: [issuelinks]
```

The response includes an `issuelinks` array. Each entry has an `id` field (e.g. `"1859698"`). Use that ID to delete the link:

```
jira_delete_issue_link:
  link_id: 1859698
```

Then clear the Blocked flag and Blocked Reason:

```
jira_edit_issue:
  issue_key: PULP-1234
  additional_fields: '{
    "customfield_10517": {"id": "10853"},
    "customfield_10483": ""
  }'
```

### Kanban board visibility

Blocked issues appear in a dedicated **Blocked** swimlane on the [PULP Kanban board](https://redhat.atlassian.net/jira/software/c/projects/PULP/boards/6881). This is the primary place to monitor blocked work — the swimlane is separate from the normal In Progress / Refinement columns, making blocked items immediately visible to the team.

## API / Automation Gotchas

When creating Jira issues via the API or MCP tools, certain fields are silently dropped at creation time and must be set in a follow-up edit call.

### `components` and `parent` are not persisted on create

Both the `components` field and the `parent` field (for linking a sub-issue to an epic) are silently ignored by the Jira Cloud REST API when passed at issue creation time. The issue is created successfully, but those fields are empty.

**Always follow up with an edit call after creation:**

```
Step 1: Create the issue
jira_create_issue:
  project: PULP
  issue_type: Task  # or Story, Bug, Epic — applies to all issue types
  summary: "Issue title"
  ...

Step 2: Set component and parent via edit (required)
jira_edit_issue:
  issue_key: PULP-1234
  components: [{"name": "services"}]
  additional_fields: '{"parent": {"key": "PULP-1030"}}'
```

This applies to all issue types: Tasks, Stories, Bugs, and Epics.

## Adding an Issue to the Kanban Board

```
Step 1: Get available transitions
jira_get_transitions:
  issue_key: PULP-1234

Step 2: Transition to Refinement
jira_transition_issue:
  issue_key: PULP-1234
  transition_id: <refinement_transition_id from step 1>
```

## Closing an Issue as Duplicate

When closing an issue as a duplicate, set the resolution to `Duplicate` during the transition and add a comment explaining which issue supersedes it.

```
Step 1: Get available transitions
jira_get_transitions:
  issue_key: PULP-1234

Step 2: Transition to Closed with Duplicate resolution
jira_transition_issue:
  issue_key: PULP-1234
  transition_id: <closed_transition_id from step 1>
  fields: {"resolution": {"name": "Duplicate"}}

Step 3: Comment with the superseding issue
jira_add_comment:
  issue_key: PULP-1234
  comment: "Closing as duplicate. See PULP-5678."
```

### Available resolution values

| Resolution | When to use |
|------------|-------------|
| Done | Work completed successfully |
| Won't Do | Decided not to do this work |
| Duplicate | Superseded by another issue |
| Cannot Reproduce | Bug that cannot be reproduced |
| Not a Bug | Reported as a bug but is expected behavior |

## Setting Target Dates on Epics

Target start and target end dates are set via custom fields on epics to track planned timelines on the [Roadmap](https://redhat.atlassian.net/jira/plans/2860/scenarios/2859/timeline?vid=434&isPlanShare=true).

**Date rule:** Target start and target end dates must always fall on a weekday (Monday–Friday). Never set them to a Saturday or Sunday.

```
jira_edit_issue:
  issue_key: PULP-1234
  fields:
    customfield_10022: "2026-04-07"   # Target start (YYYY-MM-DD, must be a weekday)
    customfield_10023: "2026-04-25"   # Target end (YYYY-MM-DD, must be a weekday)
```

These dates appear on the Jira Roadmap view and are used for planning visibility. They are typically set when an epic moves to In Progress or when planning upcoming work.

## Ticket Description Template

```
h3. Description
[Clear explanation of requirement or issue]

h3. Problem Statement
[What specific problem does this solve?]

h3. Acceptance Criteria
[Concrete, testable criteria]

h3. Additional Context
[Relevant background or links]
```

## Useful JQL Queries

**Active tickets assigned to you:**
```
(assignee = currentUser() OR reporter = currentUser())
AND status IN ('New', 'In Progress', 'Refinement')
ORDER BY priority DESC, updated DESC
```

**Recently updated tickets:**
```
(assignee = currentUser() OR reporter = currentUser())
AND status IN ('New', 'In Progress', 'Refinement')
AND updated >= -14d
ORDER BY priority DESC, updated DESC
```

**All subissues of an epic:**
```
parent = PULP-1030
ORDER BY created DESC
```

**All currently blocked issues:**
```
project = PULP AND cf[10517] = "True"
ORDER BY priority DESC, updated DESC
```
