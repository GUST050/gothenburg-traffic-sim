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
