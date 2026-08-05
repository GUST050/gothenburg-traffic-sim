# Collaborative Agent Guide

## Purpose

This repository supports collaboration between different models and tools
without binding a model to a permanent role or forcing work through a rigid
state machine. An actor is the model or person doing the current work. Roles
such as planner, implementer, researcher, tester and reviewer describe the work
being done; they are not identities or permission classes.

The goal is simple: understand the user's latest request, take the next useful
safe action, verify the result and leave enough context for another actor to
continue.

## Sources of truth

Use these sources in this order:

1. The user's latest request and any explicit scope or safety limits.
2. System, tool and environment rules that cannot be changed by repository
   documentation.
3. `AGENTS.md` for collaboration conventions.
4. `ARCHITECTURE.md` for program structure and technical contracts.
5. The marked current blocks in `TASKS.md` and `AGENT_NOTES.md` for the latest
   project focus and handoff context.
6. `IMPROVEMENT_PLAN.md` for priorities and longer-term direction.

Everything outside the marked current blocks in `TASKS.md` and
`AGENT_NOTES.md` is historical or supporting context. Historical Sol/Luna
labels, states, approvals and handoffs describe how earlier work was managed;
they do not restrict current actors.

`CLAUDE.md`, `docs/plans/PROJECT_CONTEXT_OLD_AGENTS.md`, dated audits and archived copies
are useful background, not workflow authority. When history conflicts with the
current architecture, current task or latest user request, the current source
wins.

## Flexible actors

- Any capable model or person may plan, implement, test, debug, document or
  review.
- Codex, Claude and other models have no fixed repository role. The user may
  name a model or role, but does not have to.
- One actor may take a task from discovery through implementation and
  verification. A handoff or independent review is useful when risk warrants
  it, not mandatory for ordinary progress.
- A suggested next actor or action is advisory. If a different actor can safely
  make progress, it should do so.
- Direct user requests may replace, pause or reprioritize the current focus.
  Update the current blocks so the repository reflects that decision.
- Do not stop merely because an old owner, state, revision or transition names
  another model. Stop only for a real blocker, a safety boundary or a material
  choice that needs the user.

## Working loop

For non-trivial work:

1. Read this file and the user's current request.
2. Read the marked current blocks in `TASKS.md` and `AGENT_NOTES.md` when they
   are relevant. Read architecture or plan sections only as needed.
3. Run `git status --short` and inspect relevant diffs. Preserve unrelated and
   user-owned changes.
4. State the outcome being pursued and choose the next useful action.
5. Inspect, implement and verify autonomously within the user's scope.
6. Repair in-scope defects found by checks. Ask the user only when a missing
   decision would materially change the result or when new authority is needed.
7. For substantial work, refresh the marked current blocks with a concise,
   truthful snapshot. Do not rewrite historical entries merely to modernize
   their terminology.
8. Report the result, checks, remaining risks and a useful next step.

Searching, reading, local edits, focused tests and ordinary debugging do not
need a planning/review round trip. Keep plans proportional to the task; small
changes need no ceremony.

## Current coordination blocks

The markers are retained for compatibility with existing scripts and for quick
cross-model orientation. They are coordination records, not permission gates.
Each current marker must occur exactly once in its owning file.

`TASKS.md` contains:

```text
<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL
- Mode
- Current focus
- Status
- Suggested next action
- Eligible actors
- Safety boundary
- Updated
<!-- WORKFLOW_CONTROL_END -->
```

and:

```text
<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK
### <TASK-ID> — <title>
- Status
- Objective and scope
- Completion outcome
- Context or checkpoints
- Primary files
- Constraints and safety
- Acceptance criteria
- Useful checks
<!-- ACTIVE_TASK_END -->
```

`AGENT_NOTES.md` contains:

```text
<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF
- Focus and status
- Summary
- Files changed
- Checks
- Decisions and evidence
- Blockers or risks
- Suggested next action
- Actor notes
<!-- CURRENT_HANDOFF_END -->
```

Statuses such as `READY`, `IN_PROGRESS`, `BLOCKED`, `REVIEW` and `DONE` are
descriptive only. Any actor may update them when the evidence changes. Task IDs,
revisions and primary-file lists help traceability but do not prevent related
work required to finish the user's requested outcome.

If current markers are missing or duplicated, repair them when the intended
current context is clear from the latest user request and nearby content. If it
is not clear, report the ambiguity instead of inventing project history.

## Continuation and handoffs

`CONTINUE` or `CONTINUE using AGENTS.md` means: inspect the current context and
continue with the next useful safe action. It works in any model or tool.

A handoff should be concise and decision-useful. Record what changed, exact
checks and results, important evidence, real blockers and what another actor
can do next. Do not require the user to paste a special role command. Do not
create a handoff solely to transfer work between planning and implementation
when the same actor can continue safely.

When reviewing, report findings by severity and evidence. The reviewer may fix
issues when the user asked for a completed outcome; otherwise keep a pure review
read-only. Independent review is recommended for high-risk release, security,
data-integrity and scientific-claim changes, but the reviewer can be any capable
actor.

## Scope and autonomy

Treat an active task as a focus contract, not a cage:

- Work toward the complete user-visible or evidence-visible outcome.
- Inspect and edit tightly related files when necessary, and record the reason.
- Do not bundle unrelated work merely because it is nearby.
- Preserve unrelated dirty-worktree changes.
- Prefer repository-discoverable answers over routine clarification questions.
- If the current approach cannot reach the goal, revise the approach and the
  coordination record instead of preserving a failing workflow for its own
  sake.

Stop and ask only when there is a genuine authority boundary, destructive or
irreversible consequence, missing secret/access, material architecture choice,
or several credible approaches have failed and user direction is needed.

## Safety and evidence

Flexibility does not weaken product, scientific or operational safeguards.

- Do not delete data, reset user changes, publish, deploy, push, release, spend
  money, contact external parties or perform another consequential external
  action unless the user's request and the tool's approval rules authorize it.
- Local source edits, documentation, read-only inspection and focused tests are
  normally allowed as part of implementation.
- SUMO runs, campaign creation, outcome inspection and demand/horizon warming
  may be performed when they are materially required by the user's requested
  goal, their scope and cost are understood, and no narrower safety rule
  requires confirmation. They do not require a special Sol/Luna task or magic
  approval wording.
- Before an expensive or evidence-producing run, bind inputs and outputs,
  avoid clobbering prior evidence, and make clear whether the run is diagnostic
  or release evidence.
- Never weaken validation, provenance, exactness, recall, regret,
  failure-recall, health, adoption, release or publication gates merely to make
  a result pass. Any deliberate contract change must be explicit and tested.
- Diagnostic replay is not release evidence unless the release contract says
  so and the user has requested that promotion.
- Keep secrets out of logs and documentation.

Approval can be expressed in normal language. Record the relevant scope when it
matters, but do not demand an exact quote, immutable phrase or role-specific
recorder unless an external system genuinely requires it.

## Documentation quality

- Keep current summaries short; keep detailed history in dated sections.
- Use stable links and file names. Do not copy the same current state into many
  places.
- Label hypotheses, measurements, decisions and superseded conclusions
  separately.
- Update `ARCHITECTURE.md` when structure or contracts change and
  `IMPROVEMENT_PLAN.md` when priorities or evidence change.
- Preserve historical records unless they are factually wrong; add a correction
  or supersession note rather than silently rewriting the past.
