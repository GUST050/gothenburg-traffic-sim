# Sub-hour closure search — executable plan

One linear plan. Each phase states its PRECONDITION, its ACTION, its EXIT and
what INCONCLUSIVE legitimately means there. Nothing in this file refers to a
superseded run; run history belongs in the run directories, not in the plan.

## Standing rules (apply to every phase)

* The repository and its local archives are the only permitted inputs. Deriving
  fixtures, manifests, registrations, fresh output roots and resource caps is
  part of the work, never an operator question.
* `BLOCKED` is only for a genuinely missing external file or credential, a
  necessary destructive action, or a material user choice the preregistered
  rules cannot settle. Missing code, tests or inputs are work.
* A truthful `INCONCLUSIVE_*` or a missed 30% gate is a RESULT. Report it and
  continue with every later phase that is still scientifically permitted.
* Never change scientific thresholds, timeouts, routing, teleport, health,
  recovery, provenance or frozen artifacts to obtain a green result.
* Preserve unrelated dirty files and all historical evidence. No delete, no
  commit, no push, no deploy.

## Phase C — code stabilization (precedes all evidence)

PRECONDITION: none.
ACTION: implement code, tests, manifests and deterministic tooling only. Create,
freeze, execute or modify NO evidence registration or outcome. Phase P below is
part of THIS phase, not a stage after it: its code change is written here, is
covered by these tests and is judged by these two reviews. Phase C also contains
every controller change needed by the later phases, including the exact Phase 6
eligibility predicate and the exact Phase D terminal-to-report mapping.
`WindowCostIndex` must already be implemented and tested here; Phase 5 may later
decide whether to run its full oracle, but may not edit source after the freeze.
Phase C is green only once all of this is approved.
EXIT: configured checks pass, then an all-findings review AND exactly one
reserved verification review both return APPROVED. A non-APPROVED verification
review stops the run before evidence and must not be followed by an unreviewed
fixer. At most one complete findings repair batch is permitted, so known
defects must not be deferred to the reviewer.

## Phase P — unblock Gate S (inside Phase C; blocks Phase 3)

USER-CHOSEN SCIENTIFIC CONTRACT (2026-09-01): a vehicle attributed to a
measured sensor must use a globally fastest legal OD route under the SAME edge
costs, endpoint convention and graph as the closure scorer. For a vehicle to be
eligible for a closure-cost experiment on that sensor, removing the sensor edge
must make its fastest legal OD route strictly slower. It is not enough that the
chosen route happens to cross the sensor, and it is not enough that the sensor
lies on only one of several equal-cost shortest paths.

MEASURED 2026-09-01 on registered case
`subhour-bounded-sumo-20260831-v38-01-0304fa08b47b` (edge
`8710974792_1759741980_0`, 2027-03-22 07:00-15:00, archive
`runs/demand-20260830-231746-c677eda8-e94d`):

1. The fastest banned path equals the fastest free path for all 8 affected OD
   pairs, while fastest-banned minus each actual q10 route is -67.6 to -40.6
   seconds. Those 89 sensor-crossing routes therefore violate the chosen
   baseline-route contract and are not admissible evidence for this study.
2. The root cause exists upstream: `build_candidates.py` deliberately accepts
   forced-via sensor routes up to `max(45 seconds, 20 percent of direct)` above
   the global shortest route, and `grounded_sensor_basis_route` permits up to
   `DEFAULT_MAX_STRETCH`. PFE can assign measured flow to those shapes.
3. The current `closure_cost_v1` subtraction - fastest route with the edge
   banned minus fastest route with it available - is the correct counterfactual
   once the demand invariant is true. Do not replace it with actual-route
   subtraction and do not clamp or manufacture a positive number.

The Phase P repair is therefore an upstream demand/candidate qualification plus
the existing downstream consistency repair:

1. Introduce a versioned `sensor_shortest_positive_gap` candidate/demand policy.
   For each candidate vehicle route attributed to sensor edge `e`, compute on
   the production routing graph:
   `actual_route_cost`, `shortest_free_cost` and `shortest_without_e_cost`.
   Require the actual route to be edge-legal, contain `e`, and satisfy
   `abs(actual_route_cost - shortest_free_cost) <= tolerance`. Also require
   `shortest_without_e_cost` to be finite and
   `shortest_without_e_cost - shortest_free_cost > tolerance`. Use one declared
   absolute/relative floating-point tolerance everywhere; the strict positive
   gap must exceed that tolerance. Apply this to every `(vehicle, measured
   sensor)` incidence when one route crosses more than one measured edge.
2. Apply the rule at EVERY route-producing path, including bounded-detour
   naturalness masks, verified gate pairs and `grounded_sensor_basis_route`.
   A permissive route may remain available for a different, explicitly named
   traffic-purpose model, but it must never enter the closure-study candidate
   catalog or calibrated demand. No fallback may silently restore the 45-second/
   20-percent or max-stretch allowance.
3. Before rebuilding demand, run a read-only support audit for every measured
   directed sensor and each required demand period. Record eligible OD pairs,
   route diversity, strict gap distribution, sensor-incidence matrix rank and
   whether exact quarter-hour q10/q50/q90 PFE counts remain feasible. Freeze the
   audit inputs and deterministic ordering. If support is insufficient, return
   `INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT` with the deficient sensors/periods;
   never synthesize background traffic, relax exact counts, merge directions or
   weaken the shortest/positive-gap predicates.
4. Rebuild candidates and q10/q50/q90 demand only after the support audit passes.
   PFE publication must re-audit the EMITTED routes, not merely the candidate
   pool: every vehicle counted against a sensor must satisfy route legality,
   global-shortest equality and the strict banned-edge gap for that sensor.
   Require exact sensor counts, existing endpoint/provenance gates and a
   binding from every published vehicle to exactly one qualified candidate
   record; a qualified candidate may be reused by multiple vehicles.
5. Version the candidate catalog, demand metadata and routing policy; bind the
   network, routing-cost, measured-edge, audit and route-file digests. Existing
   catalogs, demand archives, caches, registrations and outcomes are
   incompatible with this successor demand contract. Preserve them append-only
   and use fresh IDs/roots; never relabel or regenerate them in place.
6. Retain `closure_cost_v1` only if focused equivalence tests prove the scorer's
   graph, endpoint and cost semantics are byte-for-byte bound to the new demand
   audit. Otherwise introduce a successor cost/policy identity before evidence.
   In either case, assert for every affected emitted vehicle that its recorded
   disruption is greater than tolerance and equals
   `shortest_without_e_cost - shortest_free_cost` within tolerance.
7. Make `reconcile_disruption` collect every field mismatch for every variant
   and raise one deterministically ordered error. Preserve separate fail-closed
   variant-set validation. Repair any real ledger/runner disagreement
   consistently across direct, parsed/index, retained-oracle and runner paths.
8. Reproduce the historical ledger/runner disagreement only as a bounded
   diagnostic in a fresh scratch root. It may explain old artifacts but cannot
   qualify the old demand or become release evidence under the new contract.

REQUIRED REGRESSIONS: reject a sensor route that exceeds the global shortest
cost; reject a shortest route when an equal-cost sensor-avoiding alternative
exists; accept a legal shortest sensor route only when banning the sensor has a
strict positive gap; reject an unqualified grounded-support fallback; fail the
support audit without relaxing policy; prove emitted q10/q50/q90 vehicles and
exact counts against the audit; preserve the existing exact detour arithmetic;
keep direct, parsed/index, retained-oracle and runner-adapter records
field-identical; and list simultaneous q10/q50/q90 reconciliation mismatches.

HISTORICAL CLASSIFICATION, stated so it is performable: published evidence is
APPEND-ONLY and Phase C forbids modifying any evidence artifact, so no existing
outcome may be edited, relabelled or recomputed in place. Classify old artifacts
in the final report by exact reason: source-stale, incomplete, failed
reconciliation, diagnostic-only or incompatible-demand-policy. No pre-change
closure outcome qualifies as current evidence for the new route contract.

## Phase D — qualified demand freeze (after Phase C; blocks Phases 0-3)

PRECONDITION: Phase C has an approved source manifest containing the audit,
candidate, demand, validation and scorer code. No source edit is allowed under
that manifest.
ACTION: run the frozen support audit first. If and only if it passes, build the
successor catalog and q10/q50/q90 archives in fresh roots, then validate every
emitted `(vehicle, measured sensor)` incidence and exact sensor target against
the frozen audit. Bind all inputs, outputs, policy versions and digests in one
append-only qualified-demand manifest. This is input qualification, not Phase 3
performance or release evidence.
EXIT: PASS only with a complete qualified-demand manifest. Insufficient support
or exact-fit infeasibility exits `INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT`; any
schema, digest, route, count or semantic mismatch exits a precise fail-closed
INCONCLUSIVE. Either terminal leaves Phases 0-3 and all later evidence
`NOT_ALLOWED`. A required source repair starts a fresh Phase C review/freeze.

## Phases 0-2 — controller-derived code prerequisites

PRECONDITION: Phase D is PASS and its qualified-demand manifest matches the
approved source and all consumed inputs.

These phases exist in the terminal `phase_0`-through-`phase_7` schema and must
not be asserted as unexplained PASS placeholders:

* `phase_0`: the approved source, exact search space, policy, q variants,
  tie/finalist rules, timeout/capacity/budget terminals, fresh-root rule and
  55-minute work plus 5-minute publication budget are digest-bound, including
  the Phase D qualified-demand manifest.
* `phase_1`: early-stop and ordered-exhaustive use the same ledger, order,
  verifier, attempt identity, failures/health, reconciliation and cursor. The
  only arm difference is `disable_early_stop`.
* `phase_2`: the deterministic early-boundary, backfill, no-detour, inclusive
  tie, capacity, timeout, cancel/resume, corruption/swap, secondary/tertiary
  ordering and no-viable suite passes.

The controller must derive these PASS states from `CODE_APPROVED`, frozen
manifests and completed checks. Producer prose is not sufficient. Failure of
any of them stops the run before Phase 3 evidence.

## Phase 3 — bounded paired real-SUMO evidence

PRECONDITION: Phases C and P are green and reviewed, Phase D is PASS, and
controller-derived Phases 0-2 are PASS for the same `CODE_APPROVED` bytes and
qualified-demand manifest.
ACTION: freeze the selection BEFORE reading any outcome:
1. Inventory only archive/request/demand/network metadata and search specs.
   Do not read `validation/cost_ordered_benchmark_outcome_*.json`.
2. Sort deterministically on SHA-256 of the outcome-free tuple
   (demand period, directed edge, date, window, search content key).
3. Select stratified: at least 8 cases over at least 4 directed edges and 2
   demand periods. Bind the rule, the eligible-list digest and selected IDs.
4. If backfill, no-detour, dense-boundary or restart cannot be identified
   without an outcome, declare a symmetric fixture in the registration BEFORE
   running and apply it identically to both arms.
5. Use fresh append-only evidence IDs and fresh workspace/cache/output roots.
   Never reuse a v1-v5 outcome root. Bind source, input, runtime and policy
   digests plus attempt, active-time, RSS and disk caps.
Then run only the registered paired suite, identical in everything but
early-stop versus ordered-exhaustive mode, and evaluate every correctness,
ledger-equality, restart, resource and 30% gate mechanically.
EXIT: PASS, or a truthful `INCONCLUSIVE_*`. A performance miss must not erase
an otherwise complete and trustworthy decision population.

## Phase 4 — cold SUMO-free ledger profile

PRECONDITION: Phase 3 has produced a terminal artifact (PASS or INCONCLUSIVE)
and the source freeze still matches. This profile is scientifically independent
of whether Phase 3 met its performance target.
ACTION: profile exactly 1 950 daily units, 3 variants, 5 850 daily-variant
records and 1 690 parents from a cold cache. Record XML parse, grouping,
shortest-path/detour, aggregation, parent reduction/sort, total active time,
cache behaviour, process-tree RSS, disk growth and the observed SUMO-start
delta.
EXIT: PASS only for the exact complete population, complete phase timing and
zero SUMO launches; otherwise a precise INCONCLUSIVE terminal.

## Phase 5 — WindowCostIndex (conditional)

PRECONDITION: Phase 4 measured.
ACTION: the implementation is already part of the Phase C source freeze. Run
its full oracle only if cold ledger time exceeds 10 minutes or 20 percent of the
registered end-to-end budget. If triggered, require all 5 850 records to be
field-identical on affected/no-detour counts, time, metres, refusals and tie
fields, bound to the frozen inputs, and measure cold build plus query time. A
required source repair stops this generation and returns to a fresh Phase C
review/freeze; it is never made underneath the existing freeze.
EXIT: `PASS` or `NOT_TRIGGERED`. A missing or differing required oracle record
is INCONCLUSIVE, never PASS.

## Checkpoint review

PRECONDITION: Phases 3-5 have outcomes.
ACTION: freeze the complete Phase 3-5 inventory, rerun source-frozen checks,
persist a digest-bound checkpoint and obtain an independent read-only review
BEFORE any Phase 6 or 7 registration. No Phase 6/7 artifact may already exist
in this generation, and reviewer prose alone is not approval.
EXIT: persist a PASS review bound to the checkpoint bytes, or publish the
mechanically valid statuses and leave Phase 6 `NOT_ALLOWED`. A scientific
INCONCLUSIVE is not a workflow blocker.

## Phase 6 — full month (conditional)

PRECONDITION: both the controller terminal gate and the full-month registration
builder/validator implement and test this exact predicate during Phase C,
before `CODE_APPROVED`. They must consume one shared predicate or have explicit
cross-path equivalence tests so one cannot admit evidence the other refuses:

* phases 0-2 and 4 are PASS;
* Phase D is PASS and its qualified-demand manifest matches every consumed
  archive;
* phase 5 is PASS or NOT_TRIGGERED;
* the Phase 3-5 checkpoint review is PASS and matches `CODE_APPROVED`; and
* Phase 3 is PASS, OR its exact producer terminal is
  `INCONCLUSIVE_PERFORMANCE_GATE` with `decision_population_complete=true`, a
  complete q10/q50/q90 Gate S population, every registered case complete, and
  every correctness, restart, evidence-integrity and resource-cap gate true.

No generic INCONCLUSIVE is eligible. Process-census loss, workspace busy,
source drift, budget/cap exhaustion, partial execution and a preflight that ran
zero cases all make Phase 6 `NOT_ALLOWED`. Eligibility must be rederived from
the reviewed Phase 3 producer bytes, not copied from the normalized
`phase_3=INCONCLUSIVE` status label or model prose.

ACTION: register and run one fresh isolated month. Bind it before starting.
Hold 55 minutes of active work plus a 5-minute publication reserve. Stop new
starters at exhaustion. On budget failure publish only
`INCONCLUSIVE_BUDGET_EXHAUSTED`. The post-commit receipt is the authoritative
deadline and telemetry source. Never fall back to exhaustive execution.
EXIT: `PASS`, `INCONCLUSIVE` or `NOT_ALLOWED`, backed by a receipt-bound,
append-only terminal that is truthful about budget. Phase 6 is additionally
`NOT_ALLOWED` unless every consumed demand archive passed the versioned
sensor-shortest/positive-gap audit under the frozen source and network.
A month following a bounded performance-only miss is diagnostic evidence for
the operational under-60-minute goal; it does not retroactively pass either
structural 30-percent gate.

## Phase 7 — Gate S, the q-policy decision

PRECONDITION: Phase D passed and bounded and/or full-month evidence exists with
a COMPLETE q10/q50/q90 decision population. The pre-change Gate S report was
starved by incomplete source-derived decisions, but it cannot be reused because
its demand did not satisfy the new route contract.
ACTION: freeze and run Gate S from its own registration, separate from the
evidence run.
DECISION RULE (frozen): activate q50-only ONLY with zero decision regret,
identical finalists and `variant_unique_failure_recall == 1.0`.
EXIT: `phase_7=PASS` with decision `q50_only` or `ROBUST_THREE_VARIANT`,
`phase_7=INCONCLUSIVE`, or `phase_7=NOT_TRIGGERED` when no eligible complete
population exists. All are valid scientific answers. Change UI/API naming only
if the preregistered policy and the review support it.

## Final report

One report whose phase keys are EXACTLY `phase_0` through `phase_7`, as
`_PHASE_REPORT_PHASES` in tools/ai_flow.py requires. Phases C and P are code
stabilization and Phase D is input qualification; they have no phase_N key of
their own. Record their exact terminal and qualified-demand manifest (if any)
inside `phase_0`; a non-PASS Phase D makes phases 0-7 NOT_ALLOWED with its exact
reason. Each permitted phase is PASS, INCONCLUSIVE, NOT_TRIGGERED or NOT_ALLOWED,
with all new evidence IDs, exact SUMO attempts, active time,
cold-ledger time, RSS, disk, and whether a full month reached READY within 60
minutes. Include a hash-bound historical-artifact classification by exact
reason: source-stale, incomplete, failed reconciliation, diagnostic-only,
incompatible-demand-policy or still valid. Under the user-chosen contract no
pre-change closure outcome is current evidence. Then run final source/inventory
checks and obtain an independent APPROVED review bound to the report. Update
only the current coordination blocks in TASKS.md and AGENT_NOTES.md with
measured evidence. The workflow terminates truthfully even when a later phase
is INCONCLUSIVE, NOT_TRIGGERED or NOT_ALLOWED.
