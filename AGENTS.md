# Codex Sol/Luna Agent Router

## Authority

ARCHITECTURE.md is the source of truth for program structure.
IMPROVEMENT_PLAN.md is the source of truth for improvement priorities.
PROJECT_CONTEXT_OLD_AGENTS.md and CLAUDE.md are historical project context.

If files disagree about architecture, ARCHITECTURE.md wins.
If files disagree about improvement priority, IMPROVEMENT_PLAN.md wins.

## Agent roles

Sol High is the master agent.
Luna High is the worker agent.

Sol High owns:
- planning
- prioritization
- architecture decisions
- risk analysis
- task decomposition
- final review
- approval

Luna High owns:
- implementation
- reading relevant files
- writing tests
- running tests
- focused debugging
- documentation updates
- AGENT_NOTES.md updates

## Mandatory routing rule

Sol High must not do bulk implementation when Luna High can do it.

Sol High should:
1. read the goal
2. create small tasks in TASKS.md
3. assign exactly one active task to Luna High
4. define files to start with
5. define tests to run
6. stop

Luna High should:
1. read the active task
2. implement only that task
3. run focused tests
4. update AGENT_NOTES.md
5. stop for review

## Standard workflow format

### Default context

At the start of every non-trivial task, read `AGENTS.md`, `TASKS.md`, and
`AGENT_NOTES.md`, and inspect the current git diff. Use targeted reads only;
do not dump large files or print long file excerpts.

### Sol format

- Sol plans and reviews only.
- Sol updates `TASKS.md` when creating, closing, or changing tasks.
- Sol updates `AGENT_NOTES.md` when recording planning or review decisions.
- During review, Sol writes exactly one of `REVIEW_STATUS: APPROVED`,
  `REVIEW_STATUS: FIX_REQUIRED`, or `REVIEW_STATUS: BLOCKED` in
  `AGENT_NOTES.md`.
- Sol stops after planning or review.

### Luna format

- Luna implements `ACTIVE_TASK` only.
- Luna inspects the current git diff before editing.
- Luna keeps changes minimal and does not broaden scope beyond `ACTIVE_TASK`.
- Luna updates `AGENT_NOTES.md` with files changed, tests run, evidence,
  blockers, and the next step.
- Luna stops for Sol review.

### Safety gates

- Do not run SUMO unless `ACTIVE_TASK` explicitly says so and the user has
  approved it.
- Do not start horizon warming unless `ACTIVE_TASK` explicitly says so and the
  user has approved it.
- Do not merge Stage B unless Sol review says it is approved.
- Do not weaken validation, provenance, recall, regret, failure-recall,
  release, or publication gates.
- Do not use diagnostic replay as release evidence.

### Copy-paste prompts

Sol planning:

```text
Plan the next task from ACTIVE_GOAL. Update TASKS.md and AGENT_NOTES.md. Stop.
```

Luna implementation:

```text
Perform ACTIVE_TASK only. Update AGENT_NOTES.md. Stop for Sol review.
```

Sol review:

```text
Review the current work. Write REVIEW_STATUS in AGENT_NOTES.md. Stop.
```

Luna fix after `FIX_REQUIRED`:

```text
Perform only the FIX_REQUIRED work from Sol review. Update AGENT_NOTES.md. Stop for Sol review.
```

## Escalation

Luna High must stop and escalate to Sol High if:
- architecture changes are needed
- auth, safety, data loss, provenance, or publication gates are affected
- a database or artifact contract changes
- more than two serious attempts fail
- the active task grows beyond its original scope

## Token rules

- Do not read the whole repository by default.
- Start with AGENTS.md, TASKS.md, AGENT_NOTES.md, ARCHITECTURE.md and IMPROVEMENT_PLAN.md.
- Use PROJECT_CONTEXT_OLD_AGENTS.md and CLAUDE.md only when more historical context is needed.
- Prefer targeted search over opening large files.
- Do not print full files unless required.
- Make minimal diffs.
- Do not change unrelated code.
- Record durable findings in AGENT_NOTES.md.
