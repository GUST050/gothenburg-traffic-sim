# Codex Sol/Luna Agent Router

## Sources of truth

Each concern has one authority:

- `AGENTS.md`: stable Sol/Luna protocol, roles, transitions, and safety rules.
- The single marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks in `TASKS.md`:
  current workflow state and current task contract.
- The single marked `CURRENT_HANDOFF` block in `AGENT_NOTES.md`: current
  planning, execution, or review evidence.
- `ARCHITECTURE.md`: program structure.
- `IMPROVEMENT_PLAN.md`: improvement priorities.

In `TASKS.md` and `AGENT_NOTES.md`, everything outside the three marked blocks
is historical or supporting context, not current task/state authority and not
default startup context. `PROJECT_CONTEXT_OLD_AGENTS.md` and `CLAUDE.md` are
historical context. If architecture or priority documents disagree with
history, the named authority wins.

## Roles and routing

Sol High owns planning, prioritization, architecture and risk decisions, task
decomposition, approval recording, final review, and task closure. Luna High
owns implementation of the current task, focused tests and debugging,
required task documentation, and the implementation handoff. Sol must
delegate implementation to Luna as one cohesive, reviewable delivery slice
when the work fits a bounded contract. A slice includes all tightly coupled
implementation layers, focused debugging, tests, and documentation needed for
one user-visible or evidence-visible outcome; Sol must not split it merely by
file or implementation layer.

There is exactly one marked `ACTIVE_TASK` block and one task ID/revision in
flight. Sol plans or reviews only. Luna implements or fixes only the task and
revision named by `WORKFLOW_CONTROL`, then stops for Sol review.

## Delivery size and Luna autonomy

Every task declares exactly one delivery size:

- `NARROW`: boundary discovery when risk or uncertainty makes implementation
  scope unsafe to predict. Its completion outcome is a reviewable decision or
  evidence package, not an arbitrary implementation fragment.
- `STANDARD`: the default complete vertical slice, including its tightly
  coupled implementation, focused debugging, tests, and documentation.
- `EXTENDED`: a larger but still cohesive outcome. Sol defines explicit
  internal checkpoints so Luna can verify direction and evidence while
  continuing without intermediate handoffs.

Changing delivery size never expands approval, allowed-file, safety, release,
publication, architecture, or artifact-contract authority. There is still one
active task and one final Sol review gate; `EXTENDED` checkpoints are internal
execution checks, not extra workflow states or implicit approval gates.

Within the active contract, Luna continues autonomously through the complete
slice. Luna may inspect relevant files, make reasonable local implementation
choices, run the authorized focused checks, diagnose failures caused by its
changes, repair them in scope, and rerun those checks. Routine repository-
discoverable questions, implementation substeps, and check failures do not
require an intermediate return to Sol.

Luna hands off only when one terminal condition is met:

1. The entire completion outcome and all acceptance criteria pass.
2. An external approval or authority boundary is reached.
3. An architecture or artifact contract must change.
4. Scope must materially expand beyond the active contract.
5. Three distinct serious implementation approaches have failed with recorded
   evidence.

Searching, reading, ordinary diagnosis, and one retry of the same check are
not serious failed approaches. A blocked handoff must state the exact blocker,
evidence, attempted approaches, remaining safe options, and the recommended
next decision for Sol. Luna must not report only that it is stuck, wait for
routine clarification discoverable from the repository, or broaden scope
silently.

## Startup fast path

For every non-trivial `SOL PLAN`, `LUNA DO`, `LUNA FIX`, or `SOL REVIEW`:

1. Read all of `AGENTS.md`.
2. Read only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks in
   `TASKS.md` and the marked `CURRENT_HANDOFF` block in `AGENT_NOTES.md`.
3. Run `git status --short`.
4. Inspect targeted diffs for the active task's allowed files. Treat all
   unrelated changes as user-owned and preserve them.
5. Validate that every current marker occurs exactly once, and that task ID,
   revision, state, owner, next action, transition metadata, and approval
   fields agree.

Read `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, or historical task/evidence
sections only when the active task names them or the current decision requires
them. Use targeted searches and excerpts; never load a growing ledger merely
to discover current state.

If a marker or required field is missing or duplicated, IDs/revisions
conflict, the state/action/role combination is illegal, or scope and approval
cannot be proven, stop fail-closed. Do not implement, run checks with side
effects, repair state speculatively, or use history to resolve the conflict;
report the mismatch to Sol.

## Workflow state machine

Only these transitions are legal:

| Command / actor | Required state | Resulting state | Next action |
|---|---|---|---|
| `SOL PLAN` / Sol | `READY_FOR_SOL_PLAN` | `READY_FOR_LUNA` or `BLOCKED` | `LUNA DO` or exact unblock condition |
| approval record / Sol | `BLOCKED` | `READY_FOR_LUNA` | `LUNA DO` |
| `LUNA DO` / Luna | `READY_FOR_LUNA` | `READY_FOR_SOL_REVIEW` | `SOL REVIEW` |
| `SOL REVIEW` / Sol | `READY_FOR_SOL_REVIEW` | `READY_FOR_SOL_PLAN`, `FIX_REQUIRED`, or `BLOCKED` | `SOL PLAN`, `LUNA FIX`, or exact unblock condition |
| `LUNA FIX` / Luna | `FIX_REQUIRED` | `READY_FOR_SOL_REVIEW` | `SOL REVIEW` |

`READY_FOR_SOL_PLAN` means the marked task is concluded and non-executable;
Sol replaces it when planning the next revision or task. `BLOCKED` is
non-executable. No other actor, command, state, or transition is implied.

Every legal transition atomically writes `State`, `Next action`, and
`Transition`, where `Transition` is `<actor> / <command> / <YYYY-MM-DD>` and
describes the operation that produced the current state. A missing, stale, or
partially updated member of this triple is conflicting state and fails closed.

Field ownership is strict:

- Sol owns the active task contract: ID, revision, owner, scope, allowed
  files, forbidden work, acceptance criteria, checks, approval gate, and
  escalation conditions.
- Sol owns all `WORKFLOW_CONTROL` fields during planning/review. Luna may
  change only the atomic `State`, `Next action`, and `Transition` triple, and
  only for a valid terminal `READY_FOR_LUNA`/`FIX_REQUIRED` to
  `READY_FOR_SOL_REVIEW` handoff explicitly required by the active task. That
  handoff may report completed work or a terminal blocker; Sol decides the
  resulting review state. `Owner` is the assigned implementation owner, not
  the actor named by `Next action`; Luna cannot change any other Sol-owned
  control field.
- The acting role replaces `CURRENT_HANDOFF`: Sol for planning/review and Luna
  for implementation/fix. A durable dated entry may be added below it; old
  entries are never rewritten.

Any scope or contract change requires Sol to increment the task revision and
issue a fresh handoff. Luna must match the task ID and revision across all
three current blocks before acting. Evidence or approval for another ID,
revision, scope, content key, or state is invalid.

## Compact schemas

Sol uses this bounded active-task schema:

```text
<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK
### <TASK-ID> — <title>
- Revision / owner / status / delivery size
- Objective and scope (maximum 120 words)
- Completion outcome
- Internal checkpoints (`EXTENDED` only; otherwise `NOT_APPLICABLE`)
- Allowed files
- Forbidden work
- Acceptance criteria
- Focused checks
- Approval gate: NOT_REQUIRED, or REQUIRED with exact scope/key, exact quoted
  user message, user-message date, and Sol recorder/date
- Terminal handoff conditions
<!-- ACTIVE_TASK_END -->
```

The acting role uses this bounded handoff schema:

```text
<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF
- Task / revision / state / transition / owner
- Files changed
- Checks: exact command and PASS/FAIL/NOT_RUN
- Evidence (maximum five bullets; no raw logs)
- Approval: NOT_REQUIRED, or matched scope/key/message/date
- Blockers: none, or exact blocker / evidence / attempted approaches /
  remaining safe options / recommended next decision for Sol
- Next action
<!-- CURRENT_HANDOFF_END -->
```

Summaries are at most 120 words and evidence at most five bullets. Commands
and failures may link to a dated history entry, but history below the block is
never required startup context.

## Approval and safety gates

Work needing user approval remains `BLOCKED` until Sol records, in the active
task, the exact user message, exact authorized scope or immutable key, the
message date, and Sol's recorder/date. Luna must match all fields and the
current task ID/revision before any preflight or execution covered by the
gate. Approval may never be inferred, reused from another task/revision/key,
expanded, or applied retroactively. `SOL PLAN`, `LUNA DO`, and review commands
are not approval.

- Do not run SUMO unless the active task explicitly authorizes it and the
  exact required approval record matches.
- Do not create or inspect outcomes unless the active task explicitly
  authorizes it and the exact required approval record matches.
- Do not start demand generation or horizon warming unless the active task
  explicitly authorizes it and the exact required approval record matches.
- Do not merge Stage B unless Sol review explicitly approves it.
- Do not weaken validation, provenance, recall, regret, failure-recall,
  release, or publication gates.
- Diagnostic replay is never release evidence.

## Command aliases

- `SOL PLAN`: validate the fast path; create exactly one cohesive delivery-
  slice contract, using `STANDARD` by default; set `READY_FOR_LUNA` or
  `BLOCKED`; write the current handoff; stop.
- `LUNA DO`: require `READY_FOR_LUNA`; implement only the matching task;
  complete its authorized implementation/debug/check/documentation loop;
  write one terminal handoff; set `READY_FOR_SOL_REVIEW`; stop.
- `LUNA FIX`: require `FIX_REQUIRED`; fix only blockers in the matching current
  handoff; autonomously complete the authorized repair/check loop; hand off
  once for review; stop.
- `SOL REVIEW`: require `READY_FOR_SOL_REVIEW`; review only the allowed diff
  and recorded evidence; write exactly one `REVIEW_STATUS: APPROVED`,
  `REVIEW_STATUS: FIX_REQUIRED`, or `REVIEW_STATUS: BLOCKED`; transition per
  the state table; stop.

## Luna escalation

Luna stops and escalates without broadening scope only at the terminal handoff
conditions above. Auth, safety, data loss, provenance, publication, database,
approval, or state conflicts are authority boundaries and therefore always
terminal. At three failed serious approaches, Luna records the required
blocker evidence and safe options instead of attempting a fourth approach.

External workers such as Claude may act as Luna only when they follow this
contract. Sol retains review and approval authority.
