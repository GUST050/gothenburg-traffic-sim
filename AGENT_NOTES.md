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
- Checks: `The dirsplit set is 179 passing (anchor, sensitivity, dataset v2 and
  tournament, deployed central model, rewritten level-3 priors). Full suite on
  a clean worktree of HEAD versus the final state: 321 failed / 4,458 passed
  versus 321 failed / 4,599 passed, with IDENTICAL failure lists — no
  regression, and those 321 are this sandbox lacking SUMO. Every affected
  module imports cleanly after the deletions; `dirsplit.predict` and
  `prior_flows` were run for real and produce the deployed split and all five
  opposite-direction priors. The tournament was executed in two population
  configurations against the tracked aggregate.`
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
- Deployment change (2026-08-16, user-directed): `dirsplit/predict.py now
  writes the tournament winner by default — the hour x day-type D-factor pooled
  toward 0.5, no street features — importing benchmark.ShrunkDFactor so what
  ships is what was scored. Pairs are oriented from published geometry
  (verified to reproduce features.py radial_cos to 3 decimals), so the deployed
  path needs neither an OSM download nor model.pkl. q10/q90 are leave-city-out
  residual quantiles of the same model: wider and measured rather than narrow
  and unvalidated, which loosens the level-2 ceiling on unmeasured
  carriageways (1076 at 07:00: measured 50 admits ~136 instead of ~72) and
  widens Monte Carlo spread. Gate M is still INCONCLUSIVE under its frozen
  rule; the switch rests on leave-city-out and leave-station-out only.
  The superseded machinery was then DELETED, not left dormant: train.py,
  model.pkl, fetch_norway/api/match, estimate_directions.py, the rollback flag
  and their tests. prior_flows.py was rewritten to read the deployed split
  through demand.intake instead of re-running the retired model with its own
  re-orientation and shrinkage. The tracked training table stays: the deployed
  curve is refitted from it on every run, so deleting it would make the shipped
  numbers unreproducible. With the fetch client gone, Gate M is reachable only
  if raw volumes are supplied by hand; a zero-external-data project should take
  the plan's Exit A (50/50 + the 107 anchor) rather than freeze a curve whose
  source was deleted.`
- Superfluity check on q10/q90 (2026-08-16, requested): `They are TWO objects
  under one name, and only one of them was broken. As per-edge MARGINAL bounds
  they are load-bearing: measured on the tracked artifacts, the structural
  conservation ceiling on an unmeasured carriageway is 450-1057 veh/quarter
  (5-12x its measured twin) and for 1076's twin there is none at all, while the
  model ceiling sits at 1.3-2.7x (median 2.1x); and with no constraint the PFE's
  parsimony objective drives the edge to ZERO, which is a stronger claim than
  any band. Count-based OD estimation is underdetermined, so the choice is which
  prior, not whether — and the ladder already surrenders these bounds FIRST,
  before priors and long before any measured band widens. As DEMAND VARIANTS
  they were broken: each edge took its own marginal quantile, so the pair summed
  to 0.587-1.413 and the q10/q90 route files calibrated sensor 107 to 82.1% and
  117.9% of its measured day total. Fixed in demand/intake.py::scenario_shares
  by deriving the pair from one canonical edge and giving the other the
  complement; all three variants now reproduce the measured total exactly while
  the split moves, and write_counts publishes the same numbers. Deleting the
  variant axis entirely was NOT done: it touches 210 call sites in 12 production
  modules and 80 test files including frozen closure evidence, and it would
  answer Gate S by fiat instead of measuring it.`
- Shape-source question CLOSED on evidence (2026-08-17,
  `tools/measure_donor_shape_transfer.py`): `Standard practice offers two
  constructions and the project had tried one. FHWA's Traffic Monitoring Guide
  applies temporal factors from a GROUP of continuous counters to sites with
  only a bidirectional count — the deployed design. Project-level forecasting
  guidance instead borrows a NEARBY permanent counter's pattern, and Gothenburg
  has a candidate 239.9 m from 107 at 3.5 degrees. Measured on Norwegian
  stations where direction truth exists: only the widest band (11 independent
  pairs) reaches the frozen minimum of 8, and there the interval spans zero
  (+14.6%, CI [-0.0178, +0.0087]). The tempting +57.2% in the band matching
  Gothenburg rests on TWO independent pairs. Two traps had to be removed first,
  both now pinned by tests: an unoriented population collapses the pooled curve
  to a flat 0.5 because mirrored headings cancel, and reciprocal donor pairs
  count one piece of evidence twice. Verdict: the donor route is NOT deployed;
  the deployed construction stands because its one plausible upgrade cannot be
  shown better on the data this project owns. The group curve beats a flat
  anchor in every band, so shape is worth having — only its source is open.`
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
