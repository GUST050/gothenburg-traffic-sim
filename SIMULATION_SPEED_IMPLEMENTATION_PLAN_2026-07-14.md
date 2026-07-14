# Simulation Speed Implementation Plan

**Date:** 2026-07-14
**Status:** Implementation in progress (2026-07-14). The plan is the
acceptance contract; the implementation snapshot below records what has been
landed and what still requires measured adoption.
**Structural authority:** ARCHITECTURE.md. This plan must use the existing
runs/ registry and staged publication path. It must not create a parallel
release or cache system.

## Decision

The objective is lower wall-clock time and faster browser delivery without
changing traffic demand, route choice, uncertainty, seeds, model fidelity, or
publication safety.

There is no honest way to call an unmeasured change the universally "absolute
best" solution. This is the highest-confidence order given the measured
profile of this repository. It is deliberately built so that every speed claim
is proven or rejected by a fixed benchmark and exact semantic comparisons.

The normal product remains the citywide mesoscopic SUMO simulation. It is the
correct fast model for 15-minute flows, routing, and closure screening.
Microscopic signal, lane, queue, and roundabout analysis remains a bounded
corridor workflow. Replacing citywide meso with micro would be slower and is
not a performance improvement.

## What Must Never Change

Every non-research phase below must preserve:

1. The network, route files, closure definition, SUMO version, SUMO mode,
   seed numbers, direction variants, begin/end/flush settings, and rerouter
   settings.
2. PFE hard targets, 200 iterations, Gauss-Seidel update order, structural
   caps, and q10/q50/q90 uncertainty variants.
3. Per-seed flows, health telemetry, confidence inputs, closure integrity,
   final vehicle routes, exit times, and unfinished-vehicle semantics.
4. Sensor GEH, structural/purpose gates, LOSO evidence, trajectory
   reconciliation, and all health gates.
5. Atomic staged publication: failed, cancelled, incomplete, or unmeasured
   work must never replace the currently published scenario.
6. One machine-wide process budget. PFE work and a SUMO seed pool must not
   oversubscribe the host.

Do not make a run faster by using fewer seeds, fewer variants, fewer solver
iterations, weaker bounds, a smaller rerouter radius, a larger SUMO step,
larger mesoscopic segments, disabled junction control, or less trajectory
evidence.

## Measured Facts That Set The Order

| Fact | Design consequence |
| --- | --- |
| A normal single-day scenario runs seeds 1000, 1001, and 1002, then runs seed 1000 again only for trajectories. | The strongest immediate improvement is to capture the vehicle-route output during the existing seed-1000 run. |
| A cold import of run_scenario is about 10.4 seconds, dominated by build_sumo_net importing OSMnx, PyProj, and Matplotlib. | Move the tiny SUMO runtime helper out of the network builder and lazy-load network-only dependencies. |
| sumo/ contains about 2.4 GB of scratch output and several paths share filenames. | Private workspaces are mandatory before parallel SUMO processes or durable caches. |
| PFE takes about 500-535 seconds for a whole-day build; about 81% is the sequential Gauss-Seidel inner loop. It already uses a flat pool across all cores. | Do not change solver mathematics or blindly add workers. Consider only exact IPC reduction first, then a separately validated compiled-loop experiment. |
| Candidate generation is about 30 seconds. | Caching helps repeated calibrations, not a first run, and needs complete fingerprints. |
| SUMO documents that internal --threads does not offer meaningful simulation speedup. | Parallelize independent external SUMO jobs with a bounded process budget. [SUMO FAQ](https://sumo.dlr.de/userdoc/FAQ.html) |

## Phase 0 - Create The Benchmark And Semantic-Gate Harness

**Purpose:** No product behavior changes. This phase creates the evidence
required to accept later speed work.

### Implementation

1. Add a dedicated non-pytest command such as
   tools/benchmark_speed.py, modelled on tools/b0_two_continuous_days/run_b0.py.
   Timing must not run in normal pytest because hardware load makes it flaky.
2. Freeze these exact representative cases:
   - historical demand, 2025-09-16, 00:00-24:00, PFE, meso, seeds
     1000/1001/1002, trajectories enabled;
   - the same scenario with one known-detour closure,
     26842525_26355153_0;
   - one bounded micro smoke scenario with trajectories, to prove that no
     optimization accidentally makes playback meso-only.
3. Record hashes for the network, graph, flows, direction split, bounds,
   priors, candidate route files, demand metadata, closure XML, relevant source
   modules, and SUMO executable/version.
4. Record Python version, OS, CPU count, RAM, Git commit, dirty state, command
   line, wall time, CPU time where available, peak RSS, and output sizes.
5. Record stage timings separately: candidate generation, PFE preparation,
   interval solve, route publication, each SUMO seed, trajectory parse/export,
   aggregation, publish, browser download, and browser parse.
6. Generate canonical semantic digests:
   - PFE: route XML/agent records, GEH, infeasible count, relaxation rungs,
     bound violations, purpose mix, structure metrics, and OD metrics;
   - per seed: entered edge data, loaded/inserted/running/waiting/teleports,
     collisions, route errors, seed-to-variant mapping;
   - scenario: sorted flows, confidence, closure integrity, dropped/truncated
     totals, health records, and scenario metadata;
   - trajectories: inserted count, records in vehicle-route XML, represented
     count, unfinished/off-map counts, final routes, exit times, and rerouted
     routeDistribution count.
7. Normalize only non-semantic fields before comparison: generated timestamp,
   benchmark timestamp, absolute workspace path, run ID, and timing/RSS.
8. Run paired old -> new -> old -> new measurements on an idle machine:
   three complete trials for expensive scenarios and at least 20 fresh Python
   processes for cold import timing.
9. Keep a small checked-in manifest of semantic hashes. Keep large XML and JSON
   artifacts under immutable benchmark run directories, not in pytest fixtures.

### Acceptance Gate

All of the following are required:

- python3 -m pytest tests/ -q passes;
- the existing PFE benchmark fingerprint remains unchanged;
- canonical demand and scenario digests match exactly for result-preserving
  phases;
- every seed has health telemetry and no health gate fails;
- closure integrity and trajectory reconciliation remain identical;
- no orphan child process remains after success, failure, timeout, or cancel;
- the target path has a lower median wall time and no unrelated measured path
  regresses by more than 5%.

Do not proceed to parallelism or persistent caching until this harness exists.

## Phase 1 - Remove Heavyweight Runtime Imports

**Expected benefit:** Approximately 10 seconds less cold CLI/API startup. This
does not alter a SUMO command or traffic result.

### Current Cause

run_scenario.py, build_sumo_demand.py, build_candidates.py, and
validate_sim.py import sumo_home from build_sumo_net.py. That forces OSMnx and
PyProj to load even though only network construction needs them.

### Implementation

1. Add a tiny module, for example sumo_runtime.py, containing only sumo_home()
   and standard-library imports. Import the SUMO package inside sumo_home().
2. Move OSMnx and PyProj imports into build_sumo_net.main(), where graph loading
   and coordinate transformation actually happen.
3. Update runtime callers to import sumo_home from sumo_runtime.
4. Leave existing network parsing and speed/lane helpers in place unless a
   caller genuinely needs them. This must not become a broad network-builder
   refactor.
5. Add import/smoke tests for run_scenario, build_sumo_demand,
   signal_optimize, validate_sim, and the affected --help commands.

### Verification

- Measure 20 cold imports before/after for run_scenario, build_sumo_demand, and
  signal_optimize.
- Assert sumo_home resolves the same executable path.
- Run the full test suite, a real build_sumo_net invocation, and one frozen
  scenario semantic comparison.
- Require at least an 80% median startup reduction before describing this as a
  successful startup optimization.

## Phase 2 - Capture Trajectories During The Existing Seed-1000 Run

**Expected benefit:** Remove one complete SUMO invocation from the normal
single-day three-seed scenario. This is near 25% of serial SUMO work in that
path; the actual gain must be measured because vehroute output still costs I/O.

### Current Cause

run_scenario.py first runs seed 1000 for edge data and health. It then calls
export_trajectories(), which starts another SUMO process using the same seed,
route variant, closure inputs, mode, duration, and flush solely to obtain
vehicle routes.

### Implementation

1. Extend run_sumo() with an explicit vehroute-write-unfinished option.
2. Determine once whether trajectories are wanted. In the normal seed loop,
   request vehroute output only for seed 1000, alongside that seed's existing
   edge-data and statistics outputs.
3. Replace export_trajectories()'s SUMO invocation with a pure
   parse-and-publish function. It consumes the existing seed-1000 vehroute XML
   and seed-1000 health XML; it must never start SUMO.
4. Preserve all old simulation inputs exactly:
   - seed 1000 and the existing q50/variant selection;
   - baseline or closure additional files;
   - net path, route path, micro/meso mode, begin/end, and 3600-second flush;
   - ignore-route-errors, route reconciliation, and unfinished output.
5. Retain final-route parsing for both direct route entries and SUMO
   routeDistribution entries. Do not use vehroute-output.last-route in this
   phase because it discards useful reroute-history diagnostics.
6. Preserve the existing rule that multi-day scenarios do not silently create
   large trajectory artifacts unless trajectories are explicitly allowed.

### Required Tests

| Case | Must compare exactly |
| --- | --- |
| Baseline meso | seed flow arrays, health, final routes, exit times, trajectory counts, unfinished counts |
| Closure meso | all baseline fields plus closed-edge integrity and rerouted route distributions |
| Baseline micro | all trajectory fields and correct absence of mesosim |
| Closure micro | all above fields plus closure integrity |

Also add focused tests that only seed 1000 asks for vehroute output, all seeds
still write health output, and a normal three-seed scenario invokes SUMO three
times rather than four.

Do not delete the old separate execution path until baseline and closure
comparisons pass on real artifacts.

## Phase 3 - Complete Per-Job And Per-Seed Workspaces

**Expected benefit:** This is mainly a correctness and I/O-stability phase. It
is the required prerequisite for safe parallel execution, cleanup, and
content-addressed caching.

### Why It Must Come Before Parallelism

The current registry records runs, but several scenario, signal, and
closure-search paths still write shared filenames under sumo/. Concurrent jobs
could overwrite edge data, vehicle routes, statistics, or additional XML.
That can silently corrupt results.

### Implementation

1. Complete the existing PLAN.md E1 slice-2 direction. Use the existing run ID
   and publication model rather than create a second release system.
2. Give every external SUMO task an exclusive directory:

~~~text
runs/<run-id>/scratch/
  seed-1000/
  seed-1001/
  seed-1002/
~~~

3. Inputs such as net.xml and calibrated routes remain immutable/read-only and
   are passed by absolute path. All edge-data, statistics, vehroute, tripinfo,
   summary, and temporary additional XML belong only to the task directory.
4. Make run_sumo() receive an explicit working directory and explicit output
   paths. Remove dependence on cwd=sumo/ and shared inferred filenames.
5. Put a complete fingerprint in the manifest: network/demand signatures,
   date/profile hash, closure, SUMO version, commit, seed, direction variant,
   and simulation settings.
6. The parent process alone parses, sorts, aggregates, validates, and publishes
   results. Workers write no user-visible scenario JSON.
7. Delete only registered scratch files in finally after successful validation
   and atomic publication, unless --keep-scratch is given. Preserve failed or
   cancelled workspaces for diagnosis and mark them in the job record.
8. Track every child SUMO process so cancellation and orphan recovery cover all
   children, not only the Python parent.

### Verification

- Test two jobs with the same scenario name and different seeds; no path may
  overlap.
- Test one worker failure while another is writing: cancel siblings, publish
  nothing, retain diagnostics, and leave no orphan process.
- Test timeout and cancellation through the existing server process-group path.
- Test a failed run followed by a retry; stale files must not be read.
- Verify every resolved workspace path stays under the workspace root.
- Compare workspace-path and legacy-path semantic digests before retiring the
  legacy compatibility path.
- The serial workspace path may not be more than 5% slower than the current
  path before Phase 4 is enabled.

## Phase 4 - Bounded Parallel Execution Of Independent Seeds

**Expected benefit:** Lower scenario wall time, not reduced total compute. The
right worker count depends on RAM, disk, and CPU contention and must be
measured.

### Implementation

1. Keep one SUMO seed as the independent unit: fixed route variant, seed,
   workspace, and outputs.
2. Use a lightweight ThreadPoolExecutor or equivalent scheduler that launches
   external SUMO processes. Threads wait for subprocesses; do not add a nested
   Python multiprocessing pool.
3. Add an explicit --seed-workers limit. Keep default 1 initially. Benchmark
   1, 2, and 3 workers and adopt only the measured winner; never derive it
   blindly from os.cpu_count().
4. Maintain one machine-wide budget. A PFE worker pool must not overlap a SUMO
   seed pool, and the server-wide heavy-job lock remains active.
5. Gather futures by seed then sort by seed before aggregation. Concurrency
   must not alter confidence calculation, output order, or q10/q50/q90
   seed-to-variant assignment.
6. On any timeout, SUMO error, or health failure, cancel pending tasks, wait
   for active children, keep diagnostics, and do not publish.
7. Use the default worker cap of one for micro work until a separate micro
   benchmark proves a higher cap is safe on the host.

### Verification And Adoption

- Compare serial versus 1/2/3 workers using exact semantic digests for baseline
  and closure meso cases.
- Check aggregate process-group RSS, disk use, swap, health gates, timeout
  rate, and cancellation behavior as well as wall time.
- Test one seed, two seeds, three seeds, all three variants, and repeated
  variants where seed count exceeds variant count.
- Test failure and timeout of one seed: no partial aggregation, no publish, no
  orphan, and clean lock release.
- Adopt a worker count only if it improves median wall time by at least 15%
  and does not regress the micro smoke case by more than 5%.

Do not enable SUMO internal --threads as part of this work. Independent seeded
processes are the documented, reproducible parallelism unit.

## Phase 5 - Reuse The Proven Task Runner For Batch Studies

**Expected benefit:** Faster closure-time searches and signal experiment
batches. It does not change the normal interactive scenario model.

### Closure-Time Search

1. Reuse the Phase-4 task abstraction for independent candidate x seed jobs in
   suggest_closure_time.py.
2. Keep the shared baseline run read-only. Never recompute it for every
   candidate.
3. Pass the already computed rerouter_edges into each candidate simulation.
   The current search computes it once and then recomputes it inside each
   candidate.
4. Sort results by candidate and seed before ranking. The ranking calculation
   and tie-breaking must see exactly the same inputs as the serial version.

### Signal Optimization

1. Reuse the same isolated task abstraction for independent condition x seed
   jobs in signal_optimize.py.
2. Give every signal condition its own fingerprinted network/plan inputs and
   task workspace.
3. Preserve paired seeds across conditions so comparison statistics remain
   meaningful.
4. Keep fast citywide meso and bounded micro signal results distinct. This work
   schedules existing experiments faster; it does not make guessed plans more
   accurate.

### Gate

Serial and parallel batch reports must produce identical per-seed metrics,
rank inputs, ordering normalization, tie-breaking, and selected result.

## Phase 6 - Reuse Immutable Network Metadata And Cache Only Exact Inputs

### Phase 6A - Static Network Metadata

Build a versioned metadata index when build_sumo_net.py writes the network. It
contains a net hash plus immutable edge midpoint, free-flow time, and directed
successors. Closure geometry, reachability, and free-flow checks can then avoid
repeated net.net.xml/plain.edg.xml parsing.

Implementation rules:

1. One builder owns the index. Consumers reject it if its net hash differs.
2. Retain the XML parsing path temporarily as a test oracle.
3. Compare sorted metadata and closure results from both paths before switching
   callers.
4. Remove unused XML parsing only after the index is proven. This is a smaller
   win than Phases 1-4.

### Phase 6B - Candidate Template Cache

This cache is useful but must never reuse scientifically wrong demand.

Candidate geometry is not safely keyable only by weekday/weekend. The generator
samples the exact departure profile before purpose and OD selection. Therefore
the cache key must include the canonical exact normalized profile hash unless
the generator is first refactored and proven profile-independent.

The content key must include at least:

- graph/network and SUMO version hashes;
- exact departure-profile/day-block hash;
- generator source/configuration version, seed, candidate count, purpose/OD
  parameters, gravity parameters, and theta values;
- DeSO population/boundary, POI/activity, sensor metadata, direction split,
  assignment-prior, and BPR weight-file hashes;
- date/holiday/day-type settings whenever they affect sampling;
- relevant Python/OSMnx/NetworkX/SUMO versions.

Implementation rules:

1. Cache immutable templates only, never calibrated PFE routes or scenario
   results.
2. Store a manifest with every input hash and output hash.
3. Build cache misses in a temporary directory and atomically rename only after
   validation.
4. Treat an absent, malformed, partial, or mismatched manifest as a miss.
5. Lock per content key so concurrent recalibrations cannot corrupt an entry.
6. Record hit/miss and timing in the existing run manifest.

Verification:

- A cache hit must produce exactly the same candidate, calibrated routes,
  agents, reports, and scenario output as a cold build.
- Change one byte of each key input in separate tests; every mutation must
  force a cache miss.
- Test corrupt cache, interrupted write, concurrent builders, and a stale
  generator version.
- Measure cold and warm paths separately. The cold path may not regress by more
  than 5%; a warm hit must be materially faster and exact.

## Phase 7 - Faster Delivery And Browser Work Reduction

**Expected benefit:** Faster scenario loading and smoother playback. The
simulation values do not change.

1. Publish scenario, trajectory, and network JSON with content hashes named in
   the scenario index. The index stays no-cache; immutable content-hashed files
   become safely cacheable.
2. Serve gzip with Content-Encoding and Vary: Accept-Encoding, or publish
   precompressed artifacts. Preserve an uncompressed content digest in the
   manifest.
3. Start independent initial web-data fetches in parallel.
4. Keep vehicle canvas animation on requestAnimationFrame, but cache road style
   keys so unchanged displayed flow does not restyle approximately 7,000 Leaflet
   layers on every frame.
5. Update clock/date/sliders only when their displayed second, value, or play
   state changes.
6. Test initial load, scenario switch, stale-fetch cancellation, cache
   invalidation, and real-time playback. Parsed flow/trajectory data must match
   before/after.

## Phase 8 - PFE Work: One Modest Change, Then Research Only

### Phase 8A - Reduce Repeated Worker IPC

demand/calibration.py currently sends targets, bounds, and priors with every
variant x quarter task. Store immutable ordered jobs in fork-inherited worker
state and send only an integer job index or (variant, quarter). Workers read
the same immutable inputs and return the same solution/rung tuple.

Before merge:

1. Run the existing PFE semantic fixture.
2. Compare real fixed-day route XML, agents, hard targets, GEH, rungs,
   structural flags, purpose output, OD export, and LOSO evidence.
3. Profile serialization and solver time separately.
4. Keep the change only if full-day PFE median improves by at least 5%. It is a
   modest optimization, not a replacement for the dominant solver cost.
5. Keep one shared implementation path so production calibration and LOSO do
   not drift.

### Phase 8B - Compiled Exact Gauss-Seidel Loop

This is an R&D branch, not ordinary performance work. It is the only plausible
large PFE improvement, but it is accepted only if it preserves the exact
mathematics.

Requirements:

1. Preserve float64 arithmetic, edge order, sequential per-pass updates,
   200 iterations, burn-in averaging, convergence checks, and relaxation
   behavior.
2. Disable unsafe fast-math transformations.
3. Compile before workers are created and measure cold-start cost.
4. Pass the PFE fixture and full real-day comparison. Integer route counts
   should be identical; any floating difference needs a documented explanation
   and stricter acceptance review.
5. Measure full-day wall time and RSS, not only a synthetic microbenchmark.

Reject it if it changes a validation gate or does not materially improve the
real full-day build. Do not substitute NumPy/Jacobi vectorization, fewer
iterations, warm starts, or another solver merely because it is faster.

## Explicitly Excluded Changes

The following are not safe speed claims and are outside this plan:

- fewer Monte Carlo seeds or uncertainty variants;
- lower PFE iteration counts, weaker bounds, or changed Gauss-Seidel ordering;
- larger SUMO step or mesoscopic edge length;
- smaller rerouter radius or disabled limited junction control;
- citywide micro simulation or meso signal ranking;
- vehroute-output.last-route until final-route/exit-time equivalence and
  retained diagnostic provenance are proven;
- alternative routing algorithms unless seed-level routes/metrics prove
  equivalent;
- SUMO save/load checkpoints for micro queue/signal studies, because internal
  car-following and lane-change state is not fully preserved.

## Commit And Rollback Discipline

Implement exactly one phase per small commit.

For every commit:

1. Record the baseline benchmark ID before editing.
2. Add focused tests for the phase invariant.
3. Run the full test suite and relevant real benchmark cases.
4. Attach before/after semantic and timing reports to the run record or commit
   notes.
5. Reject or revert the isolated change if any accuracy, health, cancellation,
   robustness, or publication gate worsens. Never compensate by relaxing a
   gate.

## Required Implementation Order

1. Phase 0: benchmark and semantic comparison harness.
2. Phase 1: dependency-light SUMO runtime import.
3. Phase 2: one-pass seed-1000 trajectory capture.
4. Phase 3: per-job/per-seed workspaces and complete child-process recovery.
5. Phase 4: measured bounded normal-seed parallelism.
6. Phase 5: reuse the worker runner for closure-time and signal batches.
7. Phase 6A: static network metadata index.
8. Phase 6B: complete-fingerprint candidate template cache.
9. Phase 7: compressed/cacheable delivery and browser work reduction.
10. Phase 8A: PFE IPC reduction only if calibration latency still matters.
11. Phase 8B: compiled PFE loop only as a separately measured R&D effort.

This order produces the immediate, low-risk scenario speedup first, makes
parallelism safe before enabling it, ensures caches cannot silently alter
demand, and reserves solver experimentation for the point where its remaining
cost justifies the risk.

## Implementation Snapshot

The following plan slices are implemented in the current worktree and have
focused regression coverage:

- **Phase 1:** `sumo_runtime.py` keeps SUMO runtime imports lightweight;
  OSMnx/PyProj are lazy-loaded only by `build_sumo_net.py`.
- **Phase 2:** the normal seed-1000 scenario run captures vehroute output and
  publishes trajectories from that existing run. The old extra-run wrapper is
  retained only for diagnostic compatibility.
- **Phase 3:** scenario seeds run in `runs/<run-id>/scratch/seed-<seed>/`
  (or a disposable direct-caller workspace), with failure-preserving cleanup
  and explicit SUMO working directories.
- **Phase 4:** `run_scenario.py --seed-workers N` schedules independent seeds
  with bounded external-process concurrency. The default remains `1` until
  repeated benchmark trials justify changing it. Serial/parallel baseline and
  closure digests are currently identical on the checked real network.
- **Phase 5:** closure-time, `signal_lab.py`, and signal-condition batch
  runners now accept the same bounded seed-worker setting and use isolated
  per-condition/per-seed workspaces. Results are sorted by seed before
  aggregation; failed batches retain their diagnostic workspace.
- **Phase 6A:** `sumo_network_metadata.py` writes a hash-validated index from
  `build_sumo_net.py`; closure geometry, free-flow, and reachability queries
  use it only when it matches the network hash and otherwise use the XML
  oracle. SUMO internal junction edges are excluded from geometry/free-flow
  metadata.
- **Phase 7 (safe subset):** initial flow/profile/network fetches start in
  parallel; provider max-flow parsing is allocation-free; control DOM writes
  are memoized; renderer flow lookups cache the two current quarter buckets
  without changing interpolation or displayed values.
- **Phase 8A:** PFE variant/quarter workers inherit immutable input state and
  receive compact job tuples instead of copying target/bound/prior payloads.
  The PFE semantic fixture remains unchanged.
- **Phase 0:** `tools/benchmark_speed.py` and `make benchmark-speed` provide
  real baseline/closure/micro runs, input/version hashes, resource timings,
  semantic digests, and serial-vs-parallel gates. It is intentionally outside
  pytest.

The following are intentionally **not enabled or claimed as complete**:

- No worker count has been promoted from the conservative default of one;
  run three paired trials with `make benchmark-speed` and require the gates
  above before changing defaults.
- Phase 6B's complete-fingerprint candidate-template cache is not yet
  enabled. A cache that omits the exact departure profile or demand inputs
  would be scientifically unsafe, so the cold path remains authoritative.
- Compressed/content-hashed static delivery is not yet enabled; the browser
  changes above are result-neutral and do not hide stale artifacts.
- The compiled exact Gauss-Seidel experiment remains research-only and has
  not replaced the deployed solver.

## Sources

- ARCHITECTURE.md: fixed structure and contracts.
- PLAN.md E1, H2, and I: workspace direction, PFE semantic benchmark, and
  measured performance profile.
- SIMULATION_ACCURACY_SPEED_AND_ROBUSTNESS_PLAN_2026-07-14.md: release,
  health, trajectory, and acceptance-gate requirements.
- [SUMO FAQ](https://sumo.dlr.de/userdoc/FAQ.html)
- [SUMO mesoscopic model](https://sumo.dlr.de/docs/Simulation/Meso.html)
- [SUMO vehicle-route output](https://sumo.dlr.de/docs/Simulation/Output/VehRoutes.html)
