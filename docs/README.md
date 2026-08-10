# docs/

Dated one-off documents. They are a **historical record**: each was true when
written and is not maintained afterwards. Nothing here overrides the living
documents in the repository root.

## Where the authority is

| question | file |
| --- | --- |
| What is the program's structure? | `ARCHITECTURE.md` (source of truth) |
| Project context, data, rules | `CLAUDE.md` |
| How contributors collaborate | `AGENTS.md` |
| What to build next | `IMPROVEMENT_PLAN.md` (canonical plan) |
| Current focus / handoff | `TASKS.md`, `AGENT_NOTES.md` (marked blocks only) |
| Demand day-library speed design | `SPEED_ARCHITECTURE_PLAN_2026-07.md` |

Those stay in the root because code and tests reference them by path —
`IMPROVEMENT_PLAN.md` alone is named by 39 source files.

## Start here

`OPEN_ISSUES_2026-08-06.md` — everything known to be wrong, unresolved or
unproven, with where the evidence lives and whether each item is MEASURED,
DOCUMENTED or OPEN. Written as a handoff; read it before picking up the
warming, closure or demand work.

## history/

The append-only ledgers, split out on 2026-08-06. `AGENTS.md` says everything
outside the marked blocks is historical, and it was 99.5% of both files —
22,045 lines of which 118 were live. The marked blocks stay in the root files;
these hold the rest, unchanged.

| file | lines | split from |
| --- | --- | --- |
| `AGENT_NOTES_history.md` | 14,681 | `AGENT_NOTES.md` (14,736 → 62) |
| `TASKS_history.md` | 7,234 | `TASKS.md` (7,309 → 82) |

## reviews/

Point-in-time audits. Read for evidence and measurements, not for current
state; several findings have since been fixed or superseded.

| file | subject |
| --- | --- |
| `PIPELINE_FAULT_AUDIT_2026-08-06.md` | pool, picker and closure ranking; source of the C1 fix |
| `DEMAND_PIPELINE_REVIEW_2026-08-04.md` | trip pool and selection; findings P1-P4, S1-S4, B1-B2 |
| `WARMING_BUG_REVIEW_2026-08-04.md` | warm-arm rounding and join arithmetic |
| `PRE_WARMING_REVIEW_2026-08-04.md` | readiness check before the year run |
| `WARMING_SPEED_REVIEW_2026-08-03.md` | whether warming can deliver speed |
| `WARMING_SPEED_REVIEW_RESPONSE_2026-08-03.md` | verified disposition of the above |
| `WARMING_FINAL_AUDIT_2026-08-03.md` | warming audit |
| `FULL_CODE_AUDIT_2026-07-12.md` | repository-wide audit, P0/P1 findings |
| `IMPROVEMENT_REVIEW_2026-07-10.md` | improvement review |

## plans/

Dated plans and records. Superseded ones are kept because they explain why a
decision was made.

| file | status |
| --- | --- |
| `WARMING_PLAN_2026-08-05.md` | current warming plan and operations card |
| `CLOSURE_INTEGRITY_PLAN_2026-08-05.md` | closed out by `CLOSURE_INTEGRITY_STAGES_3_4_2026-08-10.md`. Stages 1-2 measured (premises revised/refuted), Stage 3 passes its paired real-SUMO gate, Stage 4/v10 is frozen and reproducible, 5 superseded by the warming plan |
| `CLOSURE_INTEGRITY_STAGES_3_4_2026-08-10.md` | current: what stages 3-4 implement, what it costs, and the two measurements that decide them |
| `DATA_REQUEST_2026-07.md` | NOT SENT. A record only — the no-more-external-data decision (2026-07-20) is permanent |
| `PROJECT_CONTEXT_OLD_AGENTS.md` | superseded by `CLAUDE.md` and `AGENTS.md` |
