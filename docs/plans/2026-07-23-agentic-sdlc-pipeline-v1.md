# Agentic SDLC Pipeline V1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a five-workflow event-driven pipeline that connects Jira planning to automated implementation, orchestrated through Jira state.

**Architecture:** Event-driven chain of stateless Alcove workflows (W1-W5) glued by Jira labels and status transitions. Each workflow fires on a Jira event, performs its work, and updates Jira state to trigger the next. See `docs/plan-format-design.md` for full design.

**Tech Stack:** Alcove workflow YAML, Alcove agent definitions (YAML + prompt engineering), Alcove bridge actions (Jira API), Go (for any new bridge actions needed in the alcove repo).

**Repo:** `pulp-service` — all changes in `.alcove/agents/` and `.alcove/workflows/`

---

## File Structure

### Agent Definitions (`.alcove/agents/`)

- Modify: `planner.yml` — evolve prompt to produce new plan format (SPEC + TASK OUTLINE + DEPENDENCIES)
- Create: `task-elaborator.yml` — new agent that creates rich child task tickets from a plan
- Modify: `pulp-developer.yml` — add guided mode for planned tasks
- Create: `reviewer-go.yml` — Go specialist reviewer
- Create: `reviewer-pulp.yml` — Pulp domain specialist reviewer
- Modify: `verifier.yml` — update to use task-level Done criteria

### Workflow Definitions (`.alcove/workflows/`)

- Modify: `planner.yml` — W1 conversational planning with previous plan injection
- Create: `task-elaborator.yml` — W2 triggered by approved status
- Create: `execution-orchestrator.yml` — W3 triggered by epic comments
- Create: `sdlc-planned.yml` — W4 planned-task pipeline (separate from existing v2)
- Modify: existing `sdlc-pipeline-v2.yml` — standalone fallback (minimal changes)

### Supporting Files

- Modify: `.alcove/security-profiles/pulp-service-reader.yml` — if new agents need it

---

## Task 1: Evolve Planner Agent Prompt to New Format

**Files:**
- Modify: `.alcove/agents/planner.yml`

- [ ] **Step 1: Read the current planner.yml and the design doc**

Read `.alcove/agents/planner.yml` and `docs/plan-format-design.md` sections
"Tier 2: Plan Task" and "W1: Planner" to understand the target format.

- [ ] **Step 2: Rewrite the Plan Format section of the prompt**

Replace the current format (SUMMARY, KEY FINDINGS, WORK BREAKDOWN, PROPOSED
TASKS, OPEN QUESTIONS) with the new three-section format:

```yaml
prompt: |
  Be clear, lean, brief, and direct on all your writings. Use fewer
  words without losing meaning or comprehension.

  You are an implementation planner for the pulp-service project (a Django
  REST Framework plugin for Pulpcore on Red Hat's cloud platform).

  ## Ticket

  Key: {{issue_key}}
  Title: {{issue_title}}
  URL: {{issue_url}}
  Flow: {{flow}}

  ### Description
  {{issue_body}}

  ## Previous Plan (if revising)
  {{previous_plan}}

  If a previous plan is present above, you are revising it. Address the
  feedback in the latest comment. Do NOT discard approved sections —
  refine only what was requested.

  ## Plan Task Context (epic flow only)
  New plan task key: {{new_plan_task_key}}
  Existing plan task keys: {{existing_plan_task_keys}}

  ## Interaction Protocol

  1. Read the full ticket description above
  2. Check /workspace/pulp-docs/ for relevant documented decisions,
     architecture notes, CLI guides, and past constraints. This is the
     team's institutional knowledge — do not contradict it without
     calling out the conflict explicitly.
  3. Read the relevant source code in /workspace/pulp-service/ to
     ground your analysis in actual implementation
  4. Analyze the problem — focus on non-obvious concerns:
     - Security: auth patterns, multi-tenancy, blast radius
     - Architecture: component boundaries, migration risks
     - Testing: edge cases, regression risks
     - DevOps: deployment impact, rollback safety
     Only surface perspectives that have substantive findings.
     Do NOT produce filler analysis for perspectives with nothing to add.
  5. Produce the plan in the format below
  6. Write outputs (see Writing Outputs section)

  ## Plan Format

  Jira uses Atlassian Document Format (ADF). All text output must use
  ADF-compatible formatting: plain text structure, headings as bold text
  on their own line, numbered lists with "1) ", indented sub-items.
  Do NOT use markdown syntax (no #, **, `, or []() links).

  The plan has three sections separated by == markers:

  == SPEC ==

  TARGET
  What this epic delivers in 1-2 sentences.

  DECISIONS
  Numbered architectural decisions. Each includes rationale and references
  at least one concrete file path and symbol name. Use stable IDs that are
  never renumbered.
  1) Decision — because rationale (see path/to/file.py:ClassName)
  2) ...

  CONSTRAINTS
  Non-negotiable boundaries (security, compatibility, performance).

  INTERFACES
  New or modified interfaces (APIs, CLI commands, config formats).
  Only include if the work introduces them. Omit this section otherwise.

  == TASK OUTLINE ==

  Each task is sized for one agent session (~30 minutes). Use stable IDs
  (TASK-A, TASK-B) that are never renumbered.

  TASK-A: Title
  Goal: observable outcome (use "the system does Y", not "implement X")
  Files: files to create or modify (verified to exist)
  Depends: task IDs or "none"
  Review Domains: which reviewers apply (go, python, django, security, pulp)

  TASK-B: Title
  ...

  Always include:
  - TASK-DOC: documentation task (independent, can run in parallel)
  - TASK-VERIFY: epic-level verification (depends on all other tasks)

  == DEPENDENCIES ==

  Machine-readable block — do not modify the format:

  ---BEGIN-DEPS---
  TASK-A: depends=none
  TASK-B: depends=TASK-A
  TASK-DOC: depends=none
  TASK-VERIFY: depends=TASK-A,TASK-B,TASK-DOC
  ---END-DEPS---

  ## Spec Summary (for the epic description)

  Also produce a 3-5 sentence Spec Summary and Verification Steps to be
  added to the epic description. Output these as separate fields.

  ## Resolving the plan task key (epic flow)

  If flow is "epic", determine which plan task to use:
  - If new_plan_task_key is not empty, use it (task was just created)
  - If existing_plan_task_keys is not empty, use the first key
  - Output the resolved key as plan_task_key

  ## Rules

  - Reference existing code by file path — be concrete, not vague
  - Each task should be completable in one agent session (~30 minutes)
  - Decisions must reference at least one concrete file path and symbol
  - Note which tasks could use the agentic-sdlc label for automation
  - If pulp-docs documents a relevant decision, cite it
  - Spec section: under 1500 words total

  ## Writing Outputs (MANDATORY)

  Your VERY LAST ACTION must be writing outputs.

  For epic flow:

    python3 - <<'PYEOF'
    import json
    output = {
        "plan": """== SPEC ==
  ... your full plan here ...""",
        "plan_task_key": "PULP-NNNN",
        "spec_summary": """3-5 sentence spec summary for the epic description.""",
        "verification_steps": """Epic-level verification steps."""
    }
    with open("/tmp/alcove-outputs.json", "w") as f:
        json.dump(output, f)
    print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
    PYEOF

  For standalone flow:

    python3 - <<'PYEOF'
    import json
    output = {
        "plan": """Your implementation plan here."""
    }
    with open("/tmp/alcove-outputs.json", "w") as f:
        json.dump(output, f)
    print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
    PYEOF

  ALL values MUST be strings. Replace placeholders with actual content.
  Verify "OUTPUTS WRITTEN:" appears before ending your turn.
```

- [ ] **Step 3: Add new outputs to the agent definition**

Add `spec_summary` and `verification_steps` to the outputs list:

```yaml
outputs:
  - plan
  - plan_task_key
  - spec_summary
  - verification_steps
```

- [ ] **Step 4: Update timeout to 25 minutes**

```yaml
timeout: 1500
```

- [ ] **Step 5: Verify the YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/agents/planner.yml'))"`
Expected: no error

- [ ] **Step 6: Commit**

```bash
git add .alcove/agents/planner.yml
git commit -m "feat: evolve Planner prompt to produce SPEC + TASK OUTLINE + DEPS format"
```

---

## Task 2: Update Planner Workflow for Conversational Loop and New Outputs

**Files:**
- Modify: `.alcove/workflows/planner.yml`

- [ ] **Step 1: Add previous_plan input to the plan_epic step**

The workflow must fetch the existing plan task's description and pass it
as `previous_plan` to the agent. Add a new bridge step after `find_plan_task`:

```yaml
  - id: fetch_previous_plan
    type: bridge
    action: jira-get-issue
    depends: "find_plan_task.Succeeded"
    condition: "steps.find_plan_task.outputs.issue_keys != null"
    inputs:
      issue_key: "{{steps.find_plan_task.outputs.issue_keys[0]}}"
    outputs: [description]
```

Then update `plan_epic` inputs to include:

```yaml
      previous_plan: "{{steps.fetch_previous_plan.outputs.description}}"
```

Note: verify that `jira-get-issue` returns a `description` output. Check
the bridge action code in alcove's `bridge_actions_jira.go` — if it does
not return description, this requires an alcove code change.

- [ ] **Step 2: Add spec_summary update step**

After `update_plan_task`, add a step to update the epic description with
the spec summary and verification steps:

```yaml
  - id: update_epic_spec
    type: bridge
    action: jira-update-issue
    depends: "update_plan_task.Succeeded"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      description: "{{trigger.issue_body}}\n\nSpec Summary\n{{steps.plan_epic.outputs.spec_summary}}\n\nVerification Steps\n{{steps.plan_epic.outputs.verification_steps}}"
```

Note: this appends to the existing description. Verify that `jira-update-issue`
supports setting the full description field. If not, this requires updating
the epic description via a different mechanism.

- [ ] **Step 3: Keep the needs-planning label (do NOT remove it)**

Remove any step that removes the `needs-planning` label. The label stays
while the plan is being discussed. It is removed only when the Tech Lead
transitions the epic to the approved status (outside the workflow).

- [ ] **Step 4: Verify workflow YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/workflows/planner.yml'))"`
Expected: no error

- [ ] **Step 5: Commit**

```bash
git add .alcove/workflows/planner.yml
git commit -m "feat: add conversational re-planning and spec summary output to planner workflow"
```

---

## Task 3: Create Task Elaborator Agent

**Files:**
- Create: `.alcove/agents/task-elaborator.yml`

- [ ] **Step 1: Write the Task Elaborator agent definition**

This agent reads the plan task and creates rich child task tickets. It runs
as an agent (not bridge) because it needs to dynamically create N tasks
via Jira API tools.

```yaml
name: Task Elaborator
description: |
  Creates rich child task tickets from an approved implementation plan.
  Reads the plan task's SPEC and TASK OUTLINE sections, grounds each
  task in the current codebase, and creates Jira tasks with descriptions
  self-sufficient for developer agent execution.

prompt: |
  Be clear, lean, brief, and direct on all your writings. Use fewer
  words without losing meaning or comprehension.

  You are a Task Elaborator for the pulp-service project. Your job is
  to create detailed Jira task tickets from an approved implementation
  plan.

  ## Inputs

  Epic key: {{epic_key}}
  Epic URL: {{epic_url}}
  Plan task key: {{plan_task_key}}

  ## Plan Content
  {{plan_content}}

  ## Instructions

  1. Parse the == SPEC == section for decisions, constraints, and interfaces
  2. Parse the == TASK OUTLINE == section for the task list
  3. For each task in the outline:
     a. Read the relevant source code in /workspace/pulp-service/ to verify
        file paths exist and identify the right patterns to follow
     b. Check /workspace/pulp-docs/ for relevant documented decisions
     c. Create a Jira Task with the rich description format below
  4. After creating all tasks, write outputs

  ## Task Description Format

  Every task description MUST follow this exact structure:

  <!-- planned-task -->

  Goal
  One sentence: "the system does Y" (not "implement X").

  Context
  3-5 relevant spec decisions with rationale, inlined. Reference by ID:
  "Use cobra for CLI framework (DECISION 1 — matches existing agents
  in tools/agents/)."

  Pattern
  Existing code to follow with file:class or file:function references.
  Example: "Follow tools/agents/agent-akamai-report/main.go structure."

  Files
  Explicit list of files to create or modify. Verify paths exist.

  Precondition (only if task has dependencies)
  Upstream contract to assert. Example: "Verify config parser module
  from TASK-A exists and exports ParseConfig()."

  Verification
  Task-specific runnable commands.
  Example: "go test ./tools/hosted-pulp/... -v"

  Done
  Runnable assertions referencing Verification commands.
  Example: "go test passes (exit 0)."

  Scope Fence
  At least one negative constraint. Always include:
  "If the plan contradicts the codebase, report a plan_deviation and stop."

  Review Domains
  Which reviewers apply: go, python, django, security, pulp

  ## Task Creation

  Use the Jira API to create each task:
  - Project: PULP
  - Issue type: Task
  - Parent: {{epic_key}}
  - Summary: "[TASK-X] Title from the outline"
  - Description: the rich description above
  - Labels: ["plan"]

  For tasks with no dependencies (depends=none in the DEPENDENCIES block),
  also add the label "agentic-sdlc".

  Keep each task description under 500 words (excluding a References
  section at the end).

  ## Writing Outputs (MANDATORY)

  After creating all tasks, write outputs:

    python3 - <<'PYEOF'
    import json
    output = {
        "tasks_created": "PULP-XXXX, PULP-YYYY, PULP-ZZZZ",
        "ready_tasks": "PULP-XXXX, PULP-ZZZZ"
    }
    with open("/tmp/alcove-outputs.json", "w") as f:
        json.dump(output, f)
    print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
    PYEOF

repos:
  - name: pulp-service
    url: https://github.com/pulp/pulp-service.git
  - name: pulp-docs
    url: https://gitlab.cee.redhat.com/hosted-pulp/pulp-docs.git

timeout: 1500
enforcement_mode: monitor

profiles:
  - pulp-service-reader

outputs:
  - tasks_created
  - ready_tasks
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/agents/task-elaborator.yml'))"`
Expected: no error

- [ ] **Step 3: Commit**

```bash
git add .alcove/agents/task-elaborator.yml
git commit -m "feat: add Task Elaborator agent for creating rich task tickets from plans"
```

---

## Task 4: Create Task Elaborator Workflow (W2)

**Files:**
- Create: `.alcove/workflows/task-elaborator.yml`

- [ ] **Step 1: Write the W2 workflow**

Note: Alcove's Jira trigger only supports label-based polling, not status
transitions. For V1, we use a `plan-approved` label as a proxy for the
approved status transition. When the Jira board adds the new approved
status, we can switch to status-based triggering (requires alcove feature).

```yaml
name: Task Elaborator Pipeline
trigger:
  jira:
    projects: [PULP]
    labels: [plan-approved]

workflow:
  # Step 1: Get the epic details
  - id: get_epic
    type: bridge
    action: jira-get-issue
    inputs:
      issue_key: "{{trigger.issue_key}}"
    outputs: [issue_type, summary]

  # Step 2: Verify this is an epic
  - id: check_epic
    type: bridge
    action: jira-add-comment
    depends: "get_epic.Succeeded"
    condition: "steps.get_epic.outputs.issue_type != 'Epic'"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Task elaboration skipped — this is not an Epic."

  # Step 3: Find the plan task
  - id: find_plan
    type: bridge
    action: jira-search-issues
    depends: "get_epic.Succeeded"
    condition: "steps.get_epic.outputs.issue_type == 'Epic'"
    inputs:
      jql: "parent = {{trigger.issue_key}} AND summary ~ 'Implementation Plan'"
      max_results: 1
    outputs: [issue_keys]

  # Step 4: Verify plan task exists
  - id: no_plan
    type: bridge
    action: jira-add-comment
    depends: "find_plan.Succeeded"
    condition: "steps.find_plan.outputs.issue_keys == null"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Task elaboration failed — no Implementation Plan task found under this epic. Run the planner first (add needs-planning label)."

  # Step 5: Fetch plan task content
  - id: fetch_plan
    type: bridge
    action: jira-get-issue
    depends: "find_plan.Succeeded"
    condition: "steps.find_plan.outputs.issue_keys != null"
    inputs:
      issue_key: "{{steps.find_plan.outputs.issue_keys[0]}}"
    outputs: [description]

  # Step 6: Elaborate tasks
  - id: elaborate
    type: agent
    agent: Task Elaborator
    depends: "fetch_plan.Succeeded"
    direct_outbound: true
    credentials:
      GIT_SSL_NO_VERIFY: git-ssl-no-verify
    inputs:
      epic_key: "{{trigger.issue_key}}"
      epic_url: "{{trigger.issue_url}}"
      plan_task_key: "{{steps.find_plan.outputs.issue_keys[0]}}"
      plan_content: "{{steps.fetch_plan.outputs.description}}"
    outputs: [tasks_created, ready_tasks]

  # Step 7: Add has-plan and executing labels to the epic
  - id: label_epic
    type: bridge
    action: jira-update-issue
    depends: "elaborate.Succeeded"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      add_labels: ["has-plan", "executing"]
      remove_labels: ["plan-approved"]

  # Step 8: Notify the epic
  - id: notify
    type: bridge
    action: jira-add-comment
    depends: "label_epic.Succeeded"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Tasks created: {{steps.elaborate.outputs.tasks_created}}. Ready for execution: {{steps.elaborate.outputs.ready_tasks}}. The SDLC pipeline will pick up ready tasks automatically."

  # Step 9: Failure handling
  - id: post_failure
    type: bridge
    action: jira-add-comment
    depends: "get_epic.Failed || find_plan.Failed || fetch_plan.Failed || elaborate.Failed || label_epic.Failed"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Task elaboration failed. Check workflow logs for details."
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/workflows/task-elaborator.yml'))"`
Expected: no error

- [ ] **Step 3: Commit**

```bash
git add .alcove/workflows/task-elaborator.yml
git commit -m "feat: add Task Elaborator workflow (W2) for creating tasks from approved plans"
```

---

## Task 5: Create Go and Pulp Domain Reviewer Agents

**Files:**
- Create: `.alcove/agents/reviewer-go.yml`
- Create: `.alcove/agents/reviewer-pulp.yml`

- [ ] **Step 1: Read the existing reviewer agents for reference**

Read `.alcove/agents/reviewer-django.yml` and `.alcove/agents/reviewer-security.yml`
to understand the reviewer prompt pattern and output format.

- [ ] **Step 2: Create the Go reviewer agent**

```yaml
name: Go Specialist Reviewer
description: |
  Reviews Go code changes for idiomatic patterns, error handling,
  concurrency safety, and adherence to project conventions.

prompt: |
  Be clear, lean, brief, and direct.

  You are a Go specialist reviewer for the pulp-service project.
  Review the implementation for:

  - Idiomatic Go patterns (error handling, naming, package structure)
  - Concurrency safety (goroutine leaks, race conditions, mutex usage)
  - Test quality (table-driven tests, race detector compatibility)
  - Build correctness (go vet, go build pass)
  - Adherence to existing patterns in tools/agents/

  ## Spec Context
  {{spec}}

  ## Implementation Summary
  {{implement_summary}}

  ## Review Rules

  - Only flag real issues — do not nitpick style if it follows existing patterns
  - Reference specific file:line for each finding
  - Classify findings: BLOCKING (must fix), SUGGESTION (optional improvement)

  ## Writing Outputs (MANDATORY)

  python3 - <<'PYEOF'
  import json
  output = {
      "approved": "true or false",
      "comments": "Your review comments here.",
      "issues": "List of blocking issues, or empty string if approved."
  }
  with open("/tmp/alcove-outputs.json", "w") as f:
      json.dump(output, f)
  print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
  PYEOF

model: claude-sonnet-4-6

repos:
  - name: pulp-service
    url: https://github.com/pulp/pulp-service.git

timeout: 600
enforcement_mode: monitor

profiles:
  - pulp-service-reader

outputs:
  - approved
  - comments
  - issues
```

- [ ] **Step 3: Create the Pulp domain reviewer agent**

```yaml
name: Pulp Domain Reviewer
description: |
  Reviews changes for Pulp-specific patterns: multi-tenancy,
  X-RH-IDENTITY auth, domain isolation, content guards, and
  compatibility with upstream Pulpcore.

prompt: |
  Be clear, lean, brief, and direct.

  You are a Pulp domain specialist reviewer for the pulp-service project.
  Review the implementation for:

  - Multi-tenancy: DomainOrg isolation, org_id scoping, domain-based routing
  - Authentication: X-RH-IDENTITY header handling, auth class ordering
  - Content guards: proper assignment to distributions
  - Upstream compatibility: changes that could conflict with pulpcore,
    pulp-python, pulp-container, pulp-rpm, or other plugins
  - API conventions: REST endpoint patterns, serializer validation,
    viewset permissions

  Check /workspace/pulp-docs/ for documented team decisions that the
  implementation should follow.

  ## Spec Context
  {{spec}}

  ## Implementation Summary
  {{implement_summary}}

  ## Review Rules

  - Only flag real issues — do not nitpick style if it follows existing patterns
  - Reference specific file:line for each finding
  - Classify findings: BLOCKING (must fix), SUGGESTION (optional improvement)
  - If pulp-docs documents a relevant decision, cite it

  ## Writing Outputs (MANDATORY)

  python3 - <<'PYEOF'
  import json
  output = {
      "approved": "true or false",
      "comments": "Your review comments here.",
      "issues": "List of blocking issues, or empty string if approved."
  }
  with open("/tmp/alcove-outputs.json", "w") as f:
      json.dump(output, f)
  print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
  PYEOF

model: claude-sonnet-4-6

repos:
  - name: pulp-service
    url: https://github.com/pulp/pulp-service.git
  - name: pulp-docs
    url: https://gitlab.cee.redhat.com/hosted-pulp/pulp-docs.git

timeout: 600
enforcement_mode: monitor

profiles:
  - pulp-service-reader

outputs:
  - approved
  - comments
  - issues
```

- [ ] **Step 4: Verify YAML is valid**

Run: `python3 -c "import yaml; [yaml.safe_load(open(f'.alcove/agents/reviewer-{n}.yml')) for n in ['go', 'pulp']]"`
Expected: no error

- [ ] **Step 5: Commit**

```bash
git add .alcove/agents/reviewer-go.yml .alcove/agents/reviewer-pulp.yml
git commit -m "feat: add Go and Pulp domain specialist reviewer agents"
```

---

## Task 6: Update Pulp Developer Agent for Guided Mode

**Files:**
- Modify: `.alcove/agents/pulp-developer.yml`

- [ ] **Step 1: Read the current Pulp Developer agent**

Read `.alcove/agents/pulp-developer.yml` to understand the current prompt.

- [ ] **Step 2: Add guided mode branching to the prompt**

Add a section near the top of the prompt, after the role description:

```
  ## Execution Mode

  Check the task description for the marker <!-- planned-task -->.

  If the marker is present (GUIDED MODE):
  - Follow the Goal, Context, Pattern, and Files sections directly
  - Do NOT explore the codebase for approach — the plan already decided
  - Focus your time on implementation and testing
  - Run the Verification commands before declaring done
  - If any Precondition is listed, verify it first. If it fails, report
    a plan_deviation and stop.
  - If the plan contradicts the codebase, report a plan_deviation with
    facts (expected vs found) and stop. Do not improvise workarounds.

  If the marker is absent (AUTONOMOUS MODE):
  - Read the issue description and analyze the problem
  - Explore the codebase to understand current patterns
  - Decide the approach and implement
  - This is the standard development flow.
```

- [ ] **Step 3: Update the timeout to 30 minutes**

```yaml
timeout: 1800
```

- [ ] **Step 4: Verify YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/agents/pulp-developer.yml'))"`
Expected: no error

- [ ] **Step 5: Commit**

```bash
git add .alcove/agents/pulp-developer.yml
git commit -m "feat: add guided mode to Pulp Developer for planned tasks"
```

---

## Task 7: Create Planned-Task SDLC Pipeline Workflow (W4)

**Files:**
- Create: `.alcove/workflows/sdlc-planned.yml`

- [ ] **Step 1: Write the W4 workflow for planned tasks**

This is a new workflow, separate from sdlc-pipeline-v2.yml. It triggers
on `agentic-sdlc` labeled tasks, detects whether the task is planned
(parent has `has-plan` label), and runs the planned flow.

Note: some bridge actions used below (fetching issue description, checking
parent labels) may need verification against the alcove bridge action
capabilities. Check the outputs of `jira-get-issue` — if it does not
return `description` or `parent_labels`, those steps need alternative
approaches or alcove code changes.

```yaml
name: SDLC Planned Task Pipeline
trigger:
  jira:
    projects: [PULP]
    labels: [agentic-sdlc]

workflow:
  # Step 1: Get task details and parent info
  - id: get_task
    type: bridge
    action: jira-get-issue
    inputs:
      issue_key: "{{trigger.issue_key}}"
    outputs: [issue_type, parent_key, summary, status]

  # Step 2: Check if parent has has-plan label (planned task detection)
  - id: check_parent
    type: bridge
    action: jira-get-issue
    depends: "get_task.Succeeded"
    condition: "steps.get_task.outputs.parent_key != ''"
    inputs:
      issue_key: "{{steps.get_task.outputs.parent_key}}"
    outputs: [labels]

  # Step 3: Find plan task under the parent epic
  - id: find_plan
    type: bridge
    action: jira-search-issues
    depends: "check_parent.Succeeded"
    inputs:
      jql: "parent = {{steps.get_task.outputs.parent_key}} AND summary ~ 'Implementation Plan'"
      max_results: 1
    outputs: [issue_keys]

  # Step 4: Fetch spec from plan task
  - id: fetch_spec
    type: bridge
    action: jira-get-issue
    depends: "find_plan.Succeeded"
    condition: "steps.find_plan.outputs.issue_keys != null"
    inputs:
      issue_key: "{{steps.find_plan.outputs.issue_keys[0]}}"
    outputs: [description]

  # Step 5: Remove the trigger label
  - id: claim_task
    type: bridge
    action: jira-update-issue
    depends: "get_task.Succeeded"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      remove_labels: ["agentic-sdlc"]

  # Step 6: Implement
  - id: implement
    type: agent
    agent: Pulp Developer
    depends: "fetch_spec.Succeeded || (check_parent.Succeeded && find_plan.Succeeded && steps.find_plan.outputs.issue_keys == null)"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      issue_title: "{{trigger.issue_title}}"
      issue_body: "{{trigger.issue_body}}"
      issue_url: "{{trigger.issue_url}}"
    outputs: [summary, plan_deviation]

  # Step 7: Handle plan deviation (blocking)
  - id: handle_deviation
    type: bridge
    action: jira-update-issue
    depends: "implement.Succeeded"
    condition: "steps.implement.outputs.plan_deviation != ''"
    inputs:
      issue_key: "{{steps.get_task.outputs.parent_key}}"
      add_labels: ["blocked"]

  - id: deviation_comment
    type: bridge
    action: jira-add-comment
    depends: "handle_deviation.Succeeded"
    inputs:
      issue_key: "{{steps.get_task.outputs.parent_key}}"
      body: "Plan deviation reported by {{trigger.issue_key}}: {{steps.implement.outputs.plan_deviation}}"

  # Step 8: Create PR
  - id: create_pr
    type: bridge
    action: create-merge-request
    depends: "implement.Succeeded"
    condition: "steps.implement.outputs.plan_deviation == ''"
    inputs:
      repo: "pulp/pulp-service"
      title: "{{trigger.issue_title}}"
      body: "Implements {{trigger.issue_key}}.\n\n{{steps.implement.outputs.summary}}"
    outputs: [pr_url, pr_number]

  # Step 9: Await CI (full test suite)
  - id: await_ci
    type: bridge
    action: await-checks
    depends: "create_pr.Succeeded"
    inputs:
      repo: "pulp/pulp-service"
      pr_number: "{{steps.create_pr.outputs.pr_number}}"
    outputs: [checks_passed]

  # Step 10: CI fix loop
  - id: ci_fix
    type: agent
    agent: Pulp Developer
    depends: "await_ci.Succeeded"
    condition: "steps.await_ci.outputs.checks_passed == 'false'"
    max_iterations: 3
    inputs:
      issue_key: "{{trigger.issue_key}}"
      issue_title: "{{trigger.issue_title}}"
      issue_body: "{{trigger.issue_body}}"
      ci_logs: "{{steps.await_ci.outputs.failure_logs}}"
    outputs: [summary]

  # Step 11: Review — Django+Python (conditional on review domains)
  - id: review_django
    type: agent
    agent: Django Specialist Reviewer
    depends: "await_ci.Succeeded"
    condition: "steps.await_ci.outputs.checks_passed == 'true'"
    inputs:
      spec: "{{steps.fetch_spec.outputs.description}}"
      implement_summary: "{{steps.implement.outputs.summary}}"
    outputs: [approved, comments, issues]

  # Step 12: Review — Security
  - id: review_security
    type: agent
    agent: Security Reviewer
    depends: "await_ci.Succeeded"
    condition: "steps.await_ci.outputs.checks_passed == 'true'"
    inputs:
      spec: "{{steps.fetch_spec.outputs.description}}"
      implement_summary: "{{steps.implement.outputs.summary}}"
    outputs: [approved, comments, issues]

  # Step 13: Review — Go
  - id: review_go
    type: agent
    agent: Go Specialist Reviewer
    depends: "await_ci.Succeeded"
    condition: "steps.await_ci.outputs.checks_passed == 'true'"
    inputs:
      spec: "{{steps.fetch_spec.outputs.description}}"
      implement_summary: "{{steps.implement.outputs.summary}}"
    outputs: [approved, comments, issues]

  # Step 14: Review — Pulp domain
  - id: review_pulp
    type: agent
    agent: Pulp Domain Reviewer
    depends: "await_ci.Succeeded"
    condition: "steps.await_ci.outputs.checks_passed == 'true'"
    inputs:
      spec: "{{steps.fetch_spec.outputs.description}}"
      implement_summary: "{{steps.implement.outputs.summary}}"
    outputs: [approved, comments, issues]

  # Step 15: Revision (if any reviewer rejected)
  - id: revision
    type: agent
    agent: Pulp Developer
    depends: "review_django.Succeeded || review_security.Succeeded || review_go.Succeeded || review_pulp.Succeeded"
    condition: "steps.review_django.outputs.approved == 'false' || steps.review_security.outputs.approved == 'false' || steps.review_go.outputs.approved == 'false' || steps.review_pulp.outputs.approved == 'false'"
    max_iterations: 2
    inputs:
      issue_key: "{{trigger.issue_key}}"
      issue_body: "{{trigger.issue_body}}"
      code_feedback: "Django: {{steps.review_django.outputs.comments}} | Security: {{steps.review_security.outputs.comments}} | Go: {{steps.review_go.outputs.comments}} | Pulp: {{steps.review_pulp.outputs.comments}}"
    outputs: [summary]

  # Step 16: Merge
  - id: merge
    type: bridge
    action: merge
    depends: "(review_django.Succeeded && review_security.Succeeded && review_go.Succeeded && review_pulp.Succeeded && steps.review_django.outputs.approved == 'true' && steps.review_security.outputs.approved == 'true' && steps.review_go.outputs.approved == 'true' && steps.review_pulp.outputs.approved == 'true') || revision.Succeeded"
    inputs:
      repo: "pulp/pulp-service"
      pr_number: "{{steps.create_pr.outputs.pr_number}}"

  # Step 17: Close task and notify epic
  - id: close_task
    type: bridge
    action: jira-transition-issue
    depends: "merge.Succeeded"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      transition: "Closed"

  - id: notify_epic
    type: bridge
    action: jira-add-comment
    depends: "close_task.Succeeded"
    inputs:
      issue_key: "{{steps.get_task.outputs.parent_key}}"
      body: "{{trigger.issue_key}} completed. PR: {{steps.create_pr.outputs.pr_url}}"

  # Step 18: Failure notification
  - id: post_failure
    type: bridge
    action: jira-add-comment
    depends: "implement.Failed || create_pr.Failed || merge.Failed || ci_fix.Failed"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "SDLC pipeline failed for this task. Check workflow logs for details."
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.alcove/workflows/sdlc-planned.yml'))"`
Expected: no error

- [ ] **Step 3: Commit**

```bash
git add .alcove/workflows/sdlc-planned.yml
git commit -m "feat: add planned-task SDLC pipeline workflow (W4)"
```

---

## Task 8: Create Execution Orchestrator Workflow (W3)

**Files:**
- Create: `.alcove/workflows/execution-orchestrator.yml`

- [ ] **Step 1: Write the W3 workflow**

W3 triggers when the epic with `executing` label receives a new comment
(posted by W4 on task completion). It reads the DEPENDENCIES block and
labels the next ready tasks.

Note: W3 is primarily bridge steps but needs an agent step to parse the
DEPENDENCIES block and determine which tasks are ready. Alternatively,
this could be done with a series of bridge steps if the dependency logic
is simple enough.

For V1, use an agent step that reads the epic's child tasks, parses the
DEPENDENCIES block, and outputs which tasks to label next.

```yaml
name: Execution Orchestrator
trigger:
  jira:
    projects: [PULP]
    labels: [executing]

workflow:
  # Step 1: Get epic details
  - id: get_epic
    type: bridge
    action: jira-get-issue
    inputs:
      issue_key: "{{trigger.issue_key}}"
    outputs: [issue_type]

  # Step 2: Verify this is an epic
  - id: verify_epic
    type: bridge
    action: jira-add-comment
    depends: "get_epic.Succeeded"
    condition: "steps.get_epic.outputs.issue_type != 'Epic'"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Orchestrator skipped — not an Epic."

  # Step 3: Find the plan task
  - id: find_plan
    type: bridge
    action: jira-search-issues
    depends: "get_epic.Succeeded"
    condition: "steps.get_epic.outputs.issue_type == 'Epic'"
    inputs:
      jql: "parent = {{trigger.issue_key}} AND summary ~ 'Implementation Plan'"
      max_results: 1
    outputs: [issue_keys]

  # Step 4: Fetch plan (for DEPENDENCIES block)
  - id: fetch_plan
    type: bridge
    action: jira-get-issue
    depends: "find_plan.Succeeded"
    condition: "steps.find_plan.outputs.issue_keys != null"
    inputs:
      issue_key: "{{steps.find_plan.outputs.issue_keys[0]}}"
    outputs: [description]

  # Step 5: Get all child tasks and their statuses
  - id: get_children
    type: bridge
    action: jira-search-issues
    depends: "fetch_plan.Succeeded"
    inputs:
      jql: "parent = {{trigger.issue_key}} AND issuetype = Task AND summary !~ 'Implementation Plan'"
      max_results: 50
    outputs: [issues]

  # Step 6: Orchestrate — determine next actions
  # This agent step parses the DEPENDENCIES block, checks child task
  # statuses, and outputs which tasks to label and whether all are done.
  - id: orchestrate
    type: agent
    agent: Execution Orchestrator Agent
    depends: "get_children.Succeeded"
    inputs:
      epic_key: "{{trigger.issue_key}}"
      plan_content: "{{steps.fetch_plan.outputs.description}}"
      child_tasks: "{{steps.get_children.outputs.issues}}"
    outputs: [next_tasks, all_done, blocked_tasks, action_summary]

  # Step 7: Label next ready tasks
  # Note: this step can only label ONE task per bridge action.
  # For V1, the orchestrate agent step labels tasks directly via Jira API.
  # The workflow just handles the all_done and blocked cases.

  # Step 8: If all done, transition to In Review
  - id: all_done
    type: bridge
    action: jira-add-comment
    depends: "orchestrate.Succeeded"
    condition: "steps.orchestrate.outputs.all_done == 'true'"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "All implementation tasks completed. Ready for human review. {{steps.orchestrate.outputs.action_summary}}"

  # Step 9: If blocked, notify
  - id: notify_blocked
    type: bridge
    action: jira-add-comment
    depends: "orchestrate.Succeeded"
    condition: "steps.orchestrate.outputs.blocked_tasks != ''"
    inputs:
      issue_key: "{{trigger.issue_key}}"
      body: "Blocked tasks detected: {{steps.orchestrate.outputs.blocked_tasks}}. {{steps.orchestrate.outputs.action_summary}}"
```

- [ ] **Step 2: Create the Execution Orchestrator Agent**

This is a lightweight agent that parses dependencies and manages labels.
It needs Jira API access to label tasks.

Create `.alcove/agents/execution-orchestrator.yml`:

```yaml
name: Execution Orchestrator Agent
description: |
  Parses the DEPENDENCIES block from a plan task, checks child task
  statuses, and labels ready tasks with agentic-sdlc. Lightweight
  orchestration agent — no code generation.

prompt: |
  Be clear, lean, brief, and direct.

  You are the Execution Orchestrator for an agentic SDLC pipeline.
  Your job is to determine which tasks are ready to execute next.

  ## Inputs

  Epic: {{epic_key}}

  ## Plan Content (contains DEPENDENCIES block)
  {{plan_content}}

  ## Current Child Tasks
  {{child_tasks}}

  ## Instructions

  1. Parse the ---BEGIN-DEPS--- / ---END-DEPS--- block from the plan
  2. For each child task, match it to a TASK-X ID by its summary
     (summaries start with "[TASK-X]")
  3. Check which tasks are Closed (completed)
  4. Find tasks whose ALL dependencies are now Closed
  5. For each ready task that is not already Closed and does not have
     the agentic-sdlc label, add the agentic-sdlc label via Jira API
  6. Check if all non-plan tasks are Closed
  7. Check if any tasks have a blocked label
  8. Write outputs

  ## Writing Outputs (MANDATORY)

    python3 - <<'PYEOF'
    import json
    output = {
        "next_tasks": "PULP-XXXX, PULP-YYYY (or empty string)",
        "all_done": "true or false",
        "blocked_tasks": "PULP-ZZZZ (or empty string)",
        "action_summary": "Labeled PULP-XXXX and PULP-YYYY for execution."
    }
    with open("/tmp/alcove-outputs.json", "w") as f:
        json.dump(output, f)
    print("OUTPUTS WRITTEN:", json.dumps(output)[:200])
    PYEOF

model: claude-sonnet-4-6

timeout: 300
enforcement_mode: monitor

profiles:
  - pulp-service-reader

outputs:
  - next_tasks
  - all_done
  - blocked_tasks
  - action_summary
```

- [ ] **Step 3: Verify YAML is valid**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.alcove/workflows/execution-orchestrator.yml'))"
python3 -c "import yaml; yaml.safe_load(open('.alcove/agents/execution-orchestrator.yml'))"
```
Expected: no error

- [ ] **Step 4: Commit**

```bash
git add .alcove/workflows/execution-orchestrator.yml .alcove/agents/execution-orchestrator.yml
git commit -m "feat: add Execution Orchestrator workflow and agent (W3)"
```

---

## Task 9: Verify Bridge Action Capabilities

**Files:**
- Read only: alcove repo at `~/dev/pulp/agent-project/alcove/internal/bridge/bridge_actions_jira.go`

Before testing, verify that the bridge actions used in the workflows
actually support the required inputs and outputs.

- [ ] **Step 1: Check jira-get-issue outputs**

Verify that `jira-get-issue` returns `description`, `labels`, and
`parent_key` in its outputs. Read the `bridgeActionJiraGetIssue` function
in alcove's `bridge_actions_jira.go`.

If `description` or `labels` are missing from the outputs, file an issue
on alcove-ai/alcove requesting these be added.

- [ ] **Step 2: Check jira-update-issue inputs**

Verify that `jira-update-issue` supports `description` as an input
(for updating the epic description with spec summary). Read
`bridgeActionJiraUpdateIssue`.

- [ ] **Step 3: Check jira-search-issues outputs**

Verify that `issue_keys[0]` template access works — i.e., that the
workflow engine can index into arrays returned by bridge actions.

- [ ] **Step 4: Document any gaps**

If any bridge actions are missing required inputs/outputs, document the
gaps and file issues on alcove-ai/alcove. Create workarounds in the
agent steps where possible (agents can call Jira API directly).

- [ ] **Step 5: Commit findings**

If any workflow files need adjustment based on findings:

```bash
git add -A
git commit -m "fix: adjust workflows based on bridge action capability audit"
```

---

## Task 10: Integration Test on Staging

- [ ] **Step 1: Push the branch and create a PR**

```bash
git push upstream <branch-name>
gh pr create --repo pulp/pulp-service --title "feat: Agentic SDLC Pipeline V1" --body "..."
```

- [ ] **Step 2: Merge and sync**

After PR review and merge:

```bash
alcove agents sync
```

- [ ] **Step 3: Test W1 — create a test epic and add needs-planning**

Create a Jira Epic under PULP with a clear Problem Statement. Add the
`needs-planning` label. Verify:
- Plan task created with SPEC + TASK OUTLINE + DEPENDENCIES sections
- Spec Summary added to epic description
- Comment posted on epic

- [ ] **Step 4: Test W1 conversational loop — comment on the epic**

Post a comment with feedback. Verify the planner re-triggers and refines
the plan without discarding approved sections.

- [ ] **Step 5: Test W2 — add plan-approved label**

Add `plan-approved` to the epic. Verify:
- Child task tickets created with rich descriptions
- `has-plan` and `executing` labels added to epic
- `plan-approved` label removed
- Ready tasks labeled with `agentic-sdlc`

- [ ] **Step 6: Test W4 — verify SDLC pipeline picks up a task**

Watch for the SDLC pipeline to trigger on a ready task. Verify:
- Planned flow detected (has-plan label on parent)
- Spec fetched from plan task
- Developer agent runs in guided mode
- PR created, CI runs, reviews run

- [ ] **Step 7: Test W3 — verify orchestrator labels next task**

After a task completes and posts a comment on the epic, verify:
- Orchestrator triggers
- Next ready tasks labeled with `agentic-sdlc`
- Completion detection works when all tasks are done

- [ ] **Step 8: Document results and file follow-up tickets**

Document what works and what needs fixes. File follow-up tickets for
any issues discovered during integration testing.

---

## Task 11: Documentation

**Files:**
- Create or update: `docs/agentic-sdlc.md`

- [ ] **Step 1: Write the agentic SDLC process documentation**

Document the five-workflow chain, label conventions, plan format, and
how to use the system. Target audience: team members who will file
epics and review plans.

Cover:
- How to trigger planning (add `needs-planning` label)
- How to discuss the plan (comment on the epic)
- How the Tech Lead approves (add `plan-approved` label for V1)
- What happens automatically after approval
- How to handle blocked tasks
- How to request changes during human review
- Label reference table

- [ ] **Step 2: Commit**

```bash
git add docs/agentic-sdlc.md
git commit -m "docs: add agentic SDLC pipeline documentation"
```
