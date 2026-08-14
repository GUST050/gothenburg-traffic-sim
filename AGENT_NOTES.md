# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/dirsplit-gated-plan-v2, based on main at
  340b628. Fas 0A, Fas 0B and Fas 1 of the gated plan are implemented. Gate M
  is decided = BASELINE. Gate S could not be run here. No exit declared.`
- Summary: `Fas 0A binds sensor 107's published D-factor as an AGGREGATE with
  period, source, raw counts and a geometry-resolved bearing->edge mapping,
  and anchors it with a single logit offset so the declared period reproduces
  52/48 while the partner direction is derived as 1-s. Fas 0B delivers the
  bounded matched-seed tool with frozen materiality thresholds and a
  fail-closed Gate S rule, but its inputs cannot be built here. Fas 1 runs the
  four-candidate tournament over leakage-free blocked folds and finds the
  deployed LightGBM significantly worse than 50/50.`
- Files changed: `data_in/sensors.json (additive only);
  traffic_sim/intake/sensors.py; demand/intake.py;
  dirsplit/evaluate.py (new, the one module Fas 1 allows);
  tools/measure_direction_decision_sensitivity.py (new);
  tests/test_sensor_107_directional_reference.py,
  tests/test_dirsplit_legacy_pin.py,
  tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_gate_m.py; data/dirsplit/gate_m_report.json;
  validation/dirsplit_gate_m_outcome_v1.json;
  validation/dirsplit_direction_sensitivity_blocker_v1.json.`
- Checks: `118 passed, 2 skipped across the four new test files. Existing
  suites unaffected: test_sensor_registry, test_demand_intake,
  test_build_sumo_demand, test_build_data -- 96 passed. Registry diff is purely
  additive.`
- Decisions and evidence: `GATE M = BASELINE. On 1,514 rows / 39 stations / 74
  blocks the deployed similarity_weighted_lgbm scores -31.6% (leave-city-out)
  and -39.2% (leave-station-out) against 50/50 with the paired block-bootstrap
  CI entirely above zero; shrunk_dfactor and beta_binomial_dfactor tie at
  about +4.5%. This agrees in sign with the tracked train_report.json.
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
- Suggested next action: `Grant egress to overpass-api.de (and geodata.scb.se
  for a DeSO refresh) or supply a cached POI/candidate artifact, then run
  build_sumo_demand.py, freeze the Fas 0B registration and run it. If Gate S
  returns NO, Exit A applies together with the decided Gate M = BASELINE. Do
  not open Fas 2 on Gate M alone.`
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
