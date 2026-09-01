# Enforce fastest sensor routes, then complete the sub-hour plan

Follow `.ai-flow/tasks/complete-subhour-plan.md`, which has been restructured
into one linear PRECONDITION / ACTION / EXIT plan. Phase P is new and blocks
Phase 3. Use the user's 2026-09-01 contract: sensor-attributed vehicles use a
globally fastest route and banning that sensor must make their fastest legal
route strictly slower.

## Why this run exists

Run `20260901-152555-95465` was stopped by the operator during `code-checks-02`,
before `code-review-02`, the freeze and any evidence. See its `OPERATOR-STOP.md`.
Its candidate code work is preserved in the worktree but remains unapproved by
the reserved verification review:

- `tools/ai_flow.py` + `tests/test_ai_flow.py`: the staged-v3 gate now requires
  TWO independently-invoked APPROVED reviews before any freeze or evidence (an
  APPROVED `code-review-01` defers, reruns checks and invokes `code-review-02`),
  a reversal by the reserved review stops the run BLOCKED with zero evidence and
  zero repairs spent, and each refreeze episode gets its own review budget via
  `code_review_episode_baseline` while `code_review_cycles` stays run-wide
  monotonic so review artifacts keep unique names.
- Earlier in the same run: the crash-consistent receipt repair, the continuous
  reused-plan provenance guard, and
  `test_staged_evidence_stops_on_reused_plan_provenance_drift_mid_run`.

Treat all of it as unapproved code: audit it, keep or correct each part on
evidence, and let this run's full review cycle judge it together with the new
work below.

## Phase P — the bounded work to complete

MEASURED read-only on 2026-09-01 against the registered case
`subhour-bounded-sumo-20260831-v38-01-0304fa08b47b` (edge
`8710974792_1759741980_0`, 2027-03-22 07:00-15:00, archive
`runs/demand-20260830-231746-c677eda8-e94d`, epoch 2027-03-21T00:00:00,
288 intervals). Reproduce it before changing anything; do not take it on trust.

1. `rs.closure_disruption` returns affected q10=89, q50=136, q90=140 with zero
   added vehicle-hours. For the 8 q10 OD pairs the fastest banned and free paths
   are equal, while fastest-banned minus the actual route is negative for all 89
   vehicles. These routes are invalid under the user's chosen contract.
2. `build_candidates.py` intentionally admits forced-via sensor routes within
   `max(45 seconds, 20 percent)` of shortest, and grounded support accepts a
   max-stretch route. This is the upstream cause; changing the subtraction
   alone cannot repair it.
3. Implement the complete Phase P contract in the canonical plan: versioned
   strict candidate qualification, per-sensor/per-period feasibility audit,
   strict-shortest plus strictly-positive banned-edge gap at every route source,
   revalidation of emitted PFE routes and exact sensor counts, full provenance,
   fresh catalog/demand/evidence identities, and fail-closed
   `INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT` when the observed demand cannot be
   represented honestly.
4. Preserve `closure_cost_v1` only if it is proven to use exactly the same graph,
   endpoints and costs as the qualification audit. Never use actual-route
   subtraction or manufacture a positive delta. The positive value must arise
   from a qualified shortest baseline and a genuinely slower legal reroute.
5. Also complete the deterministic all-variant reconciliation diagnostic and
   any real direct/index/oracle/runner consistency repair described by the plan.

Existing candidates, demand archives, caches, registrations and outcomes are
incompatible with the new demand contract. Preserve them and the interrupted
generation-1 artifacts of `20260901-123040-80853` append-only; never promote,
overwrite or silently relabel them.

## Then

Before `CODE_APPROVED`, finish every possible source edit required by the
corrected plan. In particular, keep the existing `WindowCostIndex` implementation
inside Phase C and implement/test the exact Phase 6 eligibility predicate that
admits a complete, trustworthy `INCONCLUSIVE_PERFORMANCE_GATE` but rejects every
partial, unmeasured or untrusted inconclusive terminal. Apply the same predicate
to both `tools/ai_flow.py` and the full-month registration/validation path in
`run_monthly_closure_search.py`, with cross-path tests. Also bind controller-
derived Phase 0-2 status to approved checks/manifests instead of producer prose.

After a CODE_APPROVED freeze under Phase C's two-review rule, execute Phase D's
support audit and fresh demand qualification. Continue autonomously through
Phases 0-3, Phase 4, conditional Phase 5 execution, the checkpoint review,
conditionally allowed Phase 6, Phase 7 and the exact `phase_0`-through-`phase_7`
terminal report only if Phase D passes. A truthful
`INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT` ends the evidence path as NOT_ALLOWED. No
source repair is allowed under the freeze; it must stop and enter a fresh
bounded code-review episode.

Worker and fixer are Claude Sonnet High; planner and independent reviewer are
Codex Sol High. Preserve unrelated dirty changes and all historical artifacts.
Do not weaken scientific, provenance, source-freeze, resource, routing, health,
publication or review gates. Do not delete, commit, push or deploy.

Run `20260901-161639-1291` is a terminal BLOCKED diagnostic with zero evidence
generations. Do not resume it after these task/plan bytes change. Start any
implementation as a fresh staged-v3 run with a fresh initial inventory.
