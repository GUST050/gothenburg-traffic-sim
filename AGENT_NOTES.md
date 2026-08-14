# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/gate-s-critical-findings-brkzye, continuing
  claude/dirsplit-gated-plan-v2. Review found both gates technically wrong and
  the 107 anchor disconnected from the product. All three are repaired.
  Gate S = NOT_RUN (external egress policy). Gate M = INCONCLUSIVE. The
  previously published Gate M = BASELINE is WITHDRAWN as a decision. No exit
  declared.`
- Summary: `Three critical and three high findings were addressed. (1) The
  Gate S tool never varied the route file or the seed, read a disruption field
  the product does not emit, ignored its own registered closure window, and
  reduced a private objective instead of the deployed decision fields — all
  four fixed, with the (case, seed) pair now bound through a one-seed
  ScenarioSpec and the decision taken by closure_ranking unchanged. (2) The
  107 anchor was a helper no product caller invoked; build_targets now takes
  anchor_day/anchor_epoch and all three build_sumo_demand call sites pass the
  build's own date, with the annual D-factor matched VOLUME-WEIGHTED from the
  same flows. (3) Gate M ran on the wrong population and a model that is not
  the deployed one, under code that did not implement its own frozen rule; the
  rule is now simplest_defensible_v2 and the gate returns INCONCLUSIVE because
  blocked_date folds cannot be built from the aggregated table.`
- Files changed: `tools/measure_direction_decision_sensitivity.py (rewritten
  run path and reducer, protocol v2); dirsplit/evaluate.py (deployed
  population screen, deployed LightGBM, pairwise comparisons, required fold
  kinds, rule v2); demand/intake.py (anchor_day/anchor_epoch on build_targets,
  sensor_period_weights, flat-base anchoring); build_sumo_demand.py (three
  build_targets call sites); tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_gate_m.py, tests/test_sensor_107_directional_reference.py;
  data/dirsplit/gate_m_report.json (regenerated);
  validation/dirsplit_gate_m_outcome_v2.json (new, supersedes v1);
  validation/dirsplit_direction_sensitivity_blocker_v2.json (new, supersedes
  v1). No v1 evidence file was edited.`
- Checks: `Full suite run with sumo/net.net.xml present. The three touched
  suites: 49+2s (sensitivity), 59 (gate M), 64 (sensor 107).`
- Decisions and evidence: `GATE M = INCONCLUSIVE, not BASELINE. Rule 6 of the
  frozen text says a fold kind that could not be built is INCONCLUSIVE;
  dirsplit/dataset.py aggregates away local_date, so blocked_date yields zero
  folds and the v1 code skipped it and published BASELINE anyway. Under the
  corrected DEPLOYED population (observed weekday-daytime share screen: 81
  stations / 3,665 rows / 162 blocks, not the OSM-oneway-flag 39 stations /
  1,514 rows) and the deployed LightGBM (target-centred kernel, n_obs**0.5
  evidence weight, weekday 06-20 fit, shrinkage fit by nested leave-city-out),
  the LightGBM is worse than 50/50 on leave-city-out (CI95 [+0.000768,
  +0.004979]) and indistinguishable on leave-station-out. That is DIAGNOSTIC
  only. The claim "the deployed LightGBM is 31.6-39.2% worse" is withdrawn:
  it measured a different model on a different population.
  GATE S = NOT_RUN, and now for an honest reason. Recorded while repairing:
  the deployed ranking key (closure_disruption) is demand-side and therefore
  seed-deterministic BY CONSTRUCTION, so v1's "between-case spread beats seed
  noise" ratio on it was a tautology; v2 verifies the invariant instead and
  uses the seed axis for health, closure integrity and an inertness check.`
- Blockers or risks: `Gate S is blocked by organization egress policy, not by
  code — re-verified 2026-08-14. build_candidates.py needs overpass-api.de;
  the proxy answers 403 to CONNECT for it and for geodata.scb.se.
  /root/.ccr/README.md requires reporting such denials rather than retrying, so
  no workaround was attempted. The same denial prevents raw day-level
  Norwegian volumes, which is exactly why Gate M cannot build blocked_date
  folds and is INCONCLUSIVE.`
- Suggested next action: `Run the repaired Gate S on a machine that already
  has sumo/calibrated*.rou.xml — per the review, --freeze-only completes there
  in about a second. Separately, build dataset v2 (station x date x hour x
  heading with raw counts and day_block_id) to make Gate M decidable; that
  needs Norwegian volume egress. Do NOT open Fas 2/3/4: neither gate is
  decided, so the four-quadrant table cannot be entered from either side.`
- Actor notes: `SUMO installed locally and resolved from
  SUMO_HOME=/usr/local/lib/python3.11/dist-packages/sumo. sumo/net.net.xml and
  sumo/direction_split.json were rebuilt from tracked inputs; both are
  gitignored intermediates and were not committed. No existing evidence file
  was edited, no policy activated and no runtime gate weakened. The v1
  outcome and v1 blocker are preserved unchanged and explicitly superseded.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
