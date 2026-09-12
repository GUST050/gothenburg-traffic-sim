# June run: retrospective attribution and bounded read-only replay

Research only. No optimization plan or production change is approved by this
report. Run: `speed-monthly-june-20260912`; active elapsed 3758.937 s.

## Timeline reconstruction

Demand manifest created/finished timestamps cover 30 new builders totaling
3076 s (second-resolution timestamps). First starts 15:32:58 UTC, last finishes
16:25:35 UTC. Gaps between these intervals total 81 s.
Backend artifact mtime: 16:27:47 UTC; cost ledger 16:29:09; completed pilot
selection 16:30:49; result 16:35:37. Artifact mtimes are retrospective milestones,
not direct function timers: subsequent edits/copies would invalidate them.

Approximate disjoint accounting of the 62m40s wall-clock interval:

| Category | Seconds | Basis |
| --- | ---: | --- |
| Passage calibration | 2092.043 | phase timers, cross-checked with new archive sums |
| Other PFE/variants work | 672.457 | wrapper minus nested passage |
| Other work inside demand builders | 311.500 | builder timestamp sum minus wrapper |
| Backend work outside recorded builder intervals | 214 | timeline remainder to backend artifact |
| Backend artifact to cost ledger | 82 | milestone interval |
| Cost ledger to completed pilots | 100 | milestone interval |
| Completed pilots to final result | 288 | milestone interval |

Rounding explains the ~1 second difference from active_elapsed_s. This assigns
time to pipeline regions, not individual functions. In particular the 311.5 s
includes publication, validation reporting, copying and other builder work;
the 214 s includes gaps, subprocess startup, archive validation and runner setup.
Neither is measured pure I/O. The 100/288 s include orchestration and evidence
handling; neither is measured pure SUMO time. Worker-seconds are not wall time.

## Fresh replay measurements

Archive: `runs/demand-20260912-162423-45495c29-135a`. Resolve its exact three
day-library keys from day_library_diagnostics. Production inputs were read only.
Assembly outputs used a private TemporaryDirectory and were cleaned afterwards.

* validate_demand_archive: first unprofiled call 1.239 s; separate process's
  three calls 1.301, 0.342, 0.355 s. Profiled warm call 0.297 s: JSON decode
  0.204 s, 51 sha256_file calls 0.084 s cumulative. First call is not a proven
  disk-cold measurement; imports/lazy setup and OS cache state differ.
* assemble_window: q50 0.661 s, q10 0.652 s, q90 0.653 s; 1.966 s total.
  One envelope only; this is not a whole-run attribution or before/after test.
* calibrated_structure_report: three unprofiled calls 1.424, 1.374, 1.384 s.
  Separate cProfile call: 1.666 s and 4,533,264 calls. gravity_distance_km:
  229,444 calls / 0.545 s own time; JSON decode 0.189 s; XML parse 0.186 s.
  Two _route_structure_metrics calls (candidate and pool) consume 1.089 s
  cumulative; do not add this to their child times.
* This q50 archive has 51,640 vehicles but only 491 distinct edge sequences
  and 491 distinct endpoint pairs. Route-invariant geometry can potentially
  be evaluated per unique route/OD and then weighted by vehicle multiplicity.
  Departure-quarter and purpose-dependent statistics still require their
  full observations. Preserve current distance formula, edge traversal order,
  multiplicity, thresholds and quantile definitions.

## Conclusions and remaining uncertainty

The earlier 27.8-minute residual is now geographically localized in the
pipeline, but not fully function-profiled. It is not evidence of 27.8 minutes
of redundant I/O. Assembly and archive validation are real costs, but this
sample does not justify calling them the dominant remaining bottleneck.
Repeated route geometry has stronger direct evidence than merely replacing
the JSON/XML format. System-construction duplication remains a code finding,
not a measured independent speedup.

Historical time cannot be recovered exactly for functions without timers.
Before choosing final optimization priorities, remaining measurements require
an isolated instrumented builder replay and resolver/costing replay with the
same inputs and declared cache state. One heavy mixed envelope, one weekday,
and one all-hit envelope are sufficient initial samples; no month warmup is
needed. Freeze/check inputs and output hashes, preserve failure checks and
measure unprofiled wall time separately from profiler attribution.

Reference: https://docs.python.org/3/library/profile.html — cProfile measures
call attribution and adds overhead; its elapsed times are not speed benchmarks.
