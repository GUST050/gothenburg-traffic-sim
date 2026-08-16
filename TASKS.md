# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Make the repository easier and safer for Codex, Claude and
  human contributors to navigate, change and verify.`
- Status: `IMPLEMENTED — concise layered agent instructions, a compact
  architecture map, stable domain test commands, AI evaluation tasks, large
  artifact enforcement and an evidence-aware modularization plan are in place.
  Focused quality checks pass; source-bound trial extractions were reverted.`
- Suggested next action: `Review the pushed AI-structure change, choose Git LFS
  or durable external storage for the three pinned legacy artifacts, then
  resume Gate D from the unchanged daily TOT split plan.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Stable root CLI paths, scientific contracts, frozen
  evidence and unrelated historical records remain unchanged. Large legacy
  artifacts are pinned, not silently migrated; no validation gate may be
  weakened to make CI pass.`
- Updated: `2026-08-16 Codex — user reprioritized the current focus to
  AI-friendly repository structure and requested commit/push for Claude.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### AI-REPO-STRUCTURE-1 — Layered context and repeatable verification

- Status: `IMPLEMENTED AND VERIFIED — focused checks pass, trial changes to
  source-bound product files were reverted, and publication is in progress.`
- Objective and scope: `Reduce agent context load, make ownership and commands
  discoverable, prevent accidental artifact growth and map a safe path for
  shrinking large entry points without breaking public or evidence contracts.`
- Completion outcome: `Root and nested AGENTS/CLAUDE guidance, concise
  architecture and artifact policy, stable Make targets, CI lint/hygiene/
  contract checks, representative AI evals and evidence-aware refactoring
  priorities.`
- Context or checkpoints: `Root AGENTS.md is 100 lines and CLAUDE.md imports it;
  detailed collaboration and historical Claude material moved under docs.
  Legacy tracked artifacts over 5 MiB are digest-pinned because Git LFS is not
  installed and shared storage has not been selected.`
- Primary files: `AGENTS.md; CLAUDE.md; docs/ai/;
  docs/architecture/OVERVIEW.md; Makefile; pyproject.toml; CI; domain agent
  files; tools/check_repo_hygiene.py and its allowlist/test.`
- Constraints and safety: `Preserve root CLI/import compatibility, frozen
  evidence and scientific validation behavior. Do not migrate or delete large
  artifacts without an explicit shared-storage decision.`
- Acceptance criteria: `Nearest-file instructions describe real invariants;
  documented commands run; CI detects undefined names and unapproved large
  files; focused tests have no regressions; another actor can orient from the
  short docs; source-bound modules stay byte-identical.`
- Useful checks: `make check; domain Make targets; full pytest comparison;
  git diff --check; clean status after commit and push.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
