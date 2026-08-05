# Fault audit — pool generation, trip picker, closure optimization

**Date:** 2026-08-06 · **Author:** Claude (Opus 5) · **Status:** findings only.
Nothing here is implemented. Requested before starting the annual warm
population, on the grounds that warming a year of demand that carries a known
fault is 25 h spent baking it in.

**Method.** Three sources, kept separate and labelled per finding:
`[DOC]` carried from this repo's own reviews, `[NEW]` measured today,
`[LIT]` external practice with a citation. Where a documented finding is
restated, it is because today's evidence changed its status or its rank.

---

## 0. The headline

**The closure integrity gate makes any edge that trips depart from
uncloseable.** That single rule, not the teleport leak, is what killed v9 —
and it is why a large share of the map returns an error when clicked.

Everything else in this audit is smaller.

---

## Part 1 — Closure optimization

### C1 — [NEW, CRITICAL] Origin-bearing edges can never be certified

`truncate_stranded_vehicles` shortens a route that loses its detour, but
"only actually dropped if the closed edge is the very FIRST edge of the route
(nothing to truncate to — no partial trip is possible)"
(`run_scenario.py:1514`). `disqualification_reasons` then treats any
`dropped_unreachable` as a hard, zero-tolerance failure
(`traffic_sim/simulation/metrics.py:249`).

So: close an edge that N trips start on, and N vehicles are dropped, and the
schedule is disqualified — **always**, regardless of how well the closure
otherwise behaves.

Measured across the v9 campaign's 75 schedules:

| case | eligible | schedules with drops | max dropped | max truncated |
| --- | --- | --- | --- | --- |
| v9-control-tertiary-2 | **11/15** | **0** | 0 | 0 |
| v9-control-secondary-3 | 0/15 | 6 | 3 | 0 |
| v9-control-tertiary-1 | 0/15 | 12 | 24 | 17 |
| v9-discriminating-tertiary-a | 3/15 | 12 | 33 | 34 |
| v9-discriminating-tertiary-b | 0/15 | 15 | **85** | 0 |

The only case that produced a usable ranking is the only case with **zero**
unreachability. `discriminating-tertiary-b` drops up to 85 vehicles with zero
truncations — the signature of pure origin-drops.

Confirmed directly in SUMO's own warnings on a live closure run:

```
Warning: Vehicle 'pfe4059' is not allowed to depart on any lane of edge '<closed edge>'
Warning: Vehicle 'pfe4430' is not allowed to depart on any lane of edge '<closed edge>'
Warning: Vehicle 'pfe4562' is not allowed to depart on any lane of edge '<closed edge>'
```

**Why this is a fault and not correct modelling.** The simulation is right that
a car cannot start from a fully closed street. The fault is *classification*:
that is a real-world consequence of the closure, i.e. part of the answer,
while `disqualification_reasons` is a **simulation-integrity** check — the
same list that catches vehicles teleporting through a closure, which is a
simulator artifact. Putting a modelled outcome and a simulator artifact in
one zero-tolerance list means the tool refuses to answer precisely where the
answer is most interesting: busy streets, which have the most departures.

21 of the 61 disqualified v9 schedules failed on unreachability with **no**
teleports and **no** throughput at all. Fixing rerouter reach or teleport
policy cannot make those eligible.

**Fix direction:** count displaced origins as an *impact*, not an integrity
breach — they belong beside `vehicles_affected` and `vehicles_no_detour` in
the ranking, which already has a place for them. Keep teleports and
closed-edge throughput as hard failures; those really are artifacts.

### C2 — [NEW] The leak is a teleport outcome, not the teleport itself

Recorded in `validation/closure_leak_mechanism_v1.json`. My own
`CLOSURE_INTEGRITY_PLAN` §1.1 claimed leak and teleports were one event; the
measurement **revised** that:

- Across 75 schedules, throughput never occurs without teleports (0 of 35), so
  teleports are a *necessary* condition.
- But 5 schedules teleport with no throughput, so they are not the same event.
- A directly observed teleport was **refused** entry to the closed edge and
  landed past it, producing no entry at all:

```
Teleporting 'pfe4885' from edge '496248275_7532496160_0'   (before)
'pfe4885' is not allowed on source edge '<closed edge>'    (REFUSED)
'pfe4885' ends teleporting on edge '7532496129_...'        (after)
```

Closure permissions genuinely work. `<closingReroute disallow="all"/>` is
already written (`run_scenario.py:1431`), so the failure mode the
[SUMO Rerouter docs](https://sumo.dlr.de/docs/Simulation/Rerouter.html)
describe — that a `closingReroute` *without* `allow`/`disallow` lets vehicles
"simply continue on their old route and effectively ignore the closing" — does
not apply here. Refuted in writing so it is not re-investigated.

### C3 — [DOC+LIT] Rerouter reach is 400 m and has never been re-measured

`REROUTER_RADIUS_M = 400` (`run_scenario.py:1347`). A rerouter only re-plans a
vehicle that passes one of its own edges, so traffic committed from beyond
400 m keeps a route through the closure, queues, and teleports. `CLAUDE.md`
records the radius was chosen because global rerouters were a bottleneck —
a speed/correctness trade-off never re-measured, and the near-sensor band
moved the campaign onto exactly the corridors where it bites.

### C4 — [LIT] The ranking objective is sound, and unusually well chosen

`closure_ranking` ranks on added vehicle-hours, then added metres, then count,
with stranding as a disqualifier. That matches standard practice: agency work-
zone guidance quantifies closures as **cumulative delay over all affected
drivers** for economic loss
([Maryland Lane Closure Analysis Guidelines](https://www.roads.maryland.gov/OOTS/13LaneClosureGuidelinesrev1.pdf)),
and network-level roadworks scheduling optimises user cost against business
disruption
([Miralinaghi et al., CACAIE 2020](https://onlinelibrary.wiley.com/doi/abs/10.1111/mice.12518)).
Refusing to blend metres into hours without a value-of-time is the
conservative and defensible choice. **No fault found here.**

The replaced objective was the fault: `robust_time_loss` measured +0.050 s and
−0.100 s across arms on a free-flow network, i.e. noise.

---

## Part 2 — The picker (PFE)

### S3 — [DOC+LIT] 200 iterations, no convergence test — but not a naive loop

`IPF_MAX_ITERATIONS = 200` with `burn_in = max_iterations // 5` and the
remaining ~160 samples **averaged** (`traffic_sim/demand/pfe.py:419`). So this
is not "run 200 and take the last"; the averaging is deliberate, to stay
hard-feasible whether or not the prior settles.

Standard practice in Sinkhorn/IPF is a residual tolerance — stop when the l∞
norm of the marginal residual falls below a threshold, with a maximum-iteration
cap only as a backstop
([Rethinking Initialization of the Sinkhorn Algorithm](https://arxiv.org/pdf/2206.07630),
[domain decomposition for entropic OT](https://arxiv.org/pdf/2410.08859)).
This code has the cap and no tolerance.

**Assessment: the documented recommendation is right to be cautious.** An early
exit cannot simply be bolted on, because the averaging needs a *run* of
samples, not a converged point. The correct form is a stability test on the
level-1 residual that stops sampling while keeping a fixed sample count. The
convergence profile has still **not** been measured — `DEMAND_PIPELINE_REVIEW`
says so explicitly, and I did not measure it today either. It remains the one
speed item that should not be acted on by inspection.

### S2 — [DOC] 4,137 ceilings enforced per pass, 2 bind

Measured previously on quarter 32: 4,137 ceilings touched, **2** at the cap
(0.05%), 1,267,306 touch-list adds per pass, ~365 G float ops across 1,440
solves. Two exact remedies are named — static domination, or lazy constraints
(the classic cutting-plane approach). Neither is implemented. This is pure
waste, not a correctness fault.

### B2 — [DOC] A third of the ceilings are a constant

33.8% of ceiling slots collapse to the flat `max(5.0, 5·v)` floor where
`5·v < 5`, so they carry no assignment information at all. Combined with S2,
most of the constraint machinery is doing nothing.

### B1 — [DOC] "Parsimony" no longer prunes

Every route touching a measured edge, a prior, or an active constraint is
seeded active, so the parsimony objective has nothing left to prune. Worth
re-reading now that the baseline rule removed synthetic coverage traffic — the
premise that motivated parsimony may simply no longer hold.

---

## Part 3 — Pool generation

### P1 — [DOC, now worse] Roughly half the network can carry no calibrated traffic

Documented as 41% (2,923 of 7,125 edges in no candidate route). After the
sensor-crossing baseline rule this rose: **4,095 of 7,147 edges (57.3%)** have
no baseline traffic. This is the accepted, documented price of not inventing
traffic (`CLAUDE.md` baseline rule) — **not a defect** — but it interacts
badly with C1 above, and the map still paints a smooth `confidence` decay
across edges whose real status is "not simulated".

### P2 — [DOC] Per-sensor coverage is uneven by 3.3×

The weakest sensor has 4,130 candidates against the strongest's 13,873. That is
an *outcome* of how hard each sensor is to route through, not an intent. It
means the solver has 3.3× fewer route shapes with which to satisfy one
station's counts.

### P3 — [DOC] The assignment prior fits worse than a constant

R² = −5.148 against measured flows, while it drives gate-draw weights and
6,124 solver ceilings. Given S2 shows those ceilings do essentially nothing,
the prior's real influence is on the *pool* side, not the constraint side.
Worth knowing before tuning it — and it is in tension with the LOSO record,
which says the assignment prior does real work (recovery 0.09 → 0.15).

### P4/S4 — [DOC+LIT] The pool is 56% duplicate legs

46,958 legs dedupe to 20,868 distinct route×purpose variables. Beyond the file
size, [route-set literature](https://www.sciencedirect.com/science/article/abs/pii/S0968090X24003711)
flags that heavily overlapping path sets violate the Independence of Irrelevant
Alternatives property assumed by route-choice models — paths differing in a few
links get counted as independent alternatives. This pool is a *coverage* set by
design rather than a choice model, which weakens but does not remove the
concern, because the PFE's entropy term does treat variables as exchangeable.

### P5 — [LIT] The identifiability limit is structural, and correctly stated

OD estimation from link counts is underdetermined: unknowns vastly outnumber
independent equations, so many flow patterns satisfy the observations, and
entropy maximisation or a minimum-distance objective is what forces a unique
answer ([Path Flow Estimator entropy model, Springer](https://link.springer.com/article/10.1007/s11067-016-9327-9),
[decoupled GLS path flow estimator](https://www.sciencedirect.com/science/article/abs/pii/S0191261504001006)).
With 6 sensors and 7 measured directed edges, this project sits at the extreme
end of that. `CLAUDE.md` already says the true OD is not identifiable and the
exported matrix is "ONE plausible OD consistent with the counts". **That is
the correct claim and it is being made honestly** — recorded here so the
limitation is not mistaken for a fault.

---

## Part 4 — What must change before warming, and what must not

**Before warming (they change the demand or the answer):**

- Nothing in Parts 2 or 3 is a *correctness* blocker. S2/S3/B2 are speed and
  waste; P1/P2/P3/P5 are known, documented, and honestly labelled.

**Before the next held-out campaign (not before warming):**

- **C1.** It is the reason v9 failed and the reason the map errors on click.
  It touches `run_scenario.py`/`metrics.py`, both bound inputs, so it must
  land *before* a population run starts or wait until one finishes.
- **C3**, measured rather than swept blind, once C1 stops masking it.

**Explicitly not worth doing:**

- Continuous warm state — researched 2026-08-05, blocked upstream:
  `MSDevice_Tripinfo::saveState()` does not serialize `myMesoTimeLoss`, the
  member is private with no accessor, and tripinfo is emitted only at arrival
  or run end.
- Loosening the integrity gate to make numbers pass. C1's fix is to
  *reclassify* a modelled outcome, not to weaken a check on artifacts.

---

## Sources

Project documents: `DEMAND_PIPELINE_REVIEW_2026-08-04.md` (P1-P4, S1-S4,
B1-B2), `FULL_CODE_AUDIT_2026-07-12.md`, `WARMING_SPEED_REVIEW_2026-08-03.md`,
`CLAUDE.md`, `IMPROVEMENT_PLAN.md`.

External: [SUMO Rerouter](https://sumo.dlr.de/docs/Simulation/Rerouter.html) ·
[Why Vehicles are Teleporting](https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html) ·
[SUMO SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html) ·
[MSDevice_Tripinfo source](https://raw.githubusercontent.com/eclipse-sumo/sumo/main/src/microsim/devices/MSDevice_Tripinfo.cpp) ·
[Path Flow Estimator entropy model](https://link.springer.com/article/10.1007/s11067-016-9327-9) ·
[Decoupled GLS path flow estimator](https://www.sciencedirect.com/science/article/abs/pii/S0191261504001006) ·
[Joint OD-path-choice formulation](https://www.sciencedirect.com/science/article/abs/pii/S0968090X24003711) ·
[Sinkhorn initialization](https://arxiv.org/pdf/2206.07630) ·
[Entropic OT domain decomposition](https://arxiv.org/pdf/2410.08859) ·
[Maryland Lane Closure Guidelines](https://www.roads.maryland.gov/OOTS/13LaneClosureGuidelinesrev1.pdf) ·
[Network-level roadworks scheduling](https://onlinelibrary.wiley.com/doi/abs/10.1111/mice.12518)
