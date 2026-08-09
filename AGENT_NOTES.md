# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Superseded warming removed; current bank initialized but
  unexecuted; presentation-ready sensor validation generated.`
- Summary: `Deleted 17 superseded annual banks and all annual pilot stores,
  freeing 19,171,217,408 allocated bytes while retaining logs/JSON evidence.
  Current plan 38d91d22… is the only initialized annual root. Warming remains at
  0/104685. Preflight now records three actual workers: one demand group exposes
  only q10/q50/q90 chains, so eight approved processes do not increase current
  scheduler throughput. Demand prefetch's measured ~18% gain remains enabled.
  A presentation-level aggregate-volume test now requires and includes all six
  sensors, including 1076; it passes at 3.9% total error versus a declared 10%
  project limit without changing the stricter per-sensor TAG diagnostic.`
- Files changed: `tools/validate_dmrb.py, tests/test_validate_dmrb.py,
  validation/sensor_validation_presentation_20260809.md,
  validation/annual_warm_cleanup_20260809.json, annual preflight, warming speed
  research and current coordination.`
- Checks: `24 TAG/aggregate tests pass. All-sensor aggregate is 25,761 measured
  versus 24,761 simulated (ratio 0.961, 3.9% error, PASS). Presentation replay
  gives 93/143 (65.0%) GEH<5,
  117/143 (81.8%) flow-band and 118/143 (82.5%) either criterion. Current SQLite
  progress: 104685 pending, zero running/succeeded/failed. Preflight passes with
  144,171,651,072 bytes free versus 59,877,867,520 required.`
- Decisions and evidence: `Calibration is explicitly separated from temporal
  holdout. Calibration-day raw SUMO GEH<5 is 100%, but holdout misses >85% TAG.
  Rank gain 1 is explained as underidentification, never as a score. Closure
  verified_clean and zero teleports/collisions/leaks are complementary health
  evidence, not substitutes for independent validation.`
- Compact evidence: `validation/sensor_validation_presentation_20260809.md,
  validation/aggregate_sensor_volume_test_20260809.json and
  validation/annual_warm_cleanup_20260809.json`
- Blockers or risks: `Absolute cross-sensor forecasts remain underidentified;
  sensor 1076 is weakest. A six-worker scheduler requires two active demand
  groups and whole-pipeline benchmarking because PFE already saturates the host.`
- Suggested next action: `If the user starts warming, run the current three-worker
  prefetch path and audit completed chains. Benchmark two active demand groups
  separately before adopting six workers.`
- Actor notes: `Preflight f31f89efae2aa05896ae4d47574aaeffc1a4cf522968dec02da70b20847b759f;
  current root 38d91d22… is 34 MiB metadata only. Removed states are not
  recoverable; historical JSON/log records remain.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
