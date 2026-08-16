# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/direction-split-plan-tt3gy9. The dated
  direction plan's UNCONDITIONAL phases (0A, 0B, 1) are implemented and tested.
  Gate S and Gate M are both still undecided, and no conditional branch, schema,
  monthly, warm-state, API or UI code exists.`
- Summary: `Fas 0A binds sensor 107's published 2025 D-factor as a
  provenance-carrying period aggregate and re-levels the ESTIMATED per-slot
  split at load time, so every consumer (level-1 targets, level-2 bounds,
  level-3 priors, assignment field, published report) sees one anchored profile.
  Measured: the transfer model put 107 at a flow-weighted 0.4981 for 2025; the
  city publishes 0.5231; the anchor applies delta +0.100 in log-odds, reproduces
  0.52308 exactly, moves any single quarter by at most 0.025 and leaves the
  time-of-day shape untouched (2025-09-16 08:00, two-way total 127: N target
  63.0 -> 66.2). Fas 0B adds a bounded matched-seed study that runs the full
  stress-case x seed cross product through the EXISTING run_condition/
  paired_comparison runners, with a committed preregistration and a
  deterministic Gate S rule; it fails closed to INCONCLUSIVE without a demand
  build. Fas 1 replaces the aggregated training table with a raw
  station-date-hour-heading table (counts, coverage, explicit missingness,
  day_block_id) and adds a four-model tournament with blocked folds and a
  bootstrap over independent groups.`
- Files changed: `data_in/sensors.json; traffic_sim/intake/sensors.py;
  traffic_sim/intake/direction_anchor.py (new); demand/intake.py;
  build_sumo_demand.py; traffic_sim/demand/source_identity.py;
  dirsplit/dataset.py; dirsplit/benchmark.py (new); dirsplit/coverage.py;
  tools/measure_direction_decision_sensitivity.py (new); Makefile;
  validation/direction_decision_sensitivity_registration_v1.json (new);
  validation/dirsplit_point_benchmark_v1.json (new);
  data/dirsplit/coverage_report.json (observability v2 added);
  tests/test_direction_anchor.py, tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_v2.py (new); plan/TASKS/AGENT_NOTES/IMPROVEMENT_PLAN docs.`
- Checks: `116 new tests pass (38 anchor, 33 sensitivity, 45 dirsplit v2), plus
  the existing dirsplit tests: 142 passed together. Full suite run twice in
  parallel worktrees, clean HEAD versus this change: 321 failed / 4,458 passed
  and 321 failed / 4,564 passed, with the two FAILED lists byte-identical — the
  failures are this sandbox lacking SUMO, not regressions (spot-checked: the
  monthly-demand failures are SumoRuntimeError "cannot locate SUMO" on both
  sides). The tournament was executed for real on the tracked aggregate; the
  anchor was measured against the real 2025 flows and the checked-in model.`
- Decisions and evidence: `q10/q90 are re-levelled by the SAME shift as q50, so
  the stress band keeps its width in log-odds instead of collapsing onto the
  anchor or pretending to new spread. Anchor weights come from the measured
  reference year regardless of the simulated source, mirroring
  STRUCTURAL_REFERENCE_DATE. On the aggregate, shrunk_dfactor (hour x day type,
  no street features) beats 50/50 by +4.5% leave-city-out with a bootstrap CI
  excluding zero, the deployed shrunk LightGBM manages +2.1% with a CI spanning
  zero, and the raw LightGBM is WORSE than 50/50 — but Gate M stays
  INCONCLUSIVE because the aggregate has no day blocks and no raw counts.`
- Blockers or risks: `Gate S needs a calibrated demand build plus SUMO, neither
  present in this sandbox. Gate M needs the raw Norwegian volumes; the open API
  is refused by this environment's proxy, not by the code. Nothing in the
  sensitivity tool or the benchmark may be promoted to release evidence.`
- Suggested next action: `Run the two frozen studies on a machine with SUMO and
  network access: make demand && make direction-sensitivity for Gate S, and
  make dirsplit-volumes && make dirsplit-dataset && make dirsplit-benchmark for
  Gate M. Then apply the plan's four-outcome table; only Gate S = YES may open
  Gren B/D.`
- Actor notes: `No release gate, calibration gate or frozen evidence was
  weakened; no q archive was rewritten; no external data was requested.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
