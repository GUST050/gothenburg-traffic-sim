# Prompt to repair Step 2 instrumentation and implement the bounded experiment

Continue on `claude/exciting-rubin-1e6k5m` from
`fcedfc29aa3dd1cd760bb1cbc0302c41f0ee6dcb`. Read the Step 0-8 section in
`IMPROVEMENT_PLAN.md` and the current coordination blocks first.

## 1. Repair the instrumentation before using its measurements

An independent review found three remaining contract gaps. Write RED tests,
then fix them:

1. `replay()` still exposes `solver_cache_dir` publicly and only prevents
   overlap with source evidence. Rename it `_solver_cache_dir`. When supplied,
   resolve it and require `cache_root.parent == out.parent`, matching the
   profile layout `profile-root/solver-cache` beside `repeat-N`. Perform both
   ownership and source-overlap checks before creating output or cache paths.
2. `route_shape_inventory()` uses `departure_reconciliation.read_route_vehicles`,
   which rejects named `<vehicle route="...">` references although production
   `demand.structure` supports them. Resolve both inline routes and shared named
   route definitions. Report inline vehicle count, named-reference vehicle
   count and named definition count. Refuse unresolved and empty routes.
3. Report every instrumented function even when its call count is zero. Add a
   content-bound input identity containing `GEO_PATH`, geometry SHA-256,
   measured-sensor edge count and a deterministic SHA-256 of sorted measured
   sensor edge IDs. Calculate this diagnostically without warming or mutating
   `demand.structure._EDGE_GEOMETRY_CACHE`.

The exact local repair is available in
`validation/claude_step2_review_fix_20260913.patch` if the user supplies it.
After the repair, the focused set passes 159 tests and the wider related set
passes 504 tests with one unrelated urllib3/LibreSSL warning. Commit and push
the repair separately and report its SHA.

## 2. Use the local measurement to decide the experiment

The reviewed profiler was run three times in one process on the retained q50
evidence for 2027-06-25. Full evidence is in
`validation/passage_step2_measurement_20260913.json`.

Measured facts:

- 9,675 vehicles;
- 296 unique full edge tuples;
- 296 unique endpoint pairs;
- 32.686 vehicles per unique route;
- source and staged candidate use the same route cardinalities;
- all 9,675 routes are inline in this sample;
- hot structure-source median: 0.342577 s;
- hot structure-candidate median: 0.349164 s;
- combined observed scope: 0.691741 s per replay;
- hot source plus candidate: 108,094 `gravity_distance_km` calls and 20,220
  `route_od_distance_km` calls;
- geometry SHA-256:
  `92cbcf3895b3d1428d3713a0e919cf6679f00a8e5d00e7974182345d088a1dbf`;
- seven measured sensor edges, identity
  `030d9fa9573193cd53a426fabca1a07f096e8eccc48fb236eb080e16adc8019a`;
- solver-cache pattern `[false, true, true]` and every replay reproduced the
  saved selection exactly;
- selection, route and agent hashes still match the accepted Step-1 output.

This justifies a bounded Step-2 route-facts experiment. It does not justify a
global or monthly performance claim, and 0.691741 s is the complete observed
upper bound for this pair of structure phases.

## 3. Implement the Step-2 experiment without changing semantics

Implement a per-operation, immutable structure context in `demand/structure.py`.
Do not add an unbounded global route cache. Bind the context to the geometry
content and sorted measured-sensor identity.

For each unique full edge tuple calculate once:

- endpoint distance using the current formula and float operations;
- destination edge and its current near-sensor result;
- number of measured sensor passages, including revisits;
- last measured-sensor position and onward distance.

Retain one observation per vehicle in original XML order when constructing
counts, medians, proportions and per-quarter fields. Deduplicate calculations,
never vehicles or observations. Departure quarter and purpose aggregation must
be recomputed after staging. Cache endpoint-pair distances for agent/purpose
sidecars without changing their iteration or reduction order.

Parse inline and named routes through one shared parser. Reuse the immutable
pool facts when the same pool is compared with source and candidate inside one
`automatic_passage._refine` operation. Do not reuse candidate quarter or
purpose aggregates after departure shifts. Do not use mtime/size alone as a
content identity and do not share mutable arrays or dictionaries between jobs.

Keep the existing public `calibrated_structure_report(route_path, pool_path)`
contract. If a context must be supplied, expose only an internal helper or a
private keyword used by controlled production callers. Missing geometry,
missing routes, invalid named references and malformed sidecars must retain
their current fail-closed outcomes.

## 4. Required tests and acceptance

Add tests for:

- byte/value-identical complete report and flags with and without the context;
- inline and named route equivalence;
- loops and repeated visits to the same sensor;
- identical routes with different purposes and departure quarters;
- exact 1/5/10 km threshold behavior;
- changed geometry bytes and changed sensor identity causing no reuse;
- missing route or geometry remaining fail-closed;
- source and candidate mutable data not being shared;
- reduced geometry/distance calls proportional to unique routes rather than
  vehicles;
- pool route facts being computed once per operation;
- unchanged automatic-passage selection, solver request, routes and agents.

Run the structure tests found with
`rg -n 'calibrated_structure_report|under_1km' tests`, plus
`test_automatic_passage.py`, `test_build_sumo_demand.py` and the profiler tests.
Run `git diff --check`.

Do not claim the speed improvement from fixture timings. If the local evidence
is unavailable, commit and push the tested experimental implementation and give
one exact replay A/B command. Do not start SUMO, a monthly search, date warming,
catalog generation, qualification or adoption. The local machine will run the
counterbalanced replay before the implementation can become the default.
