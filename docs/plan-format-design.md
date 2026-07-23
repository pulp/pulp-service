# Agentic SDLC Pipeline — Plan Format and Orchestration

Design for an event-driven agentic SDLC pipeline that connects Jira planning
to automated implementation through a structured plan format optimized for
both human review and agent consumption.

## Problem

The Jira planner and the SDLC pipeline are disconnected. The planner stores a
flat text plan in Jira. The SDLC pipeline generates its own plan from scratch.
Plans lack structure for agents to parse. Duplicate plan tasks get created on
re-plans. Developer agents receive insufficient context for sessions. No
orchestration connects task completion to the next task's execution.

## Design Principles

- Spec answers "what should exist." Plan answers "how to get there."
- Agents need both, but in different places and at different densities.
- Human approval gates use Jira status transitions, not labels.
- Each task ticket must be self-sufficient for one agent session.
- Plan content is immutable after approval. Amendments go in comments.
- When the plan contradicts the codebase, agents report facts and stop —
  never improvise workarounds silently.
- Every workflow must be idempotent — check what already exists before
  acting, so crash recovery is just re-triggering the same event.
- Jira is the state store, not a message bus. Accept 2-minute polling
  latency; don't add label-based signals to work around it.
- Fail fast — validate preconditions before starting agent sessions,
  not mid-flight.

## Roles

The planner agent acts as a delegate of the Epic Owner. The Epic Owner
triggers planning and approves the output. The planner creates tasks on
the Epic Owner's behalf, consistent with the team process rule that only
the Epic Owner creates tasks under their Epic.

## Scope

### V1 (this design)

Five-workflow event-driven chain: plan, elaborate, orchestrate, implement,
and human review. Delivers code and gets human sign-off.

### V2 (future)

- Process Review (RAKI) — analyzes pipeline performance across the epic
- Wrap-up (Knowledge Extraction) — extracts learnings into pulp-docs MR
- Read-only interface contracts fed forward between dependent tasks
- Reviewer-router — selects reviewers dynamically based on changed files

V2 runs asynchronously on already-closed epics. No V1 design decisions
need to change to accommodate V2.

## Three-Tier Structure

### Tier 1: Epic Description (human + planner)

The epic description follows the team process document. The planner adds two
sections after the human-authored content:

    Problem Statement          (human-authored)
    Business Impact            (human-authored)
    Requirements               (human-authored)
    Spec Summary               (planner Phase 1 — 3-5 sentences)
    Verification Steps         (planner Phase 1)

The Spec Summary covers approach, key architectural decisions, and scope. It
gives the Tech Lead and stakeholders enough to review without reading the full
plan task. Verification Steps are epic-level: how to prove the whole epic works
end-to-end after all tasks complete.

W2 adds a `has-plan` label to the epic after creating the plan task. This
label is used for detection (not title-based search) when determining whether
a child task is planned or standalone.

### Tier 2: Plan Task (child of epic, planner-owned)

A Jira Task titled "Implementation Plan for PULP-XXXX", created as a child of
the epic. Contains the full spec, task outline, and machine-readable dependency
block.

    == SPEC ==

    TARGET
    What this epic delivers in 1-2 sentences.

    DECISIONS
    Numbered architectural decisions that apply across all tasks. Use stable
    IDs that are never renumbered. Each decision includes its rationale —
    the "because" is what prevents context drift when developer agents
    reference these decisions. Each decision must reference at least one
    concrete file path and symbol name.
    1) Decision description — because rationale (see path/to/file.py:ClassName)
    2) ...

    CONSTRAINTS
    Non-negotiable boundaries.
    - Security, compatibility, performance constraints

    INTERFACES
    New or modified interfaces (APIs, CLI commands, config formats).
    Only include if the work introduces them.

    == TASK OUTLINE ==

    TASK-A: Title
    Goal: what state the codebase is in after this task
    Files: files to create or modify
    Depends: task IDs or "none"

    TASK-B: Title
    ...

    == DEPENDENCIES ==

    Machine-readable dependency block consumed by the Execution Orchestrator.
    The orchestrator reads this exclusively — it does not parse the task
    outline prose.

    ---BEGIN-DEPS---
    TASK-A: depends=none
    TASK-B: depends=TASK-A
    TASK-C: depends=TASK-A
    TASK-D: depends=TASK-B,TASK-C
    TASK-DOC: depends=none
    TASK-VERIFY: depends=TASK-A,TASK-B,TASK-C,TASK-D,TASK-DOC
    ---END-DEPS---

Task IDs use stable identifiers (TASK-A, TASK-B) that are never renumbered.
Adding or removing tasks does not affect existing IDs. The TASK OUTLINE is a
compact overview — rich details go in the child task tickets (Tier 3).

The SPEC section is under 1500 words. The plan task description is immutable
after Tech Lead approval. If the plan needs amendment, post a comment — do not
edit the description.

### Tier 3: Child Task Tickets (created by planner Phase 2)

Each task is a Jira Task, child of the epic, with a rich description
self-sufficient for a developer agent to execute in one session.

    <!-- planned-task -->

    Goal
    One sentence describing the observable outcome. Use "the system does Y"
    not "implement X."

    Context
    The 3-5 most relevant spec decisions and constraints, inlined with
    rationale. Not the full spec. Example: "Use cobra for CLI framework
    (DECISION 1 — matches existing agents in tools/agents/). Auth via
    Basic Auth to X-RH-IDENTITY transform (DECISION 3 — gateway generates
    the identity header, clients must use BasicAuth with TBR)."

    Pattern
    Existing code to follow, with file:class or file:function references.
    Example: "Follow tools/agents/agent-akamai-report/main.go structure."

    Files
    Explicit list of files to create or modify. Paths verified to exist
    at planning time.

    Precondition (dependent tasks only)
    Upstream contract to assert before starting work. Example: "Verify
    the config parser module from TASK-A exists and exports ParseConfig()."

    Verification
    Task-specific runnable commands.
    Example: "go test ./tools/hosted-pulp/... -v"

    Done
    Observable, runnable assertions. Each criterion references a specific
    Verification command and its expected output.
    Example: "go test passes (exit 0). curl -s -o /dev/null -w '%{http_code}'
    http://localhost:24817/api/v3/ returns 403."

    Scope Fence
    What NOT to do. Every task includes at least one negative constraint.
    Example: "Do NOT modify the content app." "Do NOT add new dependencies."
    Includes standing rule: "If the plan contradicts the codebase, report
    a plan_deviation and stop. Do not improvise workarounds."

    Review Domains
    Which reviewers apply to this task based on the files changed.
    Example: "go, security" or "python, django, pulp"

Each task ticket is under 500 words (excluding a References section of file
paths and decision IDs, which does not count against the limit).

The `<!-- planned-task -->` marker is a deterministic signal for the developer
agent to use guided mode (follow the task structure) rather than autonomous
mode (explore and decide approach).

### Required Special Tasks

Every plan includes these tasks in addition to implementation tasks:

Documentation Task
    Same template as other tasks. Goal, Files (docs to create/update),
    Verification (e.g., "make docs passes, links resolve"), Done criteria.
    Not a vague placeholder — must be as concrete as any implementation task.
    Independent of implementation tasks (can run in parallel).

Verification Task (always the final task)
    Runs the epic-level Verification Steps after all other tasks complete.
    Its Precondition lists all other tasks. Its Verification field contains
    the epic's Verification Steps as runnable commands. This is the executor
    for the epic's end-to-end verification.

## V1 Orchestration: Five-Workflow Chain

### W1: Planner (conversational)

    Trigger: needs-planning label on Epic in Refinement status
    Timeout: 25 minutes (extended — reads full codebase + pulp-docs)

    Behavior:
    1. Reads epic description, codebase, and pulp-docs
    2. If a previous plan task exists, reads it and injects as
       "Current Plan (do not discard approved sections)" — the agent
       refines rather than regenerates
    3. Writes Spec Summary and Verification Steps into epic description
    4. Creates or updates the Plan Task with full SPEC + TASK OUTLINE +
       DEPENDENCIES block
    5. Posts a comment on the epic: "Implementation plan created at PULP-XXXX"

    Conversational loop:
    The needs-planning label stays while the plan is being discussed.
    New comments on the epic re-trigger W1 with the latest comment as
    feedback. The agent receives its previous plan output and the new
    comment, refining specific sections rather than starting fresh.
    The label is removed when the Tech Lead approves (status transition).

    Idempotency:
    If a plan task already exists, W1 updates it rather than creating
    a duplicate. Uses the find_plan_task search with issue_keys null
    check (not total == 0).

### W2: Task Elaborator

    Trigger: Epic transitions to approved status (new Jira status between
             Refinement and In Progress)

    Behavior:
    1. Reads the plan task (spec + outline + dependencies)
    2. Reads the codebase for current file paths and patterns
    3. Creates child Task tickets under the epic with rich descriptions
    4. Adds `has-plan` label to the epic
    5. Adds agentic-sdlc label to tasks with no dependencies (ready tasks)
    6. Transitions the epic to In Progress
    7. Posts a comment on the epic listing created tasks and dependency order

    Idempotency:
    Checks for existing child tasks before creating. Only creates missing
    tasks. Safe to re-trigger after partial failure.

### W3: Execution Orchestrator (stateless, no agent)

    Trigger: Epic with `executing` label receives a new comment (posted by
             W4 on task completion or failure)

    Behavior:
    1. Validates full epic state (not just the changed task) to handle
       manual Jira edits
    2. Reads the DEPENDENCIES block from the plan task
    3. If a task completed (PR merged + verified):
       a. Finds tasks whose dependencies are now all satisfied
       b. Labels them with agentic-sdlc
    4. If a task failed (blocked after N CI-fix attempts):
       a. Labels the task as blocked
       b. Propagates blocked to all transitive dependents
       c. Adds `blocked` label to the epic
       d. Comments on the epic tagging the Epic Owner with the failure chain
    5. If all implementation tasks are closed:
       a. Transitions the epic to In Review
       b. Creates a Human Review task (W5 trigger)

    Idempotency:
    Reads current state and computes correct labels. Re-running produces
    the same result. Does not duplicate labels or comments.

    Race condition mitigation:
    W4 transitions the task to Closed BEFORE posting the epic comment.
    This ensures W3 reads consistent child task statuses.

### W4: SDLC Pipeline (per task)

    Trigger: agentic-sdlc label on Task
    Developer agent timeout: 30 minutes
    Reviewer/verifier timeout: 10 minutes each

    Detection (planned vs standalone):
    Check if the parent epic has the `has-plan` label (set by W2).
    If yes → planned flow. If no → standalone flow.
    This is a label check, not a title-based search — robust against
    renames and manual edits.

    Planned flow:
    1. Resolve parent epic (bridge: jira-get-issue)
    2. Find plan task under epic (bridge: jira-search-issues)
    3. Fetch spec from plan task description (bridge: jira-get-issue)
    4. Precondition gate (bridge step): if task has Precondition field,
       run predecessor verification commands. Fail fast if broken —
       do not start the developer agent on an invalid foundation.
    5. Implement (agent: Pulp Developer, 30-min timeout)
       - Receives {{task_description}} with inlined Context
       - Full spec NOT injected — the task's Context field is sufficient
       - Detects `<!-- planned-task -->` marker for guided mode
       - Reports plan_deviation as facts (expected vs found); deterministic
         rule decides severity, not the LLM
    6. Verify (agent: Implementation Verifier, 10-min timeout)
       - Uses task-level Verification commands and Done criteria
    7. Review (agents: conditional based on task's Review Domains field)
       - Available reviewers: Django+Python, Security, Go, Pulp-domain
       - Only matching reviewers run (e.g., Go task skips Django reviewer)
       - All selected reviewers receive {{spec}} + implementation diff
       - Run in parallel
    8. Revision loop (max 2 cycles, then escalate to human)
       - Developer agent receives review feedback, applies fixes
       - Re-verify after fixes
    9. Create PR, await CI
       - CI runs full test suite, not just task-specific verification
       - CI-fix loop (max 3 iterations)
       - CI fixes after final review DO NOT re-trigger full review —
         scoped to CI failure only
    10. Merge PR
    11. Transition task to Closed
    12. Post comment on epic: "TASK-A completed" (triggers W3)

    Standalone flow (no parent epic or no has-plan label):
    Falls through to existing v2 pipeline: triage -> plan -> implement ->
    verify -> review -> create PR. Backward compatible.

    Branch naming:
    Includes task ID to prevent conflicts when independent tasks run in
    parallel (e.g., fix/PULP-2105-TASK-A).

    Timeout recovery:
    If the developer agent's 30-minute session expires mid-implementation,
    the task transitions to a timed-out status. W4 posts a comment on the
    epic with partial progress. W3 treats this as a failure — labels the
    task as blocked and notifies the Epic Owner.

    Plan deviation handling:
    The agent reports deviations as facts: "Expected file X at path Y,
    found Z instead." A deterministic rule decides severity:
    - If deviation changes the public API surface or touches files outside
      the task's Files list → blocking. Pipeline halts, `blocked` label
      on the epic.
    - Otherwise → informational. Agent continues, reports post-completion.

### W5: Human Review

    Trigger: W3 creates the Human Review task (all implementation tasks done)

    Behavior:
    1. The review task summarizes what was built across all child tasks
    2. Epic Owner verifies the full implementation meets expectations
    3. Approval: Epic Owner transitions epic to Closed via status transition
    4. Changes needed (minor): Epic Owner creates new child tasks directly,
       W3 picks them up normally
    5. Changes needed (major): Epic Owner moves epic back to Refinement,
       which re-triggers W1 for re-planning

    The epic closes on human approval. V2 workflows (process review,
    wrap-up) run asynchronously on already-closed epics.

## Jira Status Lifecycle

    New                    Reporter creates epic
    Refinement             Product Owner prioritizes, Epic Owner plans (W1)
    Approved (new)         Tech Lead approves plan, triggers W2
    In Progress            W2 completes task creation, work begins
    In Review (new)        W3 detects all tasks done, creates review task
    Closed                 Epic Owner approves in W5

Child tasks follow their own lifecycle independently:
    New                    Created by W2
    In Progress            W4 picks up (agentic-sdlc label)
    Closed                 W4 completes (PR merged + verified)

## Labels

Only two workflow-functional labels:

    needs-planning         On Epic — triggers W1, stays during discussion
    agentic-sdlc           On Task — triggers W4 for one task execution

Supporting labels (set by workflows, not triggers):

    has-plan               On Epic — set by W2, signals plan task exists
    executing              On Epic — set by W2, stays during execution
    blocked                On Epic — set by W3 on failure/deviation

## Task Dependencies and Ordering

- Tasks use stable IDs (TASK-A, TASK-B) that are never renumbered
- Dependencies are declared in a machine-readable DEPENDENCIES block
  in the plan task, not parsed from prose
- The Execution Orchestrator (W3) reads this block exclusively
- Dependent tasks include a Precondition field; a bridge step validates
  preconditions before the developer agent starts (fail fast)
- CI runs the full test suite to catch cross-task regressions
- Independent tasks can be labeled agentic-sdlc simultaneously and run
  in parallel as separate pipeline executions
- Sequential execution by default — the planner labels only ready tasks
- Branch naming includes task ID to prevent parallel conflicts

## Failure Handling

Task failure (W4 fails after N CI-fix attempts):
    1. W3 labels the task as blocked
    2. W3 propagates blocked to all transitive dependents
    3. W3 adds `blocked` label to the epic
    4. W3 comments on the epic tagging the Epic Owner
    5. Pipeline pauses until human intervention
    Do not auto-retry with different strategies — likely a design problem.

Plan deviation:
    Agent reports facts (expected vs found). Deterministic severity rule:
    - Touches files outside task's Files list or changes public API → blocking
    - Otherwise → informational
    Blocking: pipeline halts, `blocked` label on epic.
    Informational: agent continues, reports post-completion.

Session timeout:
    Developer agent's 30-minute session expires → task marked timed-out.
    W3 treats as failure — blocks task and notifies Epic Owner.

Partial workflow failure (W2 creates 3 of 5 tasks and crashes):
    Re-trigger W2. Idempotency ensures it detects existing tasks and
    only creates the missing ones.

Review escalation:
    After 2 revision cycles without reviewer approval, pipeline stops
    and posts structured escalation to the epic with remaining findings
    and options (fix manually, run more cycles, create PR with known
    issues, re-plan the approach).

## Ceremony Threshold

For small epics (2 or fewer implementation tasks), skip the plan task
tier entirely. The planner writes the spec summary + verification steps
on the epic and creates child tasks directly. No DEPENDENCIES block
needed — sequential by default. W3 still orchestrates execution.

## Format Rules

- All Jira text uses ADF-compatible plain text. No markdown syntax.
- Spec section: under 1500 words
- Task ticket description: under 500 words (excluding References section)
- Task IDs are stable and never renumbered
- Decisions include rationale ("X — because Y"), not just conclusions
- Decisions reference at least one concrete file path and symbol name
- Use outcome-based goals ("the system does Y"), not implementation-
  prescriptive ("add method X to file Y")
- Every task includes at least one scope fence (what NOT to do)
- Every task includes: "If the plan contradicts the codebase, report
  a plan_deviation and stop."
- File paths verified to exist at planning time
- Pattern references use file:class or file:function specificity
- Done criteria must be runnable assertions referencing Verification
  commands, not prose
- Every task includes a Review Domains field listing which reviewers apply
