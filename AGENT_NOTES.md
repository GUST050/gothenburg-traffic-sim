# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/dirsplit-gated-plan-v2, based on main at
  340b628. Fas 0A, Fas 0B and Fas 1 are implemented and then REPAIRED against
  an external review that found the branch not mergeable. Gate M is now
  INCONCLUSIVE (was wrongly published as BASELINE); Gate S is NOT_RUN. Neither
  gate has an answer, so no exit is declared.`
- Summary: `Fas 0A binds sensor 107's published D-factor as an AGGREGATE with
  period, source, raw counts and a geometry-resolved bearing->edge mapping,
  and anchors it with a single logit offset so the declared period reproduces
  52/48 while the partner direction is derived as 1-s; the offset is weighted
  by that day's measured two-way volume and all three build_targets call sites
  now pass anchor_day, so the anchor reaches production rather than sitting in
  an unused helper. A non-2025 date is refused visibly instead of anchoring a
  2027 forecast to a 2025 annual aggregate. Fas 0B delivers the bounded
  matched-seed tool with frozen materiality thresholds and a fail-closed Gate
  S rule; each cell now writes its own ScenarioSpec pinned to one seed and one
  demand variant, the objective reads disruption.added_vehicle_hours, and the
  reducer compares failure, health, no-detour counts, viable set, ranking and
  winner. Its inputs still cannot be built here. Fas 1 runs the four-candidate
  tournament over leakage-free blocked folds; it CANNOT decide, because the
  tracked table has no dates and blocked_date is a required fold kind.`
- Files changed: `data_in/sensors.json (additive only);
  traffic_sim/intake/sensors.py; demand/intake.py; build_sumo_demand.py;
  dirsplit/evaluate.py (new, the one module Fas 1 allows);
  tools/measure_direction_decision_sensitivity.py (new);
  tests/test_sensor_107_directional_reference.py,
  tests/test_dirsplit_legacy_pin.py,
  tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_gate_m.py; data/dirsplit/gate_m_report.json;
  validation/dirsplit_gate_m_outcome_v1.json (marked WITHDRAWN);
  validation/dirsplit_gate_m_outcome_v2.json;
  validation/dirsplit_direction_sensitivity_blocker_v1.json.`
- Checks: `154 passed, 2 skipped across the four new test files. Existing
  suites unaffected: test_sensor_registry, test_demand_intake,
  test_build_sumo_demand, test_build_data. Registry diff is purely additive.`
- Decisions and evidence: `GATE M = INCONCLUSIVE. The frozen rule says a gate
  whose required fold kind cannot be built has not answered; blocked_date
  needs dates the tracked table does not carry, so the gate is unmeasured, not
  negative. The rule is now also enforced as written in two further respects:
  a win must hold under EVERY fold kind that ran, and a more complex candidate
  must beat the CURRENT INCUMBENT pairwise, not merely beat 50/50 next to it.
  WITHDRAWN: the previous "-31.6% / -39.2% deployed LightGBM" claim. It was
  measured on a different population (OSM oneway screen, 39 stations, 1,514
  rows) than the deployment screens (observed share band, 81 stations, 3,665
  rows), and the entrant is weighted toward the training centroid while the
  deployment aims its kernel at the Gothenburg sensor edges. The entrant is
  renamed lgbm_reimplementation and every candidate carries
  deployment_equivalent = false. Indicative, non-gate pooled numbers on the
  aligned population: shrunk +3.4%/+2.9%, beta-binomial +2.9%/+2.5%, lgbm
  -8.6%/-21.5%.
  GATE S = NOT_RUN. Also measured, on the real direction_split.json built here
  from the tracked model: q50 pairs sum to exactly 1.0000 while q10 sums to
  0.7030-0.9480 and q90 to 1.0520-1.2970 (mean -/+0.1220). At sensor 107 that
  means the q10 arm would ask the calibrator to hit Level-1 targets summing to
  76-93% of the measured two-way total. The repair belongs to Fas 2 and was
  not performed.`
- Blockers or risks: `Gate S is blocked by organization egress policy, not by
  code. build_candidates.py needs overpass-api.de; the proxy answers 403 to
  CONNECT for it and for geodata.scb.se, api.scb.se,
  trafikkdata-api.atlas.vegvesen.no and nominatim.openstreetmap.org.
  /root/.ccr/README.md requires reporting such denials rather than retrying, so
  no workaround was attempted. The same denial prevents raw day-level
  Norwegian volumes, so Fas 1 could not build blocked_date folds and its block
  unit is station x day-type rather than station x date.`
- Suggested next action: `Two independent unblocks are needed. Gate S: grant
  egress to overpass-api.de (and geodata.scb.se for a DeSO refresh) or supply
  a cached POI/candidate artifact, then run build_sumo_demand.py, freeze the
  Fas 0B registration and run it. Gate M: grant egress to
  trafikkdata-api.atlas.vegvesen.no and rebuild the training table with
  local_date retained, so blocked_date folds can exist at all. Two unmeasured
  gates are not Exit A; do not open Fas 2 on either.`
- Actor notes: `SUMO 1.27.1 was installed locally and TraCI resolved from
  SUMO_HOME=/usr/local/lib/python3.11/dist-packages/sumo. sumo/net.net.xml and
  sumo/direction_split.json were rebuilt from tracked inputs; both are
  gitignored intermediates and were not committed. No existing evidence was
  edited, no policy activated and no runtime gate weakened.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
