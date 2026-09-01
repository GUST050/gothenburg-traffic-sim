# Find sensor-crossing routes that are also globally fastest

The strict sensor-route contract is RIGHT. The candidate generator's SEARCH is
what fails it. Measured 2026-09-01, read-only and reproducible.

## 1. The routes exist, in abundance

For each measured edge, the set of OD pairs whose UNRESTRICTED shortest path
crosses it, from only 40 sampled origin edges:

    2276  26355153_91615277_0    14894 pairs   372 dests/origin
    1076  30420757_30421744_0     9077 pairs   227
    134   26355153_96523321_0     8400 pairs   210
    133   26842525_26355153_0     7020 pairs   176
    1074  60790252_60790253_0     4986 pairs   125
    107S  1455801464_18241874_0   2545 pairs    64
    107N  60786979_3575001205_0    883 pairs    22

All seven are reachable from 40/40 origins. Sampling 400 pairs from sensor
133's catchment and running them through `sensor_route_contract.qualify_route`
gives 400/400 PASS, zero rejections.

With the REAL grounded pools from the last build (1 764 home edges, 2 603
activity edges), 135 740 home x activity pairs have sensor 133 on their
shortest path, and 1 741/1 764 home origins can reach it.

## 2. Why the build found none: the search asks the question backwards

`make demand` failed with:

    strict sensor routes: kept 373/9336, dropped 8963
      {'declared_via_not_on_shortest': 8899, 'no_legal_sensor_detour': 64}
    candidate sensor-incidence basis failed: no legal grounded single-sensor
      route exists for: 26842525_26355153_0

8 899 of 8 963 drops are one reason. The generator samples an OD pair from the
home/activity pools FIRST and then declares "go via sensor S"; the qualifier
then computes the shortest path for that pair and finds S is not on it. The
pool is therefore filtered against a criterion it was never built to satisfy.

The correct direction is to enumerate, per sensor, the OD pairs that already
have it on their shortest path, then sample from THAT set weighted by the
existing grounded population/POI weights. Routes are then qualified by
construction and no filtering stage is needed.

Cost: one Dijkstra per home-pool origin on the full edge graph, plus one
forward Dijkstra per sensor. A pair (o,d) qualifies iff

    dist_o[S] + dist_S[d] == dist_o[d]

Equivalently, every edge in the subtree rooted at S in o's shortest-path tree
is a valid destination, so ONE Dijkstra per origin yields the valid
destination set for ALL sensors at once.

## 3. A real bug in `grounded_sensor_basis_route` (build_candidates.py)

    allowed = set(full_graph) - (measured_set - {target})
    graph   = full_graph.subgraph(allowed).copy()      # other sensors removed
    ...
    prefix = nx.shortest_path(graph, origin, target, weight="weight")
    suffix = nx.shortest_path(graph, target, destination, weight="weight")

The route is built as prefix+suffix on the RESTRICTED graph, then has to pass a
criterion demanding it be shortest in the UNRESTRICTED graph. Those are two
different optimisation problems, so the test almost always fails. It is also
a constrained path by construction, because it is forced through `target`.

Fix: take the UNRESTRICTED shortest path for the pair and TEST it - does it
cross `target`, and only `target`. Do not route on a modified graph.

Proof the current failure is a bug, not geometry: for sensor 133, 12 449
grounded pairs have it on the shortest path, and exclusive routes verifiably
exist, e.g. `299246103_299246041_0 -> 26355153_165154328_0` (38 edges,
crossing 133 and no other measured edge).

Exclusivity is genuinely harder for 133 than for others because its edge ENDS
at node 26355153, which is exactly where 134 (`26355153_96523321_0`) and 2276
(`26355153_91615277_0`) begin, so many continuations hit a second sensor. That
makes exclusive routes rarer, not absent.

## 4. Sensor 1074 is a genuine exception, and must be handled separately

`60790252_60790253_0` (Valhallagatan V): 0 grounded home x activity pairs have
it on the shortest path across 150 home origins x 2 603 activity edges, while
4 986 pairs exist from arbitrary origins. Its natural traffic is therefore not
home-to-activity; it is through-traffic. Its basis route must be sought in the
E-I/I-E gate population rather than the home/activity product, or it will keep
reporting "no grounded route" for a correct reason.

## Task

Implement the inverted search so sensor-crossing candidates are generated
qualified-by-construction, fix `grounded_sensor_basis_route` to test the
unrestricted shortest path instead of routing on a restricted graph, and give
sensor 1074 a gate-population basis route. Keep the strict contract, the
grounded population/POI weighting and the existing tour structure. Add
regressions that pin: a sensor's catchment is non-empty for all seven measured
edges; a basis route is found for 133; the qualifier drops nothing it should
keep; and 1074's basis comes from the gate population.

Then run `make demand` and report kept/dropped, per-variant GEH<5, and the
tour_partner_dropped rate (the failed run showed 78.8% against a documented
13.9% baseline, which must not be accepted silently).

Do not weaken the strict contract to raise the yield. Do not delete, commit,
push or deploy.
