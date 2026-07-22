#!/usr/bin/env bash
set -euo pipefail

MASTER_MODEL="${MASTER_MODEL:-gpt-5.6-sol}"
WORKER_MODEL="${WORKER_MODEL:-gpt-5.6-luna}"
APPROVAL_POLICY="${APPROVAL_POLICY:-on-request}"
SANDBOX_MODE="${SANDBOX_MODE:-workspace-write}"
MAX_ROUNDS="${MAX_ROUNDS:-1}"

echo "======================================"
echo " CODEX SOL/LUNA AUTO ORCHESTRATOR"
echo " Master:   $MASTER_MODEL"
echo " Worker:   $WORKER_MODEL"
echo " Approval: $APPROVAL_POLICY"
echo " Sandbox:  $SANDBOX_MODE"
echo "======================================"

if [ ! -f "AGENTS.md" ]; then
  echo "ERROR: AGENTS.md saknas. Kör från projektroten."
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: Detta är inte ett git-repo. Kör git init först."
  exit 1
fi

BRANCH_NAME="agent-auto-$(date +%Y%m%d-%H%M%S)"
git checkout -b "$BRANCH_NAME"

echo ""
echo "=== SOL HIGH: PLANERING ==="
codex exec \
  -m "$MASTER_MODEL" \
  -s "$SANDBOX_MODE" \
  "
You are Sol High, the master agent.

IMPORTANT OUTPUT RULES:
- Do not print long file excerpts.
- Do not use sed/cat to dump large files.
- Do not read more than 120 lines from any file.
- Prefer grep/find for headings and targeted facts.
- Edit only TASKS.md and AGENT_NOTES.md.
- Do not change implementation code.

Read only:
- AGENTS.md
- TASKS.md
- AGENT_NOTES.md

Use targeted search only if needed in:
- ARCHITECTURE.md
- IMPROVEMENT_PLAN.md
- SPEED_ARCHITECTURE_PLAN_2026-07.md

Tasks:
1. Read ACTIVE_GOAL in TASKS.md.
2. Break the goal into small tasks.
3. Write the tasks into TASKS.md.
4. Select exactly one ACTIVE_TASK for Luna High.
5. List files Luna High should start with.
6. List focused tests or commands Luna High should run.
7. Document any permanent decision in AGENT_NOTES.md or DECISIONS.md if needed.

Stop after planning.
"

for ROUND in $(seq 1 "$MAX_ROUNDS"); do
  echo ""
  echo "======================================"
  echo " ROUND $ROUND / $MAX_ROUNDS"
  echo "======================================"

  echo ""
  echo "=== LUNA HIGH: WORKER ==="
  codex exec \
    -m "$WORKER_MODEL" \
    -s "$SANDBOX_MODE" \
    "
You are Luna High, the worker agent.

IMPORTANT OUTPUT RULES:
- Do not print long file excerpts.
- Do not use sed/cat to dump large files.
- Do not read more than 120 lines from any file.
- Work only on the ACTIVE_TASK in TASKS.md.
- Make the smallest useful change.
- Do not change unrelated code.
- Update AGENT_NOTES.md.
- Stop if architecture, provenance, validation gates, data loss, or publication semantics are affected.

Read:
- AGENTS.md
- TASKS.md
- AGENT_NOTES.md

Then read only the specific files listed in ACTIVE_TASK.

Rules:
- Make the smallest useful change.
- Do not change unrelated code.
- Run focused tests if available.
- Update AGENT_NOTES.md.
- Mark status in TASKS.md.
- Stop if architecture, provenance, validation gates, data loss, or publication semantics are affected.

End with changed files, tests run, result, blockers, and next step.
"

  echo ""
  echo "=== GIT STATUS AFTER LUNA ==="
  git status --short

  echo ""
  echo "=== SOL HIGH: REVIEW ==="
  codex exec \
    -m "$MASTER_MODEL" \
    -s "$SANDBOX_MODE" \
    "
You are Sol High, the reviewer.

IMPORTANT OUTPUT RULES:
- Review only the current git diff.
- Do not print full files.
- Do not do bulk implementation.
- Edit only TASKS.md or AGENT_NOTES.md if needed.

Read:
- AGENTS.md
- TASKS.md
- AGENT_NOTES.md
- REVIEW_CHECKLIST.md

Review the current git diff only.

Do not do bulk implementation.

Tasks:
1. Check whether ACTIVE_TASK is satisfied.
2. Check whether validation gates and provenance rules are preserved.
3. Check whether unrelated files changed.
4. Check tests and risks.
5. If approved, write REVIEW_STATUS: APPROVED in AGENT_NOTES.md.
6. If a small fix is required, write REVIEW_STATUS: FIX_REQUIRED and create one small Luna task in TASKS.md.
7. If blocked, write REVIEW_STATUS: BLOCKED and explain why in AGENT_NOTES.md.
"

  if grep -q "REVIEW_STATUS: APPROVED" AGENT_NOTES.md TASKS.md 2>/dev/null; then
    echo ""
    echo "APPROVED by Sol High."
    break
  fi

  if grep -q "REVIEW_STATUS: BLOCKED" AGENT_NOTES.md TASKS.md 2>/dev/null; then
    echo ""
    echo "BLOCKED. Read AGENT_NOTES.md."
    break
  fi

  echo ""
  echo "No approval yet. Continuing if rounds remain."
done

echo ""
echo "=== FINAL STATUS ==="
git status --short
echo ""
echo "Branch: $BRANCH_NAME"
echo ""
echo "Next commands:"
echo "  git diff"
echo "  cat AGENT_NOTES.md"
echo "  cat TASKS.md"
